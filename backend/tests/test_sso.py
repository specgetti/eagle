"""OIDC SSO against a fake identity provider that signs real RS256 ID tokens."""

import json
import time
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from eagle.config import Settings
from eagle.main import create_app
from eagle.models import User
from eagle.oidc import OIDCClient, role_from_claims
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER

ISSUER = "https://sso.example.com/realms/eng"
CLIENT_ID = "eagle"


class FakeIdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict = {}
        self.nonces: dict[str, str] = {}  # code -> nonce
        self.token_requests: list[dict] = []
        self.override_token: str | None = None

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [jwk | {"kid": "k1", "use": "sig", "alg": "RS256"}]}

    def id_token(self, real_nonce: str, **overrides) -> str:
        now = int(time.time())
        claims = (
            {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "sub": "user-123",
                "iat": now,
                "exp": now + 300,
                "nonce": real_nonce,
                "preferred_username": "jdoe",
                "email": "jdoe@example.com",
                "groups": ["/eng/eagle-admins"],
            }
            | self.claims
            | overrides
        )
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                },
            )
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            code = form["code"][0]
            if code not in self.nonces:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = self.override_token or self.id_token(self.nonces[code])
            return httpx.Response(200, json={"id_token": token, "access_token": "at"})
        return httpx.Response(404)


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def sso_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'sso.db'}",
        secret_key="test-secret",
        bootstrap_admin_password=ADMIN_PASSWORD,
        public_url="https://eagle.example.com",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="client-secret",
        oidc_admin_groups=["eagle-admins"],
        oidc_member_groups=["eagle-members"],
        oidc_default_role="none",
    )


@pytest.fixture
async def sso_client(sso_settings, idp) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(sso_settings)
    app.state.oidc = OIDCClient(sso_settings, transport=httpx.MockTransport(idp.handler))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app
            yield client


async def sso_login(client: httpx.AsyncClient, idp: FakeIdP, code: str = "code-1"):
    start = await client.get("/api/v1/auth/oidc/login")
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.nonces[code] = query["nonce"][0]
    return await client.get(
        "/api/v1/auth/oidc/callback", params={"code": code, "state": query["state"][0]}
    ), query


async def test_auth_config(sso_client):
    config = (await sso_client.get("/api/v1/auth/config")).json()
    assert config == {"local_login": True, "oidc": {"enabled": True, "label": "Log in with SSO"}}


async def test_login_redirect_uses_pkce(sso_client):
    resp = await sso_client.get("/api/v1/auth/oidc/login")
    location = urlparse(resp.headers["location"])
    query = parse_qs(location.query)
    assert location.path.endswith("/protocol/openid-connect/auth")
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["https://eagle.example.com/api/v1/auth/oidc/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid email profile"]


async def test_sso_creates_admin_from_group(sso_client, idp):
    resp, _ = await sso_login(sso_client, idp)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    me = (await sso_client.get("/api/v1/auth/me")).json()
    assert me["username"] == "jdoe"
    assert me["role"] == "admin"
    assert me["auth_source"] == "oidc"
    assert me["email"] == "jdoe@example.com"
    form = idp.token_requests[0]
    assert form["client_secret"] == ["client-secret"]
    assert "code_verifier" in form


async def test_role_resynced_on_each_login(sso_client, idp):
    await sso_login(sso_client, idp)
    idp.claims = {"groups": ["eagle-members"]}
    await sso_client.post("/api/v1/auth/logout")
    await sso_login(sso_client, idp, code="code-2")
    assert (await sso_client.get("/api/v1/auth/me")).json()["role"] == "member"


async def test_user_outside_groups_denied(sso_client, idp):
    idp.claims = {"groups": ["other"]}
    resp, _ = await sso_login(sso_client, idp)
    assert resp.headers["location"].startswith("/login?error=")
    assert "not%20in%20a%20group" in resp.headers["location"]
    assert (await sso_client.get("/api/v1/auth/me")).status_code == 401


