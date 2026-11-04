"""Runner tests. The end-to-end tests use a real local git repo (with a GitHub-style
refs/pull/N/head ref) and fake agent CLIs on PATH that stand in for the model."""

import json
import os
from pathlib import Path

import pytest

from eagle.runner import __main__ as runner
from eagle.runner.parse import AgentOutputError, parse_agent_output, parse_review
from tests.fakes import FAKE_CLAUDE, make_fake_cli

REVIEW_TEXT = """## Summary
Adds widget support. Looks mostly fine.

## Findings
1. **[major]** `widgets.py:3` divides by zero when count is 0.

VERDICT: request_changes"""


# Parsing


def test_parse_claude_json():
    stdout = "noise\n" + json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": REVIEW_TEXT,
            "total_cost_usd": 0.42,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
    )
    out = parse_agent_output("claude_json", stdout)
    assert out.text == REVIEW_TEXT
    assert out.cost_usd == 0.42
    assert out.usage == {"input_tokens": 100, "output_tokens": 50}


@pytest.mark.parametrize(
    "stdout",
    ["not json", json.dumps({"is_error": True, "result": "quota"}), json.dumps({"result": 3})],
)
def test_parse_claude_errors(stdout):
    with pytest.raises(AgentOutputError):
        parse_agent_output("claude_json", stdout)


def test_parse_file_output(tmp_path):
    path = tmp_path / "last.md"
    with pytest.raises(AgentOutputError):
        parse_agent_output("file", "", str(path))
    path.write_text(REVIEW_TEXT)
    assert parse_agent_output("file", "ignored", str(path)).text == REVIEW_TEXT


def test_parse_stdout_strips_ansi():
    assert parse_agent_output("stdout", "\x1b[32mHello\x1b[0m").text == "Hello"


@pytest.mark.parametrize(
    ("line", "verdict"),
    [
        ("VERDICT: approve", "approve"),
        ("**VERDICT:** request_changes", "request_changes"),
        ("Verdict: Request Changes", "request_changes"),
        ("`VERDICT: comment`", "comment"),
    ],
)
def test_parse_review_verdicts(line, verdict):
    parsed = parse_review(f"## Summary\nOk.\n\n{line}")
    assert parsed.verdict == verdict
    assert "VERDICT" not in parsed.markdown.upper()


def test_parse_review_summary_and_no_verdict():
    parsed = parse_review(REVIEW_TEXT)
    assert parsed.summary == "Adds widget support. Looks mostly fine."
    assert "Findings" in parsed.markdown

    parsed = parse_review("Just a paragraph.\n\nAnother one.")
    assert parsed.verdict is None
    assert parsed.summary == "Just a paragraph."

    with pytest.raises(AgentOutputError):
        parse_review("   ")


# End to end with git and fake CLIs


