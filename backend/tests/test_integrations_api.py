import pytest
from sqlalchemy import select

from eagle.models import GitHost
from tests.conftest import PR

# Repo settings


async def test_repo_settings_crud_and_resolution(admin_client):
    resp = await admin_client.post(
        "/api/v1/repo-settings",
        json={"pattern": "acme/*", "instructions": "Org rules", "default_agent": "codex"},
    )
    assert resp.status_code == 201
    org_id = resp.json()["id"]
    await admin_client.post(
        "/api/v1/repo-settings",
        json={"pattern": "acme/widgets", "default_agent": "opencode", "post_comment": True},
    )
    assert (
        await admin_client.post("/api/v1/repo-settings", json={"pattern": "acme/*"})
    ).status_code == 409

    # Most specific pattern wins for the default agent and comment posting.
    review = (await admin_client.post("/api/v1/reviews", json={"pr_url": PR})).json()
    assert review["agent"] == "opencode"
    assert review["post_comment"] is True

    other = "https://github.com/acme/gears/pull/1"
    review = (await admin_client.post("/api/v1/reviews", json={"pr_url": other})).json()
    assert review["agent"] == "codex"
    assert review["post_comment"] is False

    # Explicit request values win over repo settings.
    review = (
        await admin_client.post(
            "/api/v1/reviews", json={"pr_url": PR, "agent": "claude", "post_comment": False}
        )
    ).json()
    assert review["agent"] == "claude"
    assert review["post_comment"] is False

    resp = await admin_client.put(
        f"/api/v1/repo-settings/{org_id}", json={"pattern": "acme/*", "instructions": "New"}
    )
    assert resp.json()["instructions"] == "New"
    assert (await admin_client.delete(f"/api/v1/repo-settings/{org_id}")).status_code == 204
    assert len((await admin_client.get("/api/v1/repo-settings")).json()) == 1


async def test_repo_setting_pattern_validation(admin_client):
    resp = await admin_client.post("/api/v1/repo-settings", json={"pattern": "acme/$(rm)"})
    assert resp.status_code == 422


# Git hosts


async def test_git_host_token_encrypted_and_hidden(app, admin_client):
    resp = await admin_client.post(
        "/api/v1/git-hosts", json={"host": "github.com", "token": "ghp_supersecrettoken"}
    )
    assert resp.status_code == 201
    host = resp.json()
    assert host["has_token"] is True
    assert host["api_url"] == "https://api.github.com"
    assert "ghp_" not in (await admin_client.get("/api/v1/git-hosts")).text

    async with app.state.db.sessionmaker() as db:
        row = await db.scalar(select(GitHost))
        assert "ghp_" not in row.encrypted_token
        assert app.state.box.decrypt(row.encrypted_token) == "ghp_supersecrettoken"

    # Omitted token keeps it, empty string removes it.
    resp = await admin_client.put(f"/api/v1/git-hosts/{host['id']}", json={"host": "github.com"})
    assert resp.json()["has_token"] is True
    resp = await admin_client.put(
        f"/api/v1/git-hosts/{host['id']}", json={"host": "github.com", "token": ""}
    )
    assert resp.json()["has_token"] is False


async def test_enterprise_host_allows_its_pr_urls(admin_client):
    ghe_pr = "https://github.example.com/team/svc/pull/7"
    assert (await admin_client.post("/api/v1/reviews", json={"pr_url": ghe_pr})).status_code == 422
    resp = await admin_client.post("/api/v1/git-hosts", json={"host": "github.example.com"})
    assert resp.json()["api_url"] == "https://github.example.com/api/v3"
    assert (await admin_client.post("/api/v1/reviews", json={"pr_url": ghe_pr})).status_code == 201


async def test_git_host_api_url_must_be_https(admin_client):
    resp = await admin_client.post(
        "/api/v1/git-hosts", json={"host": "ghe.local", "api_url": "http://ghe.local/api"}
    )
    assert resp.status_code == 422


