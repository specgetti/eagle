from sqlalchemy import select

from eagle.models import TrackedPR
from tests.conftest import PR


async def enable_poller(client, **overrides):
    settings = (await client.get("/api/v1/settings")).json()
    resp = await client.put(
        "/api/v1/settings",
        json=settings | {"poller_enabled": True, "poll_interval_minutes": 10} | overrides,
    )
    assert resp.status_code == 200


async def reviewed_pr(client, worker, **extra) -> dict:
    review = (await client.post("/api/v1/reviews", json={"pr_url": PR, **extra})).json()
    await worker.tick()
    await worker.tick()
    return review


async def reviews(client) -> list[dict]:
    return (await client.get("/api/v1/reviews")).json()["items"]


async def test_poller_disabled_by_default(admin_client, worker, fake_github, clock):
    await reviewed_pr(admin_client, worker)
    fake_github.add_pull(head_sha="b" * 40)
    fake_github.requests.clear()
    clock.advance(hours=5)
    await worker.poll()
    assert fake_github.requests == []
    assert len(await reviews(admin_client)) == 1


async def test_new_commit_triggers_incremental_review(admin_client, worker, fake_github, clock):
    original = await reviewed_pr(admin_client, worker, custom_prompt="ctx", model="m")
    await enable_poller(admin_client)

    # Same head: nothing to do.
    await worker.poll()
    assert len(await reviews(admin_client)) == 1

    fake_github.add_pull(head_sha="b" * 40)
    clock.advance(minutes=11)
    await worker.poll()
    items = await reviews(admin_client)
    assert len(items) == 2
    new = (await admin_client.get(f"/api/v1/reviews/{items[0]['id']}")).json()
    assert new["trigger"] == "poll"
    assert new["since_sha"] == "a" * 40
    assert new["previous_review_id"] == original["id"]
    assert new["custom_prompt"] == "ctx"
    assert new["model"] == "m"
    assert new["status"] == "queued"


async def test_poll_interval_respected(admin_client, worker, fake_github, clock):
    await reviewed_pr(admin_client, worker)
    await enable_poller(admin_client)
    await worker.poll()  # first poll records last_polled_at
    fake_github.add_pull(head_sha="b" * 40)
    clock.advance(minutes=5)
    await worker.poll()
    assert len(await reviews(admin_client)) == 1
    clock.advance(minutes=6)
    await worker.poll()
    assert len(await reviews(admin_client)) == 2


async def test_per_pr_interval_and_disable(admin_client, worker, fake_github, clock):
    await reviewed_pr(admin_client, worker)
    await enable_poller(admin_client)
    tracked = (await admin_client.get("/api/v1/tracked-prs")).json()[0]
    await admin_client.patch(f"/api/v1/tracked-prs/{tracked['id']}", json={"enabled": False})
    fake_github.add_pull(head_sha="b" * 40)
    clock.advance(hours=1)
    await worker.poll()
    assert len(await reviews(admin_client)) == 1

    await admin_client.patch(
        f"/api/v1/tracked-prs/{tracked['id']}", json={"enabled": True, "poll_interval_minutes": 1}
    )
    await worker.poll()
    assert len(await reviews(admin_client)) == 2


async def test_no_duplicate_while_review_active(admin_client, worker, fake_github, clock):
    await reviewed_pr(admin_client, worker)
    await enable_poller(admin_client)
    fake_github.add_pull(head_sha="b" * 40)
    clock.advance(minutes=11)
    await worker.poll()
    clock.advance(minutes=11)
    await worker.poll()
    assert len(await reviews(admin_client)) == 2


async def test_closed_pr_stops_tracking(app, admin_client, worker, fake_github, clock):
    await reviewed_pr(admin_client, worker)
    await enable_poller(admin_client)
    fake_github.add_pull(head_sha="b" * 40, state="closed")
    clock.advance(minutes=11)
    await worker.poll()
    assert len(await reviews(admin_client)) == 1
    async with app.state.db.sessionmaker() as db:
        assert (await db.scalar(select(TrackedPR))).closed is True
    assert (await admin_client.get("/api/v1/tracked-prs")).json() == []


async def test_failed_review_is_not_retried_by_poller(admin_client, worker, fake_github, clock):
    fake_github.pulls.clear()
    await reviewed_pr(admin_client, worker)  # fails: PR not found
    fake_github.add_pull(head_sha="b" * 40)
    await enable_poller(admin_client)
    clock.advance(minutes=11)
    await worker.poll()
    assert len(await reviews(admin_client)) == 1


async def test_poll_then_worker_reviews_new_commit(
    admin_client, worker, fake_github, clock, mock_executor
):
    await reviewed_pr(admin_client, worker)
    await enable_poller(admin_client)
    fake_github.add_pull(head_sha="c" * 40)
    clock.advance(minutes=11)
    await worker.tick()  # poll queues
    await worker.tick()  # dispatch + reap
    await worker.tick()  # finalize
    latest = (await reviews(admin_client))[0]
    assert latest["status"] == "succeeded"
    spec = mock_executor.launched[f"mock:{latest['id']}"]
    assert "git diff " + "a" * 40 + "..HEAD" in spec.prompt
