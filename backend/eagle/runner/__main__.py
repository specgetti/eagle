"""Entry point: python -m eagle.runner [SPEC_PATH]

The spec is a JSON document (written by the worker, mounted from a Secret in
Kubernetes) with the repo to check out, the agent invocation and where to send
the result. See eagle.executors.base.build_run_spec for its shape.
"""

import base64
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from eagle.runner.parse import (
    AgentOutputError,
    claude_model_report,
    find_claude_result,
    parse_agent_output,
    parse_review,
    reported_model,
    same_model,
)
from eagle.runner.progress import Progress, describe_claude_event, describe_model, redact

DEFAULT_SPEC_PATH = "/var/run/eagle/spec.json"
OUTPUT_TAIL = 4000
PASSTHROUGH_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)


class RunError(Exception):
    pass


def _expand(value: Any, home: Path) -> Any:
    if isinstance(value, str):
        return value.replace("{HOME}", str(home))
    if isinstance(value, list):
        return [_expand(v, home) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, home) for k, v in value.items()}
    return value


_redact = redact


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )
    if proc.returncode != 0:
        raise RunError(f"git {args[0]} failed: {proc.stderr.strip()[-OUTPUT_TAIL:]}")
    return proc.stdout.strip()


def checkout(repo: dict[str, Any], workdir: Path, home: Path) -> str:
    """Fetch the PR head (and target branch) and check it out. Returns the head SHA."""
    env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
    env |= {"HOME": str(home), "GIT_TERMINAL_PROMPT": "0"}
    workdir.mkdir(parents=True)
    _git(["init", "-q"], workdir, env)
    _git(["remote", "add", "origin", repo["clone_url"]], workdir, env)

    fetch_env = dict(env)
    if repo.get("token"):
        # Passed through the environment so it never lands in .git/config or argv,
        # where the agent could read it.
        basic = base64.b64encode(f"x-access-token:{repo['token']}".encode()).decode()
        fetch_env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        }

    refspecs = [f"+refs/pull/{int(repo['pr_number'])}/head:refs/eagle/head"]
    if repo.get("base_ref"):
        refspecs.append(f"+refs/heads/{repo['base_ref']}:refs/eagle/base")
    _git(["fetch", "--no-tags", "-q", "origin", *refspecs], workdir, fetch_env)
    _git(["checkout", "-q", "--detach", "refs/eagle/head"], workdir, env)
    return _git(["rev-parse", "HEAD"], workdir, env)


