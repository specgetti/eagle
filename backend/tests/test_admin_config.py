import json

import pytest

from tests.conftest import PR

SA_JSON = json.dumps({"type": "service_account", "project_id": "p", "private_key": "secret-pk"})

CREDENTIAL_CASES = [
    ("anthropic_api_key", {"api_key": "sk-ant-secret-1234"}),
    ("claude_subscription", {"oauth_token": "sk-ant-oat-secret-5678"}),
    ("vertex_ai", {"service_account_json": SA_JSON, "project_id": "proj", "region": "us-east5"}),
    ("openai_api_key", {"api_key": "sk-openai-secret-9999"}),
    ("chatgpt_subscription", {"auth_json": json.dumps({"tokens": {"access_token": "tok"}})}),
    (
        "openai_compatible",
        {"api_key": "maas-secret-0000", "base_url": "https://maas.example.com/v1"},
    ),
]


async def create_credential(client, name="cred", kind="anthropic_api_key", values=None):
    resp = await client.post(
        "/api/v1/credentials",
        json={"name": name, "kind": kind, "values": values or {"api_key": "sk-ant-secret-1234"}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# Platform settings


async def test_settings_defaults_poller_disabled(admin_client):
    settings = (await admin_client.get("/api/v1/settings")).json()
    assert settings["poller_enabled"] is False
    assert settings["max_concurrent_reviews"] == 3
    assert settings["post_comment_default"] is False


async def test_update_settings_and_validation(admin_client):
    body = {
        "max_concurrent_reviews": 5,
        "poller_enabled": True,
        "poll_interval_minutes": 10,
        "post_comment_default": True,
        "default_agent": "codex",
    }
    assert (await admin_client.put("/api/v1/settings", json=body)).status_code == 200
    assert (await admin_client.get("/api/v1/settings")).json() == body

    bad = body | {"max_concurrent_reviews": 0}
    assert (await admin_client.put("/api/v1/settings", json=bad)).status_code == 422
    bad = body | {"poll_interval_minutes": 0}
    assert (await admin_client.put("/api/v1/settings", json=bad)).status_code == 422


async def test_settings_admin_only(user_client):
    member = await user_client("mia", "member")
    assert (await member.get("/api/v1/settings")).status_code == 403
    assert (await member.get("/api/v1/agents")).status_code == 403
    assert (await member.get("/api/v1/credentials")).status_code == 403


# Credentials


@pytest.mark.parametrize(("kind", "values"), CREDENTIAL_CASES)
async def test_create_each_credential_kind_masks_secrets(admin_client, kind, values):
    created = await create_credential(admin_client, name=f"c-{kind}", kind=kind, values=values)
    raw = json.dumps(created) + (await admin_client.get("/api/v1/credentials")).text
    secret_values = [v for k, v in values.items() if k not in ("project_id", "region", "base_url")]
    for secret in secret_values:
        assert secret not in raw, f"secret leaked for {kind}"
    assert set(created["secret_fields"]) == {
        k for k in values if k not in ("project_id", "region", "base_url")
    }
    for public in ("project_id", "region", "base_url"):
        if public in values:
            assert created["public_config"][public] == values[public]


async def test_credential_secret_is_encrypted_in_db(app, admin_client):
    from sqlalchemy import select

    from eagle.models import InferenceCredential

    await create_credential(admin_client, values={"api_key": "sk-ant-very-secret-value"})
    async with app.state.db.sessionmaker() as db:
        row = await db.scalar(select(InferenceCredential))
    assert "sk-ant-very-secret-value" not in row.encrypted_secret


@pytest.mark.parametrize(
    ("kind", "values", "message"),
    [
        ("anthropic_api_key", {}, "api_key"),
        ("anthropic_api_key", {"api_key": "x", "bogus": "y"}, "Unknown"),
        (
            "vertex_ai",
            {"service_account_json": "{not json", "project_id": "p", "region": "r"},
            "JSON",
        ),
        ("vertex_ai", {"service_account_json": "{}", "region": "r"}, "project_id"),
        ("openai_compatible", {"api_key": "x"}, "base_url"),
        ("openai_compatible", {"api_key": "x", "base_url": "ftp://x"}, "http"),
    ],
)
async def test_credential_validation(admin_client, kind, values, message):
    resp = await admin_client.post(
        "/api/v1/credentials", json={"name": "bad", "kind": kind, "values": values}
    )
    assert resp.status_code == 422
    assert message in resp.text


async def test_credential_update_keeps_secret_when_omitted(app, admin_client):
    from eagle.credentials import decrypt_json
    from eagle.models import InferenceCredential

    cred = await create_credential(
        admin_client,
        kind="openai_compatible",
        values={"api_key": "keep-me-secret", "base_url": "https://a.example.com/v1"},
    )
    resp = await admin_client.patch(
        f"/api/v1/credentials/{cred['id']}",
        json={"name": "renamed", "values": {"base_url": "https://b.example.com/v1", "api_key": ""}},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"
    assert resp.json()["public_config"]["base_url"] == "https://b.example.com/v1"
    async with app.state.db.sessionmaker() as db:
        row = await db.get(InferenceCredential, cred["id"])
        assert decrypt_json(app.state.box, row.encrypted_secret) == {"api_key": "keep-me-secret"}


async def test_credential_name_unique(admin_client):
    await create_credential(admin_client, name="dup")
    resp = await admin_client.post(
        "/api/v1/credentials",
        json={"name": "dup", "kind": "anthropic_api_key", "values": {"api_key": "k"}},
    )
    assert resp.status_code == 409


async def test_credential_in_use_cannot_be_deleted(admin_client):
    cred = await create_credential(admin_client)
    await admin_client.put("/api/v1/agents/claude", json={"credential_id": cred["id"]})
    resp = await admin_client.delete(f"/api/v1/credentials/{cred['id']}")
    assert resp.status_code == 409
    assert "claude" in resp.text
    listed = (await admin_client.get("/api/v1/credentials")).json()
    assert listed[0]["used_by"] == ["claude"]

    await admin_client.put("/api/v1/agents/claude", json={"credential_id": None})
    assert (await admin_client.delete(f"/api/v1/credentials/{cred['id']}")).status_code == 204


async def test_credential_types(admin_client):
    types = {t["kind"]: t for t in (await admin_client.get("/api/v1/credentials/types")).json()}
    assert len(types) == 7
    assert types["claude_subscription"]["subscription"] is True
    assert types["vertex_ai"]["agents"] == ["claude", "opencode"]
    assert types["openai_compatible"]["agents"] == ["codex", "opencode"]


# Agents and default models


async def test_agents_seeded(admin_client):
    agents = (await admin_client.get("/api/v1/agents")).json()
    assert [a["agent"] for a in agents] == ["claude", "codex", "opencode"]
    assert all(a["enabled"] for a in agents)


async def test_assign_default_model_and_credential(admin_client):
    cred = await create_credential(admin_client)
    resp = await admin_client.put(
        "/api/v1/agents/claude",
        json={
            "default_model": "claude-opus-5-5",
            "allowed_models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
            "credential_id": cred["id"],
            "instructions": "Check for missing tests.",
            "timeout_seconds": 900,
        },
    )
    assert resp.status_code == 200, resp.text
    agent = resp.json()
    assert agent["default_model"] == "claude-opus-5-5"
    assert agent["allowed_models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]  # deduplicated
    assert agent["credential_id"] == cred["id"]
    assert agent["timeout_seconds"] == 900


async def test_agent_rejects_incompatible_credential(admin_client):
    cred = await create_credential(
        admin_client, kind="claude_subscription", values={"oauth_token": "tok-123456"}
    )
    resp = await admin_client.put("/api/v1/agents/codex", json={"credential_id": cred["id"]})
    assert resp.status_code == 422
    resp = await admin_client.put("/api/v1/agents/claude", json={"credential_id": 9999})
    assert resp.status_code == 422


async def test_default_model_must_be_allowed(admin_client):
    resp = await admin_client.put(
        "/api/v1/agents/codex",
        json={"default_model": "gpt-x", "allowed_models": ["gpt-y"]},
    )
    assert resp.status_code == 422


async def test_agent_timeout_bounds(admin_client):
    resp = await admin_client.put("/api/v1/agents/claude", json={"timeout_seconds": 10})
    assert resp.status_code == 422


async def test_review_uses_agent_default_model(admin_client):
    await admin_client.put("/api/v1/agents/claude", json={"default_model": "claude-sonnet-5-5"})
    review = (await admin_client.post("/api/v1/reviews", json={"pr_url": PR})).json()
    assert review["agent"] == "claude"
    assert review["model"] == "claude-sonnet-5-5"

    review = (
        await admin_client.post("/api/v1/reviews", json={"pr_url": PR, "model": "claude-haiku-5-5"})
    ).json()
    assert review["model"] == "claude-haiku-5-5"  # no allow list: any model


async def test_review_model_must_be_in_allowed_list(admin_client):
    await admin_client.put(
        "/api/v1/agents/codex",
        json={"default_model": "gpt-a", "allowed_models": ["gpt-a", "gpt-b"]},
    )
    ok = await admin_client.post(
        "/api/v1/reviews", json={"pr_url": PR, "agent": "codex", "model": "gpt-b"}
    )
    assert ok.status_code == 201
    bad = await admin_client.post(
        "/api/v1/reviews", json={"pr_url": PR, "agent": "codex", "model": "gpt-z"}
    )
    assert bad.status_code == 422
    assert "not allowed" in bad.text


async def test_disabled_agent_rejected_and_hidden(admin_client, user_client):
    await admin_client.put("/api/v1/agents/opencode", json={"enabled": False})
    resp = await admin_client.post("/api/v1/reviews", json={"pr_url": PR, "agent": "opencode"})
    assert resp.status_code == 422

    member = await user_client("max", "member")
    options = (await member.get("/api/v1/reviews/options")).json()
    assert [o["agent"] for o in options] == ["claude", "codex"]
    assert options[0]["configured"] is False


async def test_platform_default_agent(admin_client):
    settings = (await admin_client.get("/api/v1/settings")).json()
    await admin_client.put("/api/v1/settings", json=settings | {"default_agent": "codex"})
    review = (await admin_client.post("/api/v1/reviews", json={"pr_url": PR})).json()
    assert review["agent"] == "codex"
