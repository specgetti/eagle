from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, login


async def test_health(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).json() == {"status": "ready"}


async def test_requires_login(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/reviews")).status_code == 401


async def test_bootstrap_admin_login(client):
    await login(client, ADMIN_USER, ADMIN_PASSWORD)
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["username"] == ADMIN_USER
    assert me["role"] == "admin"


async def test_bad_credentials(client):
    for username in (ADMIN_USER, "nobody"):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": "wrong-password"}
        )
        assert resp.status_code == 401


async def test_logout(admin_client):
    assert (await admin_client.post("/api/v1/auth/logout")).status_code == 204
    assert (await admin_client.get("/api/v1/auth/me")).status_code == 401


async def test_admin_manages_users(admin_client):
    resp = await admin_client.post(
        "/api/v1/users",
        json={"username": "alice", "password": "alice-password-123", "role": "member"},
    )
    assert resp.status_code == 201
    alice_id = resp.json()["id"]

    dup = await admin_client.post(
        "/api/v1/users", json={"username": "alice", "password": "another-password-1"}
    )
    assert dup.status_code == 409

    resp = await admin_client.patch(f"/api/v1/users/{alice_id}", json={"role": "viewer"})
    assert resp.json()["role"] == "viewer"

    assert (await admin_client.delete(f"/api/v1/users/{alice_id}")).status_code == 204
    usernames = [u["username"] for u in (await admin_client.get("/api/v1/users")).json()]
    assert usernames == [ADMIN_USER]


async def test_short_password_rejected(admin_client):
    resp = await admin_client.post("/api/v1/users", json={"username": "bob", "password": "short"})
    assert resp.status_code == 422


async def test_non_admin_cannot_manage_users(user_client):
    member = await user_client("carol", "member")
    assert (await member.get("/api/v1/users")).status_code == 403
    resp = await member.post(
        "/api/v1/users", json={"username": "mallory", "password": "mallory-password-1"}
    )
    assert resp.status_code == 403


async def test_last_admin_is_protected(admin_client):
    me = (await admin_client.get("/api/v1/auth/me")).json()
    assert (await admin_client.delete(f"/api/v1/users/{me['id']}")).status_code == 409
    resp = await admin_client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"})
    assert resp.status_code == 409
    resp = await admin_client.patch(f"/api/v1/users/{me['id']}", json={"is_active": False})
    assert resp.status_code == 409


async def test_deactivated_user_loses_session(admin_client, user_client):
    dave = await user_client("dave", "member")
    dave_id = (await dave.get("/api/v1/auth/me")).json()["id"]
    await admin_client.patch(f"/api/v1/users/{dave_id}", json={"is_active": False})
    assert (await dave.get("/api/v1/auth/me")).status_code == 401


async def test_change_own_password(user_client, make_client):
    erin = await user_client("erin", "viewer")
    resp = await erin.post(
        "/api/v1/auth/password",
        json={"current_password": "erin-password-123", "new_password": "erin-new-password-1"},
    )
    assert resp.status_code == 204
    fresh = await make_client()
    await login(fresh, "erin", "erin-new-password-1")