def claude_spec(pr_repo, tmp_path, **overrides) -> dict:
    spec = {
        "review_id": 1,
        "repo": {
            "clone_url": pr_repo["clone_url"],
            "pr_number": 7,
            "base_ref": "main",
            "head_sha": pr_repo["head"],
            "token": None,
        },
        "invocation": {
            "argv": ["claude", "-p", "--output-format", "json", "--model", "m"],
            "env": {"ANTHROPIC_API_KEY": "sk-test-key-123"},
            "files": {},
            "stdin": "REVIEW THIS PR",
            "output": "claude_json",
            "output_file": None,
        },
        "timeout_seconds": 60,
        "result_path": str(tmp_path / "result.json"),
        "callback": None,
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def fake_bin(tmp_path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("EAGLE_SECRET_KEY", "worker-secret")
    return bin_dir


def test_runner_claude_end_to_end(pr_repo, tmp_path, fake_bin):
    make_fake_cli(fake_bin, "claude", FAKE_CLAUDE)
    result = runner.run(claude_spec(pr_repo, tmp_path))
    assert result["status"] == "succeeded", result
    assert result["verdict"] == "approve"
    assert result["head_sha"] == pr_repo["head"]
    assert "return a / b if b else 0" in result["markdown"]  # saw the PR diff
    assert result["cost_usd"] == 0.01
    assert result["summary"].startswith("Reviewed 14 chars")


def test_runner_codex_file_output_and_home_files(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "codex",
        """
        import os, sys, pathlib
        home = pathlib.Path(os.environ["HOME"])
        cfg = (home / ".codex" / "config.toml").read_text()
        assert "base_url" in cfg
        assert os.environ["CODEX_HOME"] == str(home / ".codex")
        out = sys.argv[sys.argv.index("--output-last-message") + 1]
        pathlib.Path(out).write_text("## Summary\\nCodex says hi.\\n\\nVERDICT: comment")
        print("progress noise that is not the review")
        """,
    )
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"] = {
        "argv": ["codex", "exec", "--output-last-message", "{HOME}/codex-last-message.md", "-"],
        "env": {"CODEX_HOME": "{HOME}/.codex", "EAGLE_PROVIDER_API_KEY": "k-123456"},
        "files": {".codex/config.toml": 'base_url = "https://x"\n'},
        "stdin": "P",
        "output": "file",
        "output_file": "{HOME}/codex-last-message.md",
    }
    result = runner.run(spec)
    assert result["status"] == "succeeded", result
    assert result["markdown"] == "## Summary\nCodex says hi."
    assert result["verdict"] == "comment"


def test_runner_redacts_secrets_from_errors(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "claude",
        """
        import os, sys
        print("auth failed for key " + os.environ["ANTHROPIC_API_KEY"], file=sys.stderr)
        sys.exit(3)
        """,
    )
    result = runner.run(claude_spec(pr_repo, tmp_path))
    assert result["status"] == "failed"
    assert "code 3" in result["error"]
    assert "sk-test-key-123" not in result["error"]
    assert "***" in result["error"]


def test_runner_missing_cli(pr_repo, tmp_path, fake_bin):
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["argv"] = ["definitely-not-installed-agent"]
    result = runner.run(spec)
    assert result == {
        "status": "failed",
        "error": "Agent CLI not found: definitely-not-installed-agent",
    }


def test_runner_timeout(pr_repo, tmp_path, fake_bin):
    make_fake_cli(fake_bin, "claude", "import time\ntime.sleep(10)\n")
    result = runner.run(claude_spec(pr_repo, tmp_path, timeout_seconds=1))
    assert result["status"] == "failed"
    assert "timed out" in result["error"]


def test_runner_bad_repo(tmp_path, fake_bin):
    spec = claude_spec({"clone_url": f"file://{tmp_path}/missing.git", "head": None}, tmp_path)
    result = runner.run(spec)
    assert result["status"] == "failed"
    assert result["error"].startswith("Checkout failed")


def test_runner_rejects_file_escape(pr_repo, tmp_path, fake_bin):
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["files"] = {"../../evil": "x"}
    result = runner.run(spec)
    assert result["status"] == "failed"
    assert "outside home" in result["error"]


def test_runner_main_writes_result_file(pr_repo, tmp_path, fake_bin):
    make_fake_cli(fake_bin, "claude", FAKE_CLAUDE)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(claude_spec(pr_repo, tmp_path)))
    assert runner.main([str(spec_path)]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "succeeded"


def test_runner_callback_delivery(pr_repo, tmp_path, fake_bin):
    """The runner POSTs the result with the bearer token to the callback URL."""
    import http.server
    import threading

    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received["auth"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    make_fake_cli(fake_bin, "claude", FAKE_CLAUDE)
    spec = claude_spec(
        pr_repo,
        tmp_path,
        result_path=None,
        callback={"url": f"http://127.0.0.1:{server.server_port}/cb", "token": "tok-abc"},
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    assert runner.main([str(spec_path)]) == 0
    thread.join(timeout=10)
    server.server_close()
    assert received["auth"] == "Bearer tok-abc"
    assert received["body"]["status"] == "succeeded"


def test_runner_uses_git_token_without_persisting_it(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "claude",
        """
        import json, pathlib, subprocess
        config = pathlib.Path(".git/config").read_text()
        assert "ghp_" not in config and "extraHeader" not in config, config
        print(json.dumps({"result": "## Summary\\nok\\nVERDICT: approve"}))
        """,
    )
    spec = claude_spec(pr_repo, tmp_path)
    spec["repo"]["token"] = "ghp_secret_token_value"
    result = runner.run(spec)
    assert result["status"] == "succeeded", result


def test_runner_drops_cost_when_not_reported(pr_repo, tmp_path, fake_bin):
    # Gateway runs (e.g. GLM via an Anthropic-compatible MaaS) would be priced at
    # Anthropic rates by the CLI, so the cost is dropped and only usage is kept.
    make_fake_cli(fake_bin, "claude", FAKE_CLAUDE)
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["report_cost"] = False
    result = runner.run(spec)
    assert result["status"] == "succeeded", result
    assert result["cost_usd"] is None
    assert result["usage"] == {"input_tokens": 5}


def test_runner_reports_claude_api_errors_readably(pr_repo, tmp_path, fake_bin):
    # What Claude Code prints with a bad key: one JSON line, exit code 1.
    make_fake_cli(
        fake_bin,
        "claude",
        """
import json, sys
sys.stdin.read()
print(json.dumps({"type": "result", "is_error": True, "subtype": "success",
                  "result": "Invalid API key · Fix external API key",
                  "api_error_status": 401, "usage": {"input_tokens": 0}}))
sys.exit(1)
""",
    )
    result = runner.run(claude_spec(pr_repo, tmp_path))
    assert result["status"] == "failed"
    assert result["error"] == (
        "Agent exited with code 1: Invalid API key · Fix external API key (HTTP 401)"
    )


def test_runner_failure_detail_falls_back_to_raw_output(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin, "claude", "import sys\nsys.stderr.write('boom: not json')\nsys.exit(2)\n"
    )
    result = runner.run(claude_spec(pr_repo, tmp_path))
    assert result["error"] == "Agent exited with code 2: boom: not json"
