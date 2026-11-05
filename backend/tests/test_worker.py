"""Worker lifecycle tests with the mock executor and fake GitHub/Slack/SMTP."""

from sqlalchemy import select, update

from eagle.executors.base import RUNNING, Executor, RunState
from eagle.executors.mock import MockExecutor
from eagle.models import Review, ReviewStatus, TrackedPR
from eagle.services import apply_result
from eagle.worker import comment_body
from tests.conftest import PR
from tests.fakes import FakeSMTP


async def submit(client, **extra) -> dict:
    resp = await client.post("/api/v1/reviews", json={"pr_url": PR, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get(client, review_id: int) -> dict:
    return (await client.get(f"/api/v1/reviews/{review_id}")).json()


async def add_slack(client, events=None):
    resp = await client.post(
        "/api/v1/notifications",
        json={
            "name": "slack",
            "kind": "slack",
            "events": events or ["review.succeeded", "review.failed"],
            "config": {"webhook_url": "https://hooks.slack.com/services/T/B/x"},
        },
    )
    assert resp.status_code == 201


async def test_full_lifecycle_with_comment_and_notifications(
    admin_client, worker, mock_executor, fake_github
):
    await admin_client.patch("/api/v1/users/1", json={"email": "admin@example.com"})
    await add_slack(admin_client)
    await admin_client.post(
        "/api/v1/notifications",
        json={
            "name": "mail",
            "kind": "smtp",
            "config": {
                "host": "smtp.example.com",
                "from_addr": "eagle@example.com",
                "to_addrs": ["team@example.com"],
                "notify_submitter": True,
            },
        },
    )
    await admin_client.post(
        "/api/v1/git-hosts", json={"host": "github.com", "token": "ghp_worker_token"}
    )
    review = await submit(
        admin_client, post_comment=True, custom_prompt="Look at the widget math", model="m-1"
    )

    await worker.dispatch()
    running = await get(admin_client, review["id"])
    assert running["status"] == "running"
    assert running["title"] == "Add widget support"
    assert running["author"] == "octocat"
    assert running["head_sha"] == "a" * 40

    spec = mock_executor.launched[f"mock:{review['id']}"]
    assert "Look at the widget math" in spec.prompt
    assert spec.git_token == "ghp_worker_token"
    assert spec.model == "m-1"

    await worker.reap()
    done = await get(admin_client, review["id"])
    assert done["status"] == "succeeded"
    assert done["verdict"] is None  # mock result has no parsed verdict
    assert "Mock review" in done["result_markdown"]

    await worker.finalize()
    final = await get(admin_client, review["id"])
    assert final["posted_comment_url"].startswith("https://github.com/acme/widgets/pull/42")
    assert final["delivery_errors"] is None
    comment = fake_github.comments[0]
    assert comment["auth"] == "Bearer ghp_worker_token"
    assert comment["body"].startswith("## Eagle review")
    assert f"/reviews/{review['id']}" in comment["body"]

    assert len(fake_github.slack_messages) == 1
    assert "Review finished: acme/widgets#42" in fake_github.slack_messages[0]["text"]
    assert FakeSMTP.sent[0]["to"] == "team@example.com, admin@example.com"
    assert mock_executor.cleaned == [f"mock:{review['id']}"]

    # Finalize runs once per review.
    await worker.finalize()
    assert len(fake_github.slack_messages) == 1


async def test_no_comment_when_disabled(admin_client, worker, fake_github):
    await submit(admin_client)
    await worker.tick()
    await worker.tick()
    assert fake_github.comments == []


async def test_comment_failure_does_not_fail_review(admin_client, worker, fake_github):
    fake_github.fail_comments = True
    review = await submit(admin_client, post_comment=True)
    for _ in range(2):
        await worker.tick()
    final = await get(admin_client, review["id"])
    assert final["status"] == "succeeded"
    assert "PR comment" in final["delivery_errors"][0]


async def test_run_failure_notifies(admin_client, make_worker, fake_github):
    worker = make_worker(MockExecutor(fail_with="model quota exceeded"))
    await add_slack(admin_client, events=["review.failed"])
    review = await submit(admin_client)
    await worker.tick()
    await worker.tick()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert final["error"] == "model quota exceeded"
    assert "Review failed" in fake_github.slack_messages[0]["text"]


async def test_missing_pr_fails_review(admin_client, worker, fake_github):
    fake_github.pulls.clear()
    review = await submit(admin_client)
    await worker.dispatch()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert "404" in final["error"]


async def test_concurrency_limit(admin_client, make_worker):
    settings = (await admin_client.get("/api/v1/settings")).json()
    await admin_client.put("/api/v1/settings", json=settings | {"max_concurrent_reviews": 2})
    executor = MockExecutor(polls_until_done=100)
    worker = make_worker(executor)
    for _ in range(3):
        await submit(admin_client)
    await worker.dispatch()
    await worker.dispatch()
    statuses = [r["status"] for r in (await admin_client.get("/api/v1/reviews")).json()["items"]]
    assert sorted(statuses) == ["queued", "running", "running"]
    assert len(executor.launched) == 2


async def test_fifo_order(admin_client, make_worker):
    settings = (await admin_client.get("/api/v1/settings")).json()
    await admin_client.put("/api/v1/settings", json=settings | {"max_concurrent_reviews": 1})
    executor = MockExecutor(polls_until_done=100)
    worker = make_worker(executor)
    first = await submit(admin_client)
    await submit(admin_client)
    await worker.dispatch()
    assert list(executor.launched) == [f"mock:{first['id']}"]


async def test_timeout(admin_client, make_worker, clock):
    await admin_client.put("/api/v1/agents/claude", json={"timeout_seconds": 60})
    executor = MockExecutor(polls_until_done=100)
    worker = make_worker(executor)
    review = await submit(admin_client)
    await worker.dispatch()
    clock.advance(seconds=60 + 119)
    await worker.reap()
    assert (await get(admin_client, review["id"]))["status"] == "running"
    clock.advance(seconds=2)
    await worker.reap()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert "timed out" in final["error"]
    assert executor.cancelled == [f"mock:{review['id']}"]


async def test_cancel_running_review_stops_run(admin_client, make_worker):
    executor = MockExecutor(polls_until_done=100)
    worker = make_worker(executor)
    review = await submit(admin_client)
    await worker.dispatch()
    resp = await admin_client.post(f"/api/v1/reviews/{review['id']}/cancel")
    assert resp.json()["status"] == "cancelled"
    await worker.tick()
    assert executor.cancelled == [f"mock:{review['id']}"]
    assert (await get(admin_client, review["id"]))["status"] == "cancelled"


async def test_late_result_does_not_override_cancel(app, admin_client, make_worker):
    from eagle.services import apply_result

    worker = make_worker(MockExecutor(polls_until_done=100))
    review = await submit(admin_client)
    await worker.dispatch()
    await admin_client.post(f"/api/v1/reviews/{review['id']}/cancel")
    async with app.state.db.sessionmaker() as db:
        applied = await apply_result(db, review["id"], {"status": "succeeded", "markdown": "x"})
    assert applied is False
    assert (await get(admin_client, review["id"]))["status"] == "cancelled"


async def test_missing_credential_fails_with_clear_error(admin_client, make_worker):
    class RealishExecutor(MockExecutor):
        requires_credentials = True

    worker = make_worker(RealishExecutor())
    review = await submit(admin_client)
    await worker.dispatch()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert "No inference credential" in final["error"]


async def test_disabled_agent_after_queueing(admin_client, worker):
    review = await submit(admin_client, agent="codex")
    await admin_client.put("/api/v1/agents/codex", json={"enabled": False})
    await worker.dispatch()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert "disabled" in final["error"]


async def test_prompt_includes_agent_and_repo_instructions(admin_client, worker, mock_executor):
    await admin_client.put("/api/v1/agents/claude", json={"instructions": "TEAM-GUIDE"})
    await admin_client.post(
        "/api/v1/repo-settings", json={"pattern": "acme/*", "instructions": "ACME-GUIDE"}
    )
    review = await submit(admin_client)
    await worker.dispatch()
    prompt = mock_executor.launched[f"mock:{review['id']}"].prompt
    assert prompt.index("TEAM-GUIDE") < prompt.index("ACME-GUIDE")


async def test_rerun(admin_client, user_client, worker, mock_executor):
    original = await submit(admin_client, custom_prompt="ctx", model="m-2")
    resp = await admin_client.post(f"/api/v1/reviews/{original['id']}/rerun")
    assert resp.status_code == 409  # original still queued

    await worker.tick()
    await worker.tick()
    member = await user_client("rita", "member")
    resp = await member.post(f"/api/v1/reviews/{original['id']}/rerun")
    assert resp.status_code == 201
    rerun = resp.json()
    assert rerun["trigger"] == "rerun"
    assert rerun["previous_review_id"] == original["id"]
    assert rerun["custom_prompt"] == "ctx"
    assert rerun["model"] == "m-2"
    assert rerun["submitted_by"] == "rita"

    viewer = await user_client("val", "viewer")
    assert (await viewer.post(f"/api/v1/reviews/{original['id']}/rerun")).status_code == 403


async def test_tracking_updated_after_success(app, admin_client, worker):
    review = await submit(admin_client)
    await worker.tick()
    await worker.tick()
    async with app.state.db.sessionmaker() as db:
        tracked = await db.scalar(select(TrackedPR))
    assert tracked.last_reviewed_sha == "a" * 40
    assert tracked.latest_review_id == review["id"]


# Callback flow (Kubernetes-style executors)


class CallbackExecutor(Executor):
    uses_callback = True
    requires_credentials = False

    def __init__(self) -> None:
        self.callbacks: dict[str, dict] = {}
        self.done: set[str] = set()

    async def launch(self, spec, callback):
        ref = f"job-{spec.review_id}"
        self.callbacks[ref] = callback
        return ref

    async def status(self, ref):
        return RunState("finished") if ref in self.done else RUNNING

    async def cancel(self, ref):
        pass


async def test_runner_callback_flow(app, admin_client, client, make_worker, settings):
    executor = CallbackExecutor()
    worker = make_worker(executor)
    review = await submit(admin_client)
    await worker.dispatch()
    callback = executor.callbacks[f"job-{review['id']}"]
    assert callback["url"] == (
        f"{settings.callback_base_url}/api/v1/internal/reviews/{review['id']}/result"
    )
    path = f"/api/v1/internal/reviews/{review['id']}/result"
    result = {"status": "succeeded", "markdown": "## Summary\nGreat", "verdict": "approve"}

    # Wrong or missing token is rejected; no session needed for the right one.
    assert (await client.post(path, json=result)).status_code == 401
    bad = await client.post(path, json=result, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    ok = await client.post(
        path, json=result, headers={"Authorization": f"Bearer {callback['token']}"}
    )
    assert ok.status_code == 204
    final = await get(admin_client, review["id"])
    assert final["status"] == "succeeded"
    assert final["verdict"] == "approve"

    # Tokens are single use.
    again = await client.post(
        path, json=result, headers={"Authorization": f"Bearer {callback['token']}"}
    )
    assert again.status_code == 401

    async with app.state.db.sessionmaker() as db:
        row = await db.get(Review, review["id"])
        assert row.callback_token_hash is None


async def test_job_finished_without_callback_fails(admin_client, make_worker):
    executor = CallbackExecutor()
    worker = make_worker(executor)
    review = await submit(admin_client)
    await worker.dispatch()
    executor.done.add(f"job-{review['id']}")
    await worker.reap()
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert "without reporting" in final["error"]


async def test_callback_failed_result(admin_client, client, make_worker):
    executor = CallbackExecutor()
    worker = make_worker(executor)
    review = await submit(admin_client)
    await worker.dispatch()
    token = executor.callbacks[f"job-{review['id']}"]["token"]
    resp = await client.post(
        f"/api/v1/internal/reviews/{review['id']}/result",
        json={"status": "failed", "error": "Checkout failed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    final = await get(admin_client, review["id"])
    assert final["status"] == "failed"
    assert final["error"] == "Checkout failed"


async def test_two_workers_do_not_double_launch(admin_client, make_worker):
    a, b = MockExecutor(polls_until_done=100), MockExecutor(polls_until_done=100)
    worker_a, worker_b = make_worker(a), make_worker(b)
    await submit(admin_client)
    import asyncio

    await asyncio.gather(worker_a.dispatch(), worker_b.dispatch())
    assert len(a.launched) + len(b.launched) == 1


async def test_reported_model_is_stored_and_used_in_the_comment(
    app, admin_client, client, make_worker, settings, caplog
):
    await admin_client.put("/api/v1/agents/claude", json={"default_model": "glm-5"})
    executor = CallbackExecutor()
    worker = make_worker(executor)
    review = await submit(admin_client)
    with caplog.at_level("INFO", logger="eagle.worker"):
        await worker.dispatch()
    assert f"Started review {review['id']} (claude, model glm-5 (agent default))" in caplog.text

    callback = executor.callbacks[f"job-{review['id']}"]
    result = {
        "status": "succeeded",
        "markdown": "## Summary\nFine",
        "verdict": "approve",
        "model": "glm-5.3-fp8",
    }
    resp = await client.post(
        callback["url"].removeprefix(settings.callback_base_url),
        json=result,
        headers={"Authorization": f"Bearer {callback['token']}"},
    )
    assert resp.status_code == 204
    final = await get(admin_client, review["id"])
    assert final["model"] == "glm-5"
    assert final["model_source"] == "agent_default"
    assert final["reported_model"] == "glm-5.3-fp8"

    async with app.state.db.sessionmaker() as db:
        row = await db.get(Review, review["id"])
        assert "Reviewed by claude (glm-5.3-fp8)" in comment_body(row, settings)


async def test_failed_run_keeps_the_reported_model(app, admin_client):
    review = await submit(admin_client)
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            update(Review).where(Review.id == review["id"]).values(status=ReviewStatus.RUNNING)
        )
        await db.commit()
        applied = await apply_result(
            db, review["id"], {"status": "failed", "error": "boom", "model": "claude-x"}
        )
    assert applied
    final = await get(admin_client, review["id"])
    assert (final["status"], final["reported_model"]) == ("failed", "claude-x")


async def test_rerun_keeps_the_model_source(admin_client):
    await admin_client.put("/api/v1/agents/claude", json={"default_model": "glm-5"})
    review = await submit(admin_client)
    await admin_client.post(f"/api/v1/reviews/{review['id']}/cancel")
    rerun = await admin_client.post(f"/api/v1/reviews/{review['id']}/rerun")
    assert rerun.status_code == 201, rerun.text
    assert (rerun.json()["model"], rerun.json()["model_source"]) == ("glm-5", "agent_default")
