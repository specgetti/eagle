"""Fake external services: GitHub API, Slack and SMTP. No network access in tests."""

import json
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


@dataclass
class FakeGitHub:
    pulls: dict[tuple[str, str, int], dict[str, Any]] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    slack_messages: list[dict[str, Any]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    fail_comments: bool = False
    slack_ok: bool = True

    def add_pull(
        self,
        owner: str = "acme",
        repo: str = "widgets",
        number: int = 42,
        head_sha: str = "a" * 40,
        state: str = "open",
        base_ref: str = "main",
        title: str = "Add widget support",
        author: str = "octocat",
        clone_url: str | None = None,
    ) -> dict[str, Any]:
        data = {
            "title": title,
            "user": {"login": author},
            "head": {"sha": head_sha},
            "base": {
                "ref": base_ref,
                "repo": {"clone_url": clone_url or f"https://github.com/{owner}/{repo}.git"},
            },
            "state": state,
            "merged": False,
        }
        self.pulls[(owner, repo, number)] = data
        return data

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host in ("hooks.slack.com",):
            self.slack_messages.append({"webhook": str(url), **json.loads(request.content)})
            return httpx.Response(200 if self.slack_ok else 500, text="ok")
        if url.host == "slack.com":
            body = json.loads(request.content)
            self.slack_messages.append({"token": request.headers.get("authorization"), **body})
            return httpx.Response(
                200, json={"ok": self.slack_ok} | ({} if self.slack_ok else {"error": "bad"})
            )

        path = url.path.removeprefix("/api/v3")
        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)/pulls/(\d+)", path):
            pull = self.pulls.get((m[1], m[2], int(m[3])))
            if pull is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=pull)
        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)/issues/(\d+)/comments", path):
            if self.fail_comments:
                return httpx.Response(403, json={"message": "Resource not accessible"})
            body = json.loads(request.content)["body"]
            self.comments.append(
                {
                    "repo": f"{m[1]}/{m[2]}",
                    "number": int(m[3]),
                    "body": body,
                    "auth": request.headers.get("authorization"),
                }
            )
            n = len(self.comments)
            return httpx.Response(
                201, json={"html_url": f"https://github.com/{m[1]}/{m[2]}/pull/{m[3]}#c{n}"}
            )
        return httpx.Response(404, json={"message": f"unhandled {request.method} {url}"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL; records what would be sent."""

    sent: list[dict[str, Any]] = []
    fail: bool = False

    def __init__(self, host: str, port: int, timeout: float = 30) -> None:
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:
        import smtplib

        if FakeSMTP.fail:
            raise smtplib.SMTPException("relay denied")
        FakeSMTP.sent.append(
            {
                "host": self.host,
                "port": self.port,
                "tls": self.started_tls,
                "login": self.logged_in,
                "to": message["To"],
                "subject": message["Subject"],
                "body": message.get_content(),
            }
        )


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


# Git repo and fake agent CLIs


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_pr_repo(tmp_path) -> dict:
    """A bare 'origin' repo with main and refs/pull/7/head, like GitHub."""
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "widgets.py").write_text("def ratio(a, b):\n    return a / b\n")
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "base", cwd=work)
    _git("checkout", "-q", "-b", "feature", cwd=work)
    (work / "widgets.py").write_text("def ratio(a, b):\n    return a / b if b else 0\n")
    _git("commit", "-q", "-am", "feature", cwd=work)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True
    ).stdout.strip()

    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(work), str(origin), cwd=tmp_path)
    _git("update-ref", "refs/pull/7/head", head, cwd=origin)
    return {"clone_url": f"file://{origin}", "head": head}


def make_fake_cli(bin_dir: Path, name: str, body: str) -> None:
    """Write a fake agent CLI (Python script) that runs with the test interpreter."""
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    script.chmod(0o755)


FAKE_CLAUDE = """
import json, os, subprocess, sys
prompt = sys.stdin.read()
diff = subprocess.run(["git", "diff", "refs/eagle/base...HEAD"], capture_output=True, text=True).stdout
assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-key-123", "missing key"
assert "EAGLE_SECRET_KEY" not in os.environ, "worker secret leaked"
assert os.environ["HOME"].endswith("home")
assert "--model" in sys.argv
result = "## Summary\\nReviewed " + str(len(prompt)) + " chars.\\n\\n" + diff + "\\nVERDICT: approve"
print(json.dumps({"type": "result", "is_error": False, "result": result,
                  "total_cost_usd": 0.01, "usage": {"input_tokens": 5}}))
"""