def run_agent(
    argv: list[str],
    stdin: str | None,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    on_line: Any,
) -> tuple[int, str, str]:
    """Run the agent CLI, calling on_line(stream, line) for each line as it arrives.

    Returns (exit code, stdout, stderr). Raises subprocess.TimeoutExpired after
    killing the process, and FileNotFoundError if the CLI is missing.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        bufsize=1,
    )
    captured: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def pump(name: str, stream: Any) -> None:
        for line in stream:
            captured[name].append(line)
            # Progress output must never break a run.
            with contextlib.suppress(Exception):
                on_line(name, line.rstrip("\n"))

    def feed() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(stdin or "")
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threads = [
        threading.Thread(target=pump, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=pump, args=("stderr", proc.stderr), daemon=True),
    ]
    if stdin is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=10)
    return returncode, "".join(captured["stdout"]), "".join(captured["stderr"])


def _agent_model(argv: list[str], env: dict[str, str]) -> str | None:
    for flag in ("--model", "-m"):
        if flag in argv[:-1]:
            return argv[argv.index(flag) + 1]
    return env.get("ANTHROPIC_MODEL")


def run(spec: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    invocation = spec["invocation"]
    secrets = [v for v in invocation.get("env", {}).values() if isinstance(v, str)]
    if spec["repo"].get("token"):
        secrets.append(spec["repo"]["token"])
    progress = progress or Progress(secrets)

    with tempfile.TemporaryDirectory(prefix="eagle-run-") as tmp:
        home = Path(tmp) / "home"
        workdir = Path(tmp) / "repo"
        home.mkdir()
        for rel_path, content in invocation.get("files", {}).items():
            target = (home / rel_path).resolve()
            if home.resolve() not in target.parents:
                return _failed(f"Refusing to write file outside home: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_expand(content, home))
            target.chmod(0o600)

        repo = spec["repo"]
        progress.log(f"checking out pull request {repo.get('pr_number')} of {repo['clone_url']}")
        try:
            head_sha = checkout(repo, workdir, home)
        except (RunError, subprocess.TimeoutExpired) as exc:
            progress.log(f"checkout failed: {exc}")
            return _failed(_redact(f"Checkout failed: {exc}", secrets))
        progress.log(f"checked out {head_sha}")

        env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
        env |= {"HOME": str(home), "GIT_TERMINAL_PROMPT": "0", "CI": "1"}
        env |= _expand(invocation.get("env", {}), home)
        argv = _expand(invocation["argv"], home)
        output_kind = invocation.get("output", "stdout")
        timeout = spec.get("timeout_seconds", 1800)

        def on_line(stream: str, line: str) -> None:
            if stream == "stdout" and output_kind == "claude_json":
                for message in describe_claude_event(line):
                    progress.log(message)
            else:
                progress.log(f"{argv[0]}: {line}")

        model = _agent_model(argv, env)
        progress.log(f"starting {argv[0]} with {describe_model(model, spec.get('model_source'))}")
        try:
            returncode, stdout, stderr = run_agent(
                argv, invocation.get("stdin"), workdir, env, timeout, on_line
            )
        except FileNotFoundError:
            progress.log(f"agent CLI not found: {argv[0]}")
            return _failed(f"Agent CLI not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            progress.log(f"agent timed out after {timeout}s")
            return _failed(f"Agent timed out after {spec.get('timeout_seconds')}s")
        progress.log(f"{argv[0]} exited with code {returncode}")
        used = reported_model(output_kind, stdout, stderr)
        if used:
            used = _redact(used, secrets)[:128]
            progress.log(f"the agent reports it used model {used}")
        else:
            progress.log("the agent did not report which model it used")
        report = claude_model_report(stdout) if output_kind == "claude_json" else None
        if report:
            _log_served(progress, report, model or used)
        extra = {"model": used, "model_report": report}

        if returncode != 0:
            detail = _failure_detail(output_kind, stdout, stderr)
            error = _redact(f"Agent exited with code {returncode}: {detail}", secrets)
            return _failed(error, **extra)

        try:
            output = parse_agent_output(
                output_kind, stdout, _expand(invocation.get("output_file"), home)
            )
            review = parse_review(output.text)
        except AgentOutputError as exc:
            progress.log(str(exc))
            return _failed(_redact(str(exc), secrets), **extra)
        progress.log(f"review parsed: verdict {review.verdict or 'none'}")

        return {
            "status": "succeeded",
            "markdown": _redact(review.markdown, secrets),
            "verdict": review.verdict,
            "summary": _redact(review.summary, secrets) if review.summary else None,
            "usage": output.usage,
            "cost_usd": output.cost_usd if invocation.get("report_cost", True) else None,
            "head_sha": head_sha,
            **extra,
        }


def _log_served(progress: Progress, report: dict[str, Any], expected: str | None) -> None:
    """Which models the endpoint says answered, and a warning if any is unexpected."""
    served = report.get("served") or []
    if not served:
        return
    progress.log(
        "the endpoint answered as "
        + ", ".join(
            f"{e['model']} ({e['responses']} response{'' if e['responses'] == 1 else 's'})"
            for e in served
        )
    )
    others = [e["model"] for e in served if expected and not same_model(expected, e["model"])]
    if others:
        names = ", ".join(others)
        progress.log(f"WARNING: {expected} was requested, but responses came from {names}")


def _failure_detail(output_kind: str | None, stdout: str, stderr: str) -> str:
    """The most useful part of a failed agent run's output.

    Claude Code reports errors (bad key, unknown model, API outage) in the
    `result` field of its final JSON event on stdout, which is otherwise a long
    stream of events.
    """
    if output_kind == "claude_json":
        data = find_claude_result(stdout)
        if isinstance(data, dict) and data.get("result"):
            status = data.get("api_error_status")
            return str(data["result"]) + (f" (HTTP {status})" if status else "")
    return (stderr.strip() or stdout.strip())[-OUTPUT_TAIL:]


def _failed(error: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "failed", "error": error}
    result.update({k: v for k, v in extra.items() if v})
    return result


def deliver(spec: dict[str, Any], result: dict[str, Any]) -> None:
    if spec.get("result_path"):
        Path(spec["result_path"]).write_text(json.dumps(result))

    callback = spec.get("callback")
    if not callback:
        return
    body = json.dumps(result).encode()
    last_error: Exception | None = None
    for attempt in range(5):
        request = urllib.request.Request(
            callback["url"],
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {callback['token']}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                if resp.status < 300:
                    return
        except Exception as exc:  # noqa: BLE001 (retry on any network error)
            last_error = exc
        time.sleep(2**attempt)
    raise RunError(f"Could not deliver result: {last_error}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    spec_path = argv[0] if argv else os.environ.get("EAGLE_RUN_SPEC", DEFAULT_SPEC_PATH)
    spec = json.loads(Path(spec_path).read_text())

    result = run(spec)
    print(f"eagle-runner: review {spec.get('review_id')} {result['status']}", flush=True)
    if result["status"] == "failed":
        print(f"eagle-runner: {result['error']}", file=sys.stderr, flush=True)
    try:
        deliver(spec, result)
    except RunError as exc:
        print(f"eagle-runner: {exc}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