# Notification channels


async def test_slack_channel_crud_masks_webhook(admin_client):
    resp = await admin_client.post(
        "/api/v1/notifications",
        json={
            "name": "team",
            "kind": "slack",
            "config": {"webhook_url": "https://hooks.slack.com/services/T/B/secretpart"},
        },
    )
    assert resp.status_code == 201, resp.text
    channel = resp.json()
    assert channel["events"] == ["review.succeeded", "review.failed"]
    assert "secretpart" not in (await admin_client.get("/api/v1/notifications")).text

    resp = await admin_client.patch(
        f"/api/v1/notifications/{channel['id']}",
        json={"enabled": False, "events": ["review.failed"]},
    )
    assert resp.json()["enabled"] is False
    assert resp.json()["events"] == ["review.failed"]
    assert (await admin_client.delete(f"/api/v1/notifications/{channel['id']}")).status_code == 204


@pytest.mark.parametrize(
    ("kind", "config", "message"),
    [
        ("slack", {}, "webhook_url or a bot_token"),
        ("slack", {"bot_token": "xoxb-1"}, "channel is required"),
        ("slack", {"webhook_url": "http://insecure"}, "https"),
        ("smtp", {"host": "smtp.example.com"}, "from_addr"),
        ("smtp", {"host": "h", "from_addr": "a@b.c"}, "to_addrs"),
        (
            "smtp",
            {"host": "h", "from_addr": "a@b.c", "to_addrs": ["x@y.z"], "security": "tls1"},
            "security",
        ),
        ("smtp", {"host": "h", "from_addr": "a@b.c", "to_addrs": ["x@y.z"], "bogus": 1}, "Unknown"),
    ],
)
async def test_channel_validation(admin_client, kind, config, message):
    resp = await admin_client.post(
        "/api/v1/notifications", json={"name": "bad", "kind": kind, "config": config}
    )
    assert resp.status_code == 422
    assert message in resp.text


async def test_smtp_update_keeps_password(admin_client, notifier):
    from tests.fakes import FakeSMTP

    resp = await admin_client.post(
        "/api/v1/notifications",
        json={
            "name": "mail",
            "kind": "smtp",
            "config": {
                "host": "smtp.example.com",
                "username": "bot",
                "password": "smtp-pass-123",
                "from_addr": "eagle@example.com",
                "to_addrs": "a@example.com, b@example.com",
            },
        },
    )
    channel = resp.json()
    assert channel["public_config"]["port"] == 587
    assert channel["public_config"]["to_addrs"] == ["a@example.com", "b@example.com"]

    await admin_client.patch(
        f"/api/v1/notifications/{channel['id']}",
        json={
            "config": {
                "host": "smtp2.example.com",
                "username": "bot",
                "from_addr": "eagle@example.com",
                "to_addrs": ["a@example.com"],
            }
        },
    )
    assert (
        await admin_client.post(f"/api/v1/notifications/{channel['id']}/test")
    ).status_code == 204
    sent = FakeSMTP.sent[-1]
    assert sent["host"] == "smtp2.example.com"
    assert sent["login"] == ("bot", "smtp-pass-123")
    assert sent["tls"] is True


async def test_test_notification_slack(admin_client, notifier, fake_github):
    resp = await admin_client.post(
        "/api/v1/notifications",
        json={"name": "bot", "kind": "slack", "config": {"bot_token": "xoxb-1", "channel": "#rev"}},
    )
    channel_id = resp.json()["id"]
    assert (await admin_client.post(f"/api/v1/notifications/{channel_id}/test")).status_code == 204
    message = fake_github.slack_messages[-1]
    assert message["channel"] == "#rev"
    assert message["token"] == "Bearer xoxb-1"
    assert "Test notification" in message["text"]

    fake_github.slack_ok = False
    resp = await admin_client.post(f"/api/v1/notifications/{channel_id}/test")
    assert resp.status_code == 502


