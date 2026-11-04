# Agent runner image

Each review runs in its own pod from this image. The worker creates a Job per
review and mounts a per-run Secret at `/var/run/eagle/spec.json` holding:

- the PR to check out (clone URL, PR number, target branch, optional git token)
- the agent command line, environment and config files (built by `eagle/agents.py`)
- a one-time callback URL and token for reporting the result

`python3 -m eagle.runner` then:

1. Fetches `refs/pull/<n>/head` and the target branch. The git token is passed
   through the environment only, never written to `.git/config`.
2. Writes agent config files into a fresh `$HOME` under `/tmp`.
3. Runs the agent CLI (Claude Code, Codex or OpenCode) in read-only mode with a
   minimal environment and a timeout.
4. Parses the review and verdict and POSTs them to eagle-web. Secrets are
   redacted from any error output.

The runner package uses only the Python standard library.

## Build

```bash
podman build --platform linux/amd64 -f agent-runner/Containerfile -t quay.io/hmoghani/eagle-agent-runner:dev \
  --build-arg CLAUDE_CODE_VERSION=<version> \
  --build-arg CODEX_VERSION=<version> \
  --build-arg OPENCODE_VERSION=<version> .
```

Pin the CLI versions: the flags Eagle passes (see `backend/eagle/agents.py`) are
tested against fake CLIs, so check them against the real versions you pin.

## Try it locally

```bash
cd backend
uv run python -m eagle.runner /path/to/spec.json   # writes result_path if set
```
