"""Runner tests. The end-to-end tests use a real local git repo (with a GitHub-style
refs/pull/N/head ref) and fake agent CLIs on PATH that stand in for the model."""

import io
import json
import os
from pathlib import Path

import pytest

from eagle.runner import __main__ as runner
from eagle.runner.parse import (
    AgentOutputError,
    claude_model_report,
    parse_agent_output,
    parse_review,
    reported_model,
    same_model,
)
from eagle.runner.progress import Progress, describe_claude_event, describe_model
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


# Progress log (what `oc logs` shows on a review pod)

STREAM_EVENTS = [
    {"type": "system", "subtype": "init", "model": "glm-5.3", "claude_code_version": "2.1.305"},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Let me look at the diff."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "git diff main"}},
                {"type": "tool_use", "name": "Grep", "input": {"pattern": "def ", "path": "src"}},
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "is_error": True, "content": "File does not exist."},
                {"type": "tool_result", "content": "fine"},
            ]
        },
    },
    {
        "type": "result",
        "is_error": False,
        "result": REVIEW_TEXT,
        "num_turns": 4,
        "duration_ms": 61000,
        "usage": {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 80},
    },
]


def test_parse_claude_stream_json_uses_the_result_event():
    stdout = "\n".join(json.dumps(e) for e in STREAM_EVENTS)
    assert parse_agent_output("claude_json", stdout).text == REVIEW_TEXT


def test_parse_claude_stream_without_result_event_fails():
    stdout = "\n".join(json.dumps(e) for e in STREAM_EVENTS[:-1])
    with pytest.raises(AgentOutputError, match="Could not parse"):
        parse_agent_output("claude_json", stdout)


def test_describe_claude_events():
    lines = [line for e in STREAM_EVENTS for line in describe_claude_event(json.dumps(e))]
    assert lines == [
        "Claude Code 2.1.305 started: model glm-5.3",
        "agent: Let me look at the diff.",
        "tool Bash git diff main",
        "tool Grep def  in src",
        "tool error: File does not exist.",
        "Claude Code finished: 4 turns, 61s, 1500 tokens in, 80 out",
    ]
    assert describe_claude_event("not json") == ["not json"]
    assert describe_claude_event('{"type": "stream_event"}') == []


def test_describe_claude_error_result():
    event = {
        "type": "result",
        "is_error": True,
        "result": "Invalid API key",
        "api_error_status": 401,
    }
    assert describe_claude_event(json.dumps(event)) == [
        "Claude Code failed: Invalid API key (HTTP 401)"
    ]


def test_progress_lines_are_redacted_single_line_and_bounded():
    out = io.StringIO()
    ticks = iter([0.0, 12.4, 13.0])
    progress = Progress(["sk-secret-value"], out=out, clock=lambda: next(ticks))
    progress.log("key sk-secret-value\nsecond line")
    progress.log("x" * 1000)
    lines = out.getvalue().splitlines()
    assert lines[0] == "[   12s] key *** second line"
    assert len(lines[1]) < 320 and lines[1].endswith("...")


def test_runner_streams_progress_while_the_agent_runs(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "claude",
        f"""
import json, sys
sys.stdin.read()
events = {json.dumps(STREAM_EVENTS)!r}
for event in json.loads(events):
    print(json.dumps(event), flush=True)
sys.stderr.write("warning: using sk-test-key-123\\n")
""",
    )
    out = io.StringIO()
    spec = claude_spec(pr_repo, tmp_path, model_source="agent_default")
    result = runner.run(spec, progress=Progress(["sk-test-key-123"], out=out))
    assert result["status"] == "succeeded", result
    assert result["model"] == "glm-5.3"
    log = out.getvalue()
    for expected in (
        "checking out pull request 7 of ",
        f"checked out {pr_repo['head']}",
        "starting claude with model m (agent default)",
        "the agent reports it used model glm-5.3",
        "Claude Code 2.1.305 started: model glm-5.3",
        "tool Bash git diff main",
        "claude: warning: using ***",
        "claude exited with code 0",
        "review parsed: verdict request_changes",
    ):
        assert expected in log, (expected, log)
    assert "sk-test-key-123" not in log