async def test_state_mismatch_rejected(sso_client, idp):
    start = await sso_client.get("/api/v1/auth/oidc/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.nonces["c"] = query["nonce"][0]
    resp = await sso_client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": "forged"}
    )
    assert "expired" in resp.headers["location"]
    assert (await sso_client.get("/api/v1/auth/me")).status_code == 401


async def test_callback_without_login_rejected(sso_client):
    resp = await sso_client.get("/api/v1/auth/oidc/callback", params={"code": "x", "state": "y"})
    assert resp.headers["location"].startswith("/login?error=")


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 3600},
        {"nonce": "replayed"},
    ],
)
async def test_invalid_id_tokens_rejected(sso_client, idp, bad):
    start = await sso_client.get("/api/v1/auth/oidc/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.nonces["c"] = query["nonce"][0]
    idp.override_token = idp.id_token(query["nonce"][0], **bad)
    resp = await sso_client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": query["state"][0]}
    )
    assert "Could%20not%20verify" in resp.headers["location"]
    assert (await sso_client.get("/api/v1/auth/me")).status_code == 401


async def test_token_signed_by_other_key_rejected(sso_client, idp):
    other = FakeIdP()
    start = await sso_client.get("/api/v1/auth/oidc/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.nonces["c"] = query["nonce"][0]
    idp.override_token = other.id_token(query["nonce"][0])
    resp = await sso_client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": query["state"][0]}
    )
    assert "Could%20not%20verify" in resp.headers["location"]


async def test_sso_cannot_take_over_local_account(sso_client, idp):
    idp.claims = {"preferred_username": ADMIN_USER}
    resp, _ = await sso_login(sso_client, idp)
    assert "local%20account" in resp.headers["location"]
    assert (await sso_client.get("/api/v1/auth/me")).status_code == 401


async def test_sso_user_cannot_use_password_login(sso_client, idp):
    await sso_login(sso_client, idp)
    async with sso_client.app.state.db.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == "jdoe"))
        assert user.password_hash == "!oidc"
    await sso_client.post("/api/v1/auth/logout")
    for password in ("!oidc", "", "anything"):
        resp = await sso_client.post(
            "/api/v1/auth/login", json={"username": "jdoe", "password": password}
        )
        assert resp.status_code == 401


async def test_disabled_sso_user_rejected(sso_client, idp):
    await sso_login(sso_client, idp)
    async with sso_client.app.state.db.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == "jdoe"))
        user.is_active = False
        await db.commit()
    await sso_client.post("/api/v1/auth/logout")
    resp, _ = await sso_login(sso_client, idp, code="code-2")
    assert "disabled" in resp.headers["location"]


async def test_session_cookie_is_lax_with_sso(sso_client):
    resp = await sso_client.get("/api/v1/auth/oidc/login")
    assert "samesite=lax" in resp.headers["set-cookie"].lower()


async def test_local_login_can_be_disabled(sso_settings, idp, tmp_path):
    settings = sso_settings.model_copy(update={"local_login_enabled": False})
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        )
        assert resp.status_code == 403


async def test_sso_disabled_by_default(client):
    assert (await client.get("/api/v1/auth/oidc/login")).status_code == 404
    config = (await client.get("/api/v1/auth/config")).json()
    assert config["oidc"]["enabled"] is False


def test_role_from_claims_precedence():
    settings = Settings(
        _env_file=None,
        oidc_admin_groups=["a"],
        oidc_member_groups=["m"],
        oidc_viewer_groups=["v"],
        oidc_default_role="viewer",
    )
    assert role_from_claims({"groups": ["m", "a"]}, settings) == "admin"
    assert role_from_claims({"groups": ["/x/m"]}, settings) == "member"
    assert role_from_claims({"groups": "v"}, settings) == "viewer"
    assert role_from_claims({}, settings) == "viewer"
    none = settings.model_copy(update={"oidc_default_role": "none"})
    assert role_from_claims({"groups": ["zzz"]}, none) is None
