"""Credential "Test" button and model listing, against fake provider endpoints."""

import json
from collections.abc import Callable

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from eagle.providers import ProviderClient

MAAS = "https://maas.example"
KEY = "maas-secret-key-123"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeProviders:
    """Routes requests by (method, url-without-query) and records them."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, url: str, handler: Handler | httpx.Response) -> None:
        self.routes[(method, url)] = handler if callable(handler) else (lambda _r, h=handler: h)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url.copy_with(query=None)))
        if key not in self.routes:
            return httpx.Response(404, json={"error": {"message": "no such route"}})
        return self.routes[key](request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture
def providers(app) -> FakeProviders:
    fake = FakeProviders()
    app.state.providers = ProviderClient(transport=fake.transport)
    return fake


async def create(admin_client, kind: str, values: dict, name: str = "cred") -> int:
    resp = await admin_client.post(
        "/api/v1/credentials", json={"name": name, "kind": kind, "values": values}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def maas_credential(admin_client, **extra) -> int:
    return await create(
        admin_client, "anthropic_compatible", {"base_url": MAAS + "/", "api_key": KEY, **extra}
    )


# Anthropic-compatible gateway (MaaS)


async def test_gateway_credential_validation(admin_client):
    resp = await admin_client.post(
        "/api/v1/credentials",
        json={
            "name": "bad",
            "kind": "anthropic_compatible",
            "values": {"base_url": MAAS, "api_key": KEY, "auth_style": "basic"},
        },
    )
    assert resp.status_code == 422
    assert "auth_style" in resp.json()["detail"]

    cred_id = await maas_credential(admin_client)
    creds = (await admin_client.get("/api/v1/credentials")).json()
    cred = next(c for c in creds if c["id"] == cred_id)
    assert cred["public_config"]["base_url"] == MAAS  # trailing slash removed
    assert KEY not in json.dumps(cred)

    types = {t["kind"]: t for t in (await admin_client.get("/api/v1/credentials/types")).json()}
    gateway = types["anthropic_compatible"]
    assert gateway["agents"] == ["claude"]
    auth = next(f for f in gateway["fields"] if f["name"] == "auth_style")
    assert auth["choices"] == ["x-api-key", "bearer"]
    assert auth["help"]

    # Claude accepts it; Codex does not.
    ok = await admin_client.put("/api/v1/agents/claude", json={"credential_id": cred_id})
    assert ok.status_code == 200
    bad = await admin_client.put("/api/v1/agents/codex", json={"credential_id": cred_id})
    assert bad.status_code == 422


async def test_gateway_lists_models_with_x_api_key(admin_client, providers):
    providers.on(
        "GET",
        f"{MAAS}/v1/models",
        httpx.Response(
            200,
            json={
                "data": [{"id": "rits/zai-org/glm-5-3"}, {"id": "claude-opus-5-5"}],
                "has_more": False,
            },
        ),
    )
    cred_id = await maas_credential(admin_client)

    resp = await admin_client.get(f"/api/v1/credentials/{cred_id}/models")
    assert resp.status_code == 200
    assert resp.json() == {
        "models": ["claude-opus-5-5", "rits/zai-org/glm-5-3"],
        "source": "provider",
        "message": None,
    }
    sent = providers.requests[-1]
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"]
    assert "authorization" not in sent.headers


async def test_gateway_model_pagination(admin_client, providers):
    def page(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("after_id") == "m2":
            return httpx.Response(200, json={"data": [{"id": "m3"}], "has_more": False})
        return httpx.Response(
            200, json={"data": [{"id": "m1"}, {"id": "m2"}], "has_more": True, "last_id": "m2"}
        )

    providers.on("GET", f"{MAAS}/v1/models", page)
    cred_id = await maas_credential(admin_client)
    resp = await admin_client.get(f"/api/v1/credentials/{cred_id}/models")
    assert resp.json()["models"] == ["m1", "m2", "m3"]


async def test_gateway_test_without_model(admin_client, providers):
    providers.on(
        "GET",
        f"{MAAS}/v1/models",
        httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}),
    )
    cred_id = await maas_credential(admin_client, auth_style="bearer")
    resp = await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["models_found"] == 2
    assert isinstance(body["latency_ms"], int)
    assert providers.requests[-1].headers["authorization"] == f"Bearer {KEY}"
    assert "x-api-key" not in providers.requests[-1].headers


async def test_gateway_test_with_model_sends_one_token_request(admin_client, providers):
    providers.on(
        "POST",
        f"{MAAS}/v1/messages",
        httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": "p"}]}),
    )
    cred_id = await maas_credential(admin_client)
    resp = await admin_client.post(
        f"/api/v1/credentials/{cred_id}/test", json={"model": "rits/zai-org/glm-5-3"}
    )
    assert resp.json()["ok"] is True
    assert "rits/zai-org/glm-5-3" in resp.json()["message"]
    sent = json.loads(providers.requests[-1].content)
    assert sent["model"] == "rits/zai-org/glm-5-3"
    assert sent["max_tokens"] == 1


async def test_gateway_rejected_key_is_reported_and_redacted(admin_client, providers):
    providers.on(
        "GET",
        f"{MAAS}/v1/models",
        httpx.Response(401, json={"error": {"message": f"invalid key {KEY}"}}),
    )
    cred_id = await maas_credential(admin_client)

    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is False
    assert "401" in test["message"] and "rejected" in test["message"]
    assert KEY not in test["message"]

    models = await admin_client.get(f"/api/v1/credentials/{cred_id}/models")
    assert models.status_code == 502
    assert KEY not in models.text


async def test_gateway_without_model_listing(admin_client, providers):
    # No /v1/models route: the fake answers 404.
    cred_id = await maas_credential(admin_client)
    models = (await admin_client.get(f"/api/v1/credentials/{cred_id}/models")).json()
    assert models["source"] == "suggested"
    assert models["models"] == []
    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is None
    assert "does not list models" in test["message"]


async def test_unreachable_and_non_json_endpoints(admin_client, app):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app.state.providers = ProviderClient(transport=httpx.MockTransport(boom))
    cred_id = await maas_credential(admin_client)
    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is False
    assert "Could not reach maas.example" in test["message"]

    app.state.providers = ProviderClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>login</html>"))
    )
    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is False
    assert "non-JSON" in test["message"]


# OpenAI-style endpoints


async def test_openai_compatible_models_and_ping(admin_client, providers):
    base = "https://llm.example/v1"
    providers.on("GET", f"{base}/models", httpx.Response(200, json={"data": [{"id": "granite-4"}]}))
    providers.on("POST", f"{base}/chat/completions", httpx.Response(200, json={"choices": []}))
    cred_id = await create(
        admin_client, "openai_compatible", {"base_url": base, "api_key": KEY}, name="oc"
    )
    models = (await admin_client.get(f"/api/v1/credentials/{cred_id}/models")).json()
    assert models["models"] == ["granite-4"]
    assert providers.requests[-1].headers["authorization"] == f"Bearer {KEY}"

    test = (
        await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={"model": "granite-4"})
    ).json()
    assert test["ok"] is True
    assert json.loads(providers.requests[-1].content)["max_tokens"] == 1


async def test_openai_api_key_uses_default_base(admin_client, providers):
    providers.on(
        "GET",
        "https://api.openai.com/v1/models",
        httpx.Response(200, json={"data": [{"id": "gpt-x"}]}),
    )
    cred_id = await create(admin_client, "openai_api_key", {"api_key": KEY}, name="oa")
    models = (await admin_client.get(f"/api/v1/credentials/{cred_id}/models")).json()
    assert models["models"] == ["gpt-x"]


# Vertex AI


def _service_account() -> tuple[str, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "eagle@proj.iam.gserviceaccount.com",
            "private_key": pem,
            "private_key_id": "kid-1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ), key.public_key()


async def test_vertex_token_exchange_and_model_ping(admin_client, providers):
    sa_json, public_key = _service_account()

    def token(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        claims = jwt.decode(
            form["assertion"],
            public_key,
            algorithms=["RS256"],
            audience="https://oauth2.googleapis.com/token",
        )
        assert claims["iss"] == "eagle@proj.iam.gserviceaccount.com"
        assert "cloud-platform" in claims["scope"]
        return httpx.Response(200, json={"access_token": "ya29.token"})

    providers.on("POST", "https://oauth2.googleapis.com/token", token)
    predict = (
        "https://us-east5-aiplatform.googleapis.com/v1/projects/proj/locations/us-east5"
        "/publishers/anthropic/models/claude-opus-5-5:rawPredict"
    )
    providers.on("POST", predict, httpx.Response(200, json={"content": []}))
    cred_id = await create(
        admin_client,
        "vertex_ai",
        {"service_account_json": sa_json, "project_id": "proj", "region": "us-east5"},
        name="vx",
    )

    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is True
    assert "service account is valid" in test["message"]

    test = (
        await admin_client.post(
            f"/api/v1/credentials/{cred_id}/test", json={"model": "claude-opus-5-5"}
        )
    ).json()
    assert test["ok"] is True, test
    assert providers.requests[-1].headers["authorization"] == "Bearer ya29.token"
    assert json.loads(providers.requests[-1].content)["max_tokens"] == 1

    models = (await admin_client.get(f"/api/v1/credentials/{cred_id}/models")).json()
    assert models["source"] == "suggested"
    assert "opus" in models["models"]


async def test_vertex_global_region_and_bad_key(admin_client, providers):
    providers.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        httpx.Response(200, json={"access_token": "t"}),
    )
    providers.on(
        "POST",
        "https://aiplatform.googleapis.com/v1/projects/p/locations/global"
        "/publishers/anthropic/models/m:rawPredict",
        httpx.Response(403, json={"error": {"message": "Permission denied on model"}}),
    )
    sa_json, _ = _service_account()
    cred_id = await create(
        admin_client,
        "vertex_ai",
        {"service_account_json": sa_json, "project_id": "p", "region": "global"},
        name="vg",
    )
    test = (
        await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={"model": "m"})
    ).json()
    assert test["ok"] is False
    assert "403" in test["message"] and "Permission denied" in test["message"]

    broken = await create(
        admin_client,
        "vertex_ai",
        {"service_account_json": '{"type": "x"}', "project_id": "p", "region": "global"},
        name="vbad",
    )
    test = (await admin_client.post(f"/api/v1/credentials/{broken}/test", json={})).json()
    assert test["ok"] is False
    assert "client_email" in test["message"]


# Subscriptions


async def test_subscriptions_cannot_be_tested_remotely(admin_client, providers):
    claude = await create(admin_client, "claude_subscription", {"oauth_token": "t" * 20}, name="cs")
    test = (await admin_client.post(f"/api/v1/credentials/{claude}/test", json={})).json()
    assert test["ok"] is None
    models = (await admin_client.get(f"/api/v1/credentials/{claude}/models")).json()
    assert models["source"] == "suggested" and "sonnet" in models["models"]

    bad = await create(admin_client, "chatgpt_subscription", {"auth_json": "{}"}, name="gb")
    test = (await admin_client.post(f"/api/v1/credentials/{bad}/test", json={})).json()
    assert test["ok"] is False and "codex login" in test["message"]

    good = await create(
        admin_client,
        "chatgpt_subscription",
        {"auth_json": json.dumps({"tokens": {"access_token": "x"}})},
        name="gg",
    )
    test = (await admin_client.post(f"/api/v1/credentials/{good}/test", json={})).json()
    assert test["ok"] is None
    assert providers.requests == []  # nothing left the server


# Access control and audit


async def test_only_admins_can_test_or_list(admin_client, user_client, providers):
    cred_id = await maas_credential(admin_client)
    member = await user_client("dev", "member")
    assert (await member.post(f"/api/v1/credentials/{cred_id}/test", json={})).status_code == 403
    assert (await member.get(f"/api/v1/credentials/{cred_id}/models")).status_code == 403
    assert (await admin_client.post("/api/v1/credentials/999/test", json={})).status_code == 404
    assert providers.requests == []


async def test_test_is_audited(admin_client, providers):
    cred_id = await maas_credential(admin_client)
    await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={"model": "m"})
    entries = (await admin_client.get("/api/v1/audit")).json()
    items = entries["items"] if isinstance(entries, dict) else entries
    tested = [e for e in items if e["action"] == "credential.tested"]
    assert tested and tested[0]["details"] == {"model": "m", "ok": False}


# Base URL mistakes (seen with a MaaS behind an OpenShift router)

ROUTER_503 = httpx.Response(
    503,
    headers={"content-type": "text/html"},
    text='<html> <head> <meta name="viewport" content="width=device-width"> <style>body{}</style>',
)


async def test_openai_base_without_v1_gets_exact_fix(admin_client, providers):
    base = "https://maas.example"
    providers.on("GET", f"{base}/models", ROUTER_503)
    providers.on("POST", f"{base}/chat/completions", ROUTER_503)
    providers.on("GET", f"{base}/v1/models", httpx.Response(200, json={"data": [{"id": "glm"}]}))
    cred_id = await create(
        admin_client, "openai_compatible", {"base_url": base, "api_key": KEY}, name="nov1"
    )

    models = await admin_client.get(f"/api/v1/credentials/{cred_id}/models")
    assert models.status_code == 502
    detail = models.json()["detail"]
    assert "set the base URL to https://maas.example/v1" in detail
    assert "<html" not in detail

    test = (await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={})).json()
    assert test["ok"] is False and "/v1" in test["message"]
    ping = (
        await admin_client.post(f"/api/v1/credentials/{cred_id}/test", json={"model": "glm"})
    ).json()
    assert ping["ok"] is False and "set the base URL to https://maas.example/v1" in ping["message"]


async def test_html_error_page_is_summarized(admin_client, providers):
    # Already versioned, so no /v1 probe: just a readable message instead of HTML.
    base = "https://maas.example/v1"
    providers.on("GET", f"{base}/models", ROUTER_503)
    cred_id = await create(
        admin_client, "openai_compatible", {"base_url": base, "api_key": KEY}, name="html"
    )
    detail = (await admin_client.get(f"/api/v1/credentials/{cred_id}/models")).json()["detail"]
    assert detail == (
        "HTTP 503: maas.example returned a web page instead of an API response. Check the base URL."
    )
    assert not any(str(r.url).endswith("/v1/v1/models") for r in providers.requests)


async def test_anthropic_gateway_strips_trailing_v1(admin_client):
    cred_id = await create(
        admin_client,
        "anthropic_compatible",
        {"base_url": "https://maas.example/v1/", "api_key": KEY},
        name="v1",
    )
    creds = (await admin_client.get("/api/v1/credentials")).json()
    assert next(c for c in creds if c["id"] == cred_id)["public_config"]["base_url"] == (
        "https://maas.example"
    )