def test_runner_logs_plain_agent_output(pr_repo, tmp_path, fake_bin):
    make_fake_cli(fake_bin, "opencode", "print('thinking about it')\nprint('VERDICT: approve')\n")
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"] |= {"argv": ["opencode", "run", "PROMPT"], "stdin": None, "output": "stdout"}
    out = io.StringIO()
    result = runner.run(spec, progress=Progress([], out=out))
    assert result["status"] == "succeeded", result
    assert "model" not in result or result["model"] is None
    log = out.getvalue()
    assert "opencode: thinking about it" in log
    assert "starting opencode with the CLI's default model" in log
    assert "the agent did not report which model it used" in log


@pytest.mark.parametrize(
    ("kind", "stdout", "stderr", "expected"),
    [
        (
            "claude_json",
            '{"type":"system","subtype":"init","model":"glm-5.3"}\n{"type":"result"}',
            "",
            "glm-5.3",
        ),
        ("claude_json", '{"type":"result","result":"x"}', "", None),
        ("claude_json", "not json\n", "", None),
        ("file", "", "workdir: /x\nmodel: gpt-5-codex\nprovider: openai\n", "gpt-5-codex"),
        ("file", "", "\x1b[1mmodel:\x1b[0m o4-mini\n", "o4-mini"),
        ("stdout", "the model: is not a header line\n", "", None),
    ],
)
def test_reported_model(kind, stdout, stderr, expected):
    assert reported_model(kind, stdout, stderr) == expected


def test_describe_model():
    assert describe_model("glm-5", "agent_default") == "model glm-5 (agent default)"
    assert describe_model("glm-5", "requested") == "model glm-5 (requested)"
    assert describe_model("glm-5", None) == "model glm-5"
    assert describe_model(None, None) == "the CLI's default model"


def test_runner_reports_the_codex_model(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "codex",
        """
        import sys, pathlib
        sys.stdin.read()
        sys.stderr.write("--------\\nworkdir: /tmp\\nmodel: gpt-5-codex\\nprovider: openai\\n--------\\n")
        out = sys.argv[sys.argv.index("--output-last-message") + 1]
        pathlib.Path(out).write_text("Fine.\\n\\nVERDICT: approve")
        """,
    )
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"] = {
        "argv": ["codex", "exec", "--output-last-message", "{HOME}/last.md", "-"],
        "env": {},
        "files": {},
        "stdin": "P",
        "output": "file",
        "output_file": "{HOME}/last.md",
    }
    result = runner.run(spec, progress=Progress([], out=io.StringIO()))
    assert result["status"] == "succeeded", result
    assert result["model"] == "gpt-5-codex"


def test_runner_reports_the_model_of_a_failed_run(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "claude",
        """
import json, sys
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-5"}))
print(json.dumps({"type": "result", "is_error": True, "result": "model not found"}))
sys.exit(1)
""",
    )
    result = runner.run(claude_spec(pr_repo, tmp_path), progress=Progress([], out=io.StringIO()))
    assert result["status"] == "failed"
    assert result["model"] == "claude-sonnet-4-5"
    assert "model not found" in result["error"]


def _assistant(message_id, model, text="x", **usage):
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": usage,
        },
    }


SERVED_EVENTS = [
    {"type": "system", "subtype": "init", "model": "glm-5"},
    # One response printed as two events (one per content block): counted once.
    _assistant("m1", "glm-5", input_tokens=100, cache_read_input_tokens=50, output_tokens=10),
    _assistant("m1", "glm-5", input_tokens=100, cache_read_input_tokens=50, output_tokens=10),
    _assistant("m2", "qwen-3-coder", input_tokens=200, output_tokens=40),
    _assistant("m3", "<synthetic>", text="API Error"),
    {
        "type": "result",
        "is_error": False,
        "result": "Looks fine.\n\nVERDICT: approve",
        "modelUsage": {
            "glm-5": {"inputTokens": 150, "outputTokens": 10, "cacheReadInputTokens": 50},
            "claude-haiku-4-5": {"inputTokens": 30, "outputTokens": 5},
        },
    },
]


