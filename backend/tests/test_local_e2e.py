"""End to end through the real worker, LocalExecutor, runner subprocess and git,
with fake agent CLIs instead of model endpoints."""

import asyncio
import json

import pytest

from eagle.executors.local import LocalExecutor
from tests.conftest import PR
from tests.fakes import FAKE_CLAUDE, make_fake_cli


@pytest.fixture
def agent_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    make_fake_cli(bin_dir, "claude", FAKE_CLAUDE)
    make_fake_cli(
        bin_dir,
        "opencode",
        """
        import os, sys
        assert os.environ["EAGLE_PROVIDER_API_KEY"] == "maas-key-123"
        print("\\x1b[1m## Summary\\x1b[0m\\nOpenCode via MaaS on " + sys.argv[3] + "\\n\\nVERDICT: comment")
        """,
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    return bin_dir


async def wait_for_status(admin_client, worker, review_id, statuses=("succeeded", "failed")):
    for _ in range(200):
        await worker.tick()
        review = (await admin_client.get(f"/api/v1/reviews/{review_id}")).json()
        if review["status"] in statuses and review.get("delivery_errors") is None:
            await worker.tick()  # finalize
            return (await admin_client.get(f"/api/v1/reviews/{review_id}")).json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"review stuck in {review['status']}")


async def test_local_executor_claude_api_key(
    admin_client,
    make_worker,
    fake_github,
    pr_repo,
    agent_path,
    tmp_path,
):
    fake_github.add_pull(head_sha=pr_repo["head"], clone_url=pr_repo["clone_url"])
    cred = (
        await admin_client.post(
            "/api/v1/credentials",
            json={
                "name": "anthropic",
                "kind": "anthropic_api_key",
                "values": {"api_key": "sk-test-key-123"},
            },
        )
    ).json()
    await admin_client.put(
        "/api/v1/agents/claude",
        json={"credential_id": cred["id"], "default_model": "claude-sonnet-5-5"},
    )
    review = (
        await admin_client.post("/api/v1/reviews", json={"pr_url": PR.replace("42", "7")})
    ).json()
    fake_github.add_pull(number=7, head_sha=pr_repo["head"], clone_url=pr_repo["clone_url"])

    worker = make_worker(LocalExecutor(workdir=str(tmp_path / "runs")))
    final = await wait_for_status(admin_client, worker, review["id"])
    assert final["status"] == "succeeded", final["error"]
    assert final["verdict"] == "approve"
    assert final["cost_usd"] == 0.01
    assert final["head_sha"] == pr_repo["head"]
    assert "return a / b if b else 0" in final["result_markdown"]
    assert not list((tmp_path / "runs").iterdir())  # cleaned up


async def test_local_executor_opencode_maas(
    admin_client,
    make_worker,
    fake_github,
    pr_repo,
    agent_path,
    tmp_path,
):
    fake_github.add_pull(number=7, head_sha=pr_repo["head"], clone_url=pr_repo["clone_url"])
    cred = (
        await admin_client.post(
            "/api/v1/credentials",
            json={
                "name": "maas",
                "kind": "openai_compatible",
                "values": {"api_key": "maas-key-123", "base_url": "https://maas.example.com/v1"},
            },
        )
    ).json()
    await admin_client.put(
        "/api/v1/agents/opencode", json={"credential_id": cred["id"], "default_model": "granite"}
    )
    review = (
        await admin_client.post(
            "/api/v1/reviews", json={"pr_url": PR.replace("42", "7"), "agent": "opencode"}
        )
    ).json()
    worker = make_worker(LocalExecutor(workdir=str(tmp_path / "runs")))
    final = await wait_for_status(admin_client, worker, review["id"])
    assert final["status"] == "succeeded", final["error"]
    assert "OpenCode via MaaS on eagle/granite" in final["result_markdown"]
    assert final["verdict"] == "comment"


async def test_local_executor_agent_failure(
    admin_client,
    make_worker,
    fake_github,
    pr_repo,
    tmp_path,
    monkeypatch,
):
    bin_dir = tmp_path / "bin2"
    make_fake_cli(
        bin_dir, "claude", "import sys\nprint('rate limited', file=sys.stderr)\nsys.exit(1)\n"
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    fake_github.add_pull(number=7, head_sha=pr_repo["head"], clone_url=pr_repo["clone_url"])
    cred = (
        await admin_client.post(
            "/api/v1/credentials",
            json={"name": "a", "kind": "anthropic_api_key", "values": {"api_key": "sk-abcdef"}},
        )
    ).json()
    await admin_client.put("/api/v1/agents/claude", json={"credential_id": cred["id"]})
    review = (
        await admin_client.post("/api/v1/reviews", json={"pr_url": PR.replace("42", "7")})
    ).json()
    worker = make_worker(LocalExecutor(workdir=str(tmp_path / "runs")))
    final = await wait_for_status(admin_client, worker, review["id"])
    assert final["status"] == "failed"
    assert "rate limited" in final["error"]


async def test_local_executor_cancel_kills_process(tmp_path, monkeypatch):
    from eagle.executors.base import RunSpec

    executor = LocalExecutor(workdir=str(tmp_path / "runs"))
    spec = RunSpec(
        review_id=1,
        clone_url=f"file://{tmp_path}/none.git",
        pr_number=1,
        base_ref=None,
        head_sha=None,
        git_token=None,
        invocation={"argv": ["sleep", "30"]},
        timeout_seconds=60,
    )
    ref = await executor.launch(spec, None)
    await executor.cancel(ref)
    state = await executor.status(ref)
    assert state.state == "failed"  # forgotten after cancel
    assert not list((tmp_path / "runs").iterdir())
    json.dumps(state.__dict__)  # serializable
