import pytest

from eagle.github import parse_pr_url

PR = "https://github.com/acme/widgets/pull/42"


@pytest.mark.parametrize(
    "url",
    [PR, PR + "/files", PR + "?w=1", PR + "#discussion", "  " + PR + "  "],
)
def test_parse_pr_url_valid(url):
    ref = parse_pr_url(url, ["github.com"])
    assert ref.full_name == "acme/widgets"
    assert ref.number == 42
    assert ref.canonical_url == PR


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widgets/pull/42",
        "https://github.com/acme/widgets/issues/42",
        "https://evil.example.com/acme/widgets/pull/42",
        "not a url",
    ],
)
def test_parse_pr_url_invalid(url):
    with pytest.raises(ValueError):
        parse_pr_url(url, ["github.com"])


async def test_member_submits_and_cancels(user_client):
    member = await user_client("alice", "member")
    resp = await member.post("/api/v1/reviews", json={"pr_url": PR + "/files", "agent": "codex"})
    assert resp.status_code == 201
    review = resp.json()
    assert review["status"] == "queued"
    assert review["repo"] == "acme/widgets"
    assert review["pr_url"] == PR
    assert review["agent"] == "codex"

    page = (await member.get("/api/v1/reviews")).json()
    assert page["total"] == 1

    resp = await member.post(f"/api/v1/reviews/{review['id']}/cancel")
    assert resp.json()["status"] == "cancelled"
    assert (await member.post(f"/api/v1/reviews/{review['id']}/cancel")).status_code == 409


async def test_viewer_can_read_but_not_submit(admin_client, user_client):
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    viewer = await user_client("victor", "viewer")
    assert (await viewer.post("/api/v1/reviews", json={"pr_url": PR})).status_code == 403
    assert (await viewer.get("/api/v1/reviews")).json()["total"] == 1


async def test_member_cannot_cancel_others_review(admin_client, user_client):
    review_id = (await admin_client.post("/api/v1/reviews", json={"pr_url": PR})).json()["id"]
    member = await user_client("bob", "member")
    assert (await member.post(f"/api/v1/reviews/{review_id}/cancel")).status_code == 403


async def test_custom_prompt(admin_client):
    context = "Focus on the migration in db/. Backward compat is not needed."
    resp = await admin_client.post(
        "/api/v1/reviews", json={"pr_url": PR, "custom_prompt": f"  {context}\n"}
    )
    assert resp.status_code == 201
    review_id = resp.json()["id"]
    detail = (await admin_client.get(f"/api/v1/reviews/{review_id}")).json()
    assert detail["custom_prompt"] == context

    # List view stays light and does not include the prompt.
    item = (await admin_client.get("/api/v1/reviews")).json()["items"][0]
    assert "custom_prompt" not in item


async def test_custom_prompt_blank_and_too_long(admin_client):
    resp = await admin_client.post("/api/v1/reviews", json={"pr_url": PR, "custom_prompt": "   "})
    assert resp.json()["custom_prompt"] is None

    resp = await admin_client.post(
        "/api/v1/reviews", json={"pr_url": PR, "custom_prompt": "x" * 8001}
    )
    assert resp.status_code == 422


async def test_invalid_pr_url(admin_client):
    resp = await admin_client.post("/api/v1/reviews", json={"pr_url": "https://gitlab.com/a/b"})
    assert resp.status_code == 422


async def test_filter_by_status(admin_client):
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    page = (await admin_client.get("/api/v1/reviews", params={"status": "running"})).json()
    assert page["total"] == 0
    page = (await admin_client.get("/api/v1/reviews", params={"status": "queued"})).json()
    assert page["total"] == 1
