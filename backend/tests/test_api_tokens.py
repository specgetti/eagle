"""Personal API tokens: Bearer access for scripts, CI jobs and bots."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from eagle.models import ApiToken, utcnow
from eagle.security import hash_api_token
from tests.conftest import PR


async def _create(client, name="ci", **extra) -> dict:
    resp = await client.post("/api/v1/tokens", json={"name": name, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_list_and_use_a_token(admin_client, make_client, app):
    created = await _create(admin_client, "github-actions", expires_in_days=30)
    token = created["token"]
    assert token.startswith("eagle_") and len(token) > 40
    assert created["prefix"] == token[:12]
    assert created["username"] == "admin"
    assert created["expires_at"] is not None

    # Only the hash is stored.
    async with app.state.db.sessionmaker() as db:
        stored = await db.scalar(select(ApiToken))
        assert stored.token_hash == hash_api_token(token)
        assert token not in (stored.token_hash, stored.prefix)

    # Listing never returns the token again.
    listed = (await admin_client.get("/api/v1/tokens")).json()
    assert [t["name"] for t in listed] == ["github-actions"]
    assert "token" not in listed[0]
    assert listed[0]["last_used_at"] is None

    # A fresh client with no cookies can use it.
    bot = await make_client()
    me = await bot.get("/api/v1/auth/me", headers=_bearer(token))
    assert me.status_code == 200 and me.json()["username"] == "admin"
    assert (await admin_client.get("/api/v1/tokens")).json()[0]["last_used_at"] is not None


async def test_member_token_submits_a_review(user_client, make_client):
    alice = await user_client("alice", "member")
    token = (await _create(alice))["token"]
    bot = await make_client()
    resp = await bot.post("/api/v1/reviews", json={"pr_url": PR}, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    assert resp.json()["submitted_by"] == "alice"
    review = await bot.get(f"/api/v1/reviews/{resp.json()['id']}", headers=_bearer(token))
    assert review.status_code == 200


async def test_token_has_the_owners_role(user_client, make_client):
    viewer = await user_client("vic", "viewer")
    token = (await _create(viewer))["token"]
    bot = await make_client()
    assert (await bot.get("/api/v1/reviews", headers=_bearer(token))).status_code == 200
    resp = await bot.post("/api/v1/reviews", json={"pr_url": PR}, headers=_bearer(token))
    assert resp.status_code == 403
    assert (await bot.get("/api/v1/users", headers=_bearer(token))).status_code == 403


@pytest.mark.parametrize(
    "header",
    ["Bearer eagle_not-a-real-token", "Bearer something-else", "Basic dXNlcjpwYXNz", "Bearer "],
)
async def test_bad_tokens_are_rejected(client, header):
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": header})
    assert resp.status_code == 401


async def test_a_bad_token_is_not_rescued_by_a_session_cookie(admin_client):
    resp = await admin_client.get("/api/v1/auth/me", headers=_bearer("eagle_wrong"))
    assert resp.status_code == 401


async def test_expired_token_is_rejected(admin_client, make_client, app):
    token = (await _create(admin_client, expires_in_days=1))["token"]
    async with app.state.db.sessionmaker() as db:
        stored = await db.scalar(select(ApiToken))
        stored.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    bot = await make_client()
    resp = await bot.get("/api/v1/auth/me", headers=_bearer(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired API token"


async def test_revoked_token_stops_working(admin_client, make_client):
    created = await _create(admin_client)
    bot = await make_client()
    assert (await bot.get("/api/v1/auth/me", headers=_bearer(created["token"]))).status_code == 200
    assert (await admin_client.delete(f"/api/v1/tokens/{created['id']}")).status_code == 204
    assert (await bot.get("/api/v1/auth/me", headers=_bearer(created["token"]))).status_code == 401
    assert (await admin_client.get("/api/v1/tokens")).json() == []


async def test_deactivated_or_deleted_user_token_stops_working(
    admin_client, user_client, make_client
):
    bob = await user_client("bob", "member")
    token = (await _create(bob))["token"]
    users = {u["username"]: u["id"] for u in (await admin_client.get("/api/v1/users")).json()}
    bot = await make_client()

    await admin_client.patch(f"/api/v1/users/{users['bob']}", json={"is_active": False})
    assert (await bot.get("/api/v1/auth/me", headers=_bearer(token))).status_code == 401

    await admin_client.patch(f"/api/v1/users/{users['bob']}", json={"is_active": True})
    assert (await bot.get("/api/v1/auth/me", headers=_bearer(token))).status_code == 200

    assert (await admin_client.delete(f"/api/v1/users/{users['bob']}")).status_code == 204
    assert (await bot.get("/api/v1/auth/me", headers=_bearer(token))).status_code == 401
    assert (await admin_client.get("/api/v1/tokens", params={"all": True})).json() == []


async def test_tokens_cannot_manage_tokens_or_the_password(admin_client, make_client):
    token = (await _create(admin_client))["token"]
    bot = await make_client()
    for method, path, body in (
        ("POST", "/api/v1/tokens", {"name": "more"}),
        ("GET", "/api/v1/tokens", None),
        ("DELETE", "/api/v1/tokens/1", None),
        ("POST", "/api/v1/auth/password", {"current_password": "x", "new_password": "y" * 12}),
    ):
        resp = await bot.request(method, path, json=body, headers=_bearer(token))
        assert resp.status_code == 403, (method, path, resp.text)
        assert "browser session" in resp.json()["detail"]


async def test_users_see_only_their_own_tokens_admins_see_all(admin_client, user_client):
    alice = await user_client("alice", "member")
    carol = await user_client("carol", "member")
    alice_token = await _create(alice, "alice-ci")
    await _create(carol, "carol-ci")

    assert [t["name"] for t in (await alice.get("/api/v1/tokens")).json()] == ["alice-ci"]
    assert (await alice.get("/api/v1/tokens", params={"all": True})).status_code == 403
    # Someone else's token looks missing to a non admin.
    assert (await carol.delete(f"/api/v1/tokens/{alice_token['id']}")).status_code == 404

    everyone = (await admin_client.get("/api/v1/tokens", params={"all": True})).json()
    assert {(t["username"], t["name"]) for t in everyone} == {
        ("alice", "alice-ci"),
        ("carol", "carol-ci"),
    }
    assert (await admin_client.delete(f"/api/v1/tokens/{alice_token['id']}")).status_code == 204
    assert (await alice.get("/api/v1/tokens")).json() == []


async def test_token_actions_are_audited(admin_client):
    created = await _create(admin_client, "audited")
    await admin_client.delete(f"/api/v1/tokens/{created['id']}")
    entries = (await admin_client.get("/api/v1/audit")).json()["items"]
    actions = {e["action"]: e for e in entries}
    assert actions["token.created"]["details"]["name"] == "audited"
    assert actions["token.revoked"]["details"]["prefix"] == created["prefix"]
    assert created["token"] not in str(entries)


@pytest.mark.parametrize(
    "body",
    [{"name": ""}, {"name": "   "}, {"name": "x" * 65}, {"name": "ok", "expires_in_days": 0}],
)
async def test_create_validation(admin_client, body):
    assert (await admin_client.post("/api/v1/tokens", json=body)).status_code == 422


async def test_token_limit(admin_client, monkeypatch):
    monkeypatch.setattr("eagle.api.tokens.MAX_TOKENS_PER_USER", 2)
    await _create(admin_client, "a")
    await _create(admin_client, "b")
    resp = await admin_client.post("/api/v1/tokens", json={"name": "c"})
    assert resp.status_code == 409


async def test_openapi_declares_the_bearer_scheme_for_swagger(client):
    schema = (await client.get("/openapi.json")).json()
    assert schema["components"]["securitySchemes"]["API token"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "A personal API token from Account > API tokens (eagle_...).",
    }
    submit = schema["paths"]["/api/v1/reviews"]["post"]
    assert {"API token": []} in submit["security"]
    assert (await client.get("/docs")).status_code == 200