def test_claude_model_report_counts_responses_and_tokens():
    stdout = "\n".join(json.dumps(e) for e in SERVED_EVENTS)
    assert claude_model_report(stdout) == {
        "served": [
            {"model": "qwen-3-coder", "responses": 1, "input_tokens": 200, "output_tokens": 40},
            {"model": "glm-5", "responses": 1, "input_tokens": 150, "output_tokens": 10},
        ],
        "usage_by_model": [
            {"model": "glm-5", "input_tokens": 200, "output_tokens": 10},
            {"model": "claude-haiku-4-5", "input_tokens": 30, "output_tokens": 5},
        ],
    }
    assert claude_model_report('{"type":"result","result":"x"}\nnot json') is None


@pytest.mark.parametrize(
    ("requested", "served", "same"),
    [
        ("glm-5", "glm-5", True),
        ("GLM-5", "glm-5", True),
        ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", True),
        ("claude-sonnet-4-5", "claude-sonnet-4-5@20250929", True),
        ("glm-5", "zai/glm-5", True),
        ("openai/gpt-5", "gpt-5", True),
        ("glm-5", "glm-5.3", False),
        ("glm-5", "qwen-3-coder", False),
        ("claude-sonnet-4-5", "claude-haiku-4-5", False),
        ("", "glm-5", False),
    ],
)
def test_same_model(requested, served, same):
    assert same_model(requested, served) is same


def test_runner_warns_when_responses_come_from_another_model(pr_repo, tmp_path, fake_bin):
    make_fake_cli(
        fake_bin,
        "claude",
        f"""
import json, sys
sys.stdin.read()
for event in json.loads({json.dumps(json.dumps(SERVED_EVENTS))}):
    print(json.dumps(event), flush=True)
""",
    )
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["argv"] = ["claude", "-p", "--model", "glm-5"]
    out = io.StringIO()
    result = runner.run(spec, progress=Progress([], out=out))
    assert result["status"] == "succeeded", result
    assert [e["model"] for e in result["model_report"]["served"]] == ["qwen-3-coder", "glm-5"]
    log = out.getvalue()
    assert "the endpoint answered as qwen-3-coder (1 response), glm-5 (1 response)" in log
    assert "WARNING: glm-5 was requested, but responses came from qwen-3-coder" in log


def test_model_names_in_the_agent_env_are_not_redacted(pr_repo, tmp_path, fake_bin):
    # Gateway credentials put the model in ANTHROPIC_MODEL; it must still show.
    make_fake_cli(
        fake_bin,
        "claude",
        """
import json, sys
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "model": "glm-5.3"}))
print(json.dumps({"type": "result", "is_error": False,
                  "result": "Uses key sk-gateway-key-999. VERDICT: approve"}))
""",
    )
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["argv"] = ["claude", "-p"]
    spec["invocation"]["env"] = {
        "ANTHROPIC_AUTH_TOKEN": "sk-gateway-key-999",
        "ANTHROPIC_BASE_URL": "https://maas.example.com",
        "ANTHROPIC_MODEL": "glm-5.3",
        "ANTHROPIC_SMALL_FAST_MODEL": "glm-5.3",
    }
    out = io.StringIO()
    secrets = [v for k, v in spec["invocation"]["env"].items() if not k.endswith("_MODEL")]
    result = runner.run(spec, progress=Progress(secrets, out=out))
    assert result["status"] == "succeeded", result
    assert result["model"] == "glm-5.3"
    assert "sk-gateway-key-999" not in result["markdown"]
    log = out.getvalue()
    assert "starting claude with model glm-5.3" in log
    assert "Claude Code started: model glm-5.3" in log
    assert "the agent reports it used model glm-5.3" in log
    assert "sk-gateway-key-999" not in log


def test_runner_redacts_every_env_value_except_model_names(
    pr_repo, tmp_path, fake_bin, monkeypatch
):
    make_fake_cli(fake_bin, "claude", FAKE_CLAUDE)
    spec = claude_spec(pr_repo, tmp_path)
    spec["invocation"]["env"] |= {"ANTHROPIC_MODEL": "glm-5.3", "OTHER": "private-value"}
    captured = {}
    real = runner.Progress

    def spy(secrets, *args, **kwargs):
        captured["secrets"] = list(secrets)
        return real(secrets, *args, **kwargs)

    monkeypatch.setattr(runner, "Progress", spy)
    runner.run(spec)
    assert "private-value" in captured["secrets"]
    assert "sk-test-key-123" in captured["secrets"]
    assert "glm-5.3" not in captured["secrets"]