# Tracked PRs, audit, metrics


async def test_tracked_prs(admin_client, user_client):
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    tracked = (await admin_client.get("/api/v1/tracked-prs")).json()
    assert len(tracked) == 1
    assert tracked[0]["enabled"] is True

    viewer = await user_client("vic", "viewer")
    assert (await viewer.get("/api/v1/tracked-prs")).status_code == 200
    resp = await viewer.patch(f"/api/v1/tracked-prs/{tracked[0]['id']}", json={"enabled": False})
    assert resp.status_code == 403

    resp = await admin_client.patch(
        f"/api/v1/tracked-prs/{tracked[0]['id']}",
        json={"enabled": False, "poll_interval_minutes": 5},
    )
    assert resp.json()["enabled"] is False
    assert resp.json()["poll_interval_minutes"] == 5


async def test_audit_log(admin_client, user_client):
    await user_client("aud", "viewer")
    await admin_client.put("/api/v1/agents/claude", json={"default_model": "m"})
    page = (await admin_client.get("/api/v1/audit")).json()
    actions = [e["action"] for e in page["items"]]
    assert "user.created" in actions
    assert "agent.updated" in actions
    filtered = (await admin_client.get("/api/v1/audit", params={"action": "agent."})).json()
    assert {e["action"] for e in filtered["items"]} == {"agent.updated"}


async def test_audit_admin_only(user_client):
    member = await user_client("mem", "member")
    assert (await member.get("/api/v1/audit")).status_code == 403


async def test_metrics(admin_client, client):
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    text = (await client.get("/metrics")).text
    assert 'eagle_reviews{agent="claude",status="queued"} 1.0' in text
    assert "eagle_queue_depth 1.0" in text


# Gaps found while building the UI


async def test_timestamps_are_timezone_aware(admin_client):
    await admin_client.post("/api/v1/reviews", json={"pr_url": PR})
    listed = (await admin_client.get("/api/v1/reviews")).json()["items"][0]
    detail = (await admin_client.get(f"/api/v1/reviews/{listed['id']}")).json()
    audit = (await admin_client.get("/api/v1/audit")).json()["items"][0]
    for value in (listed["created_at"], detail["created_at"], audit["created_at"]):
        assert value.endswith(("Z", "+00:00")), value


async def test_slack_switch_mode_drops_old_secret(app, admin_client):
    from eagle.credentials import decrypt_json
    from eagle.models import NotificationChannel

    channel = (
        await admin_client.post(
            "/api/v1/notifications",
            json={
                "name": "s",
                "kind": "slack",
                "config": {"webhook_url": "https://hooks.slack.com/services/x"},
            },
        )
    ).json()
    resp = await admin_client.patch(
        f"/api/v1/notifications/{channel['id']}",
        json={"config": {"bot_token": "xoxb-new", "channel": "#c"}},
    )
    assert list(resp.json()["secret_fields"]) == ["bot_token"]
    # Blank secrets keep what is stored.
    resp = await admin_client.patch(
        f"/api/v1/notifications/{channel['id']}", json={"config": {"channel": "#other"}}
    )
    assert resp.status_code == 200
    async with app.state.db.sessionmaker() as db:
        row = await db.get(NotificationChannel, channel["id"])
        assert decrypt_json(app.state.box, row.encrypted_secret) == {"bot_token": "xoxb-new"}
        assert row.public_config["channel"] == "#other"


async def test_review_list_has_submitter_and_repo_substring(admin_client, user_client):
    member = await user_client("sam", "member")
    await member.post("/api/v1/reviews", json={"pr_url": PR})
    items = (await admin_client.get("/api/v1/reviews", params={"repo": "widg"})).json()["items"]
    assert [i["submitted_by"] for i in items] == ["sam"]
    assert (await admin_client.get("/api/v1/reviews", params={"repo": "nope"})).json()["total"] == 0
