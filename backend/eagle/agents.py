"""Builds the command line, environment and config files for each agent CLI.

`{HOME}` in env values, argv and file contents is replaced by the runner with the
per-run home directory. Paths in `files` are relative to that home directory.
CLI flags target the versions pinned in agent-runner/Containerfile.
"""

import json
from dataclasses import dataclass, field
from typing import Literal

from eagle.credentials import AGENT_CREDENTIAL_KINDS, ResolvedCredential
from eagle.models import AgentKind, CredentialKind

OutputKind = Literal["claude_json", "file", "stdout"]

CLAUDE_ALLOWED_TOOLS = (
    "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git status)"
)
CLAUDE_DISALLOWED_TOOLS = "Edit,Write,NotebookEdit,WebFetch,WebSearch"
CODEX_OUTPUT_FILE = "{HOME}/codex-last-message.md"
VERTEX_SA_FILE = ".config/gcloud/eagle-sa.json"


class AgentConfigError(ValueError):
    pass


@dataclass
class AgentInvocation:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None
    output: OutputKind = "stdout"
    output_file: str | None = None
    # False when the CLI's cost figure would be wrong (it prices with its vendor's rates).
    report_cost: bool = True

    def to_dict(self) -> dict:
        return {
            "argv": self.argv,
            "env": self.env,
            "files": self.files,
            "stdin": self.stdin,
            "output": self.output,
            "output_file": self.output_file,
            "report_cost": self.report_cost,
        }


def build_invocation(
    agent: AgentKind, model: str | None, credential: ResolvedCredential, prompt: str
) -> AgentInvocation:
    if credential.kind not in AGENT_CREDENTIAL_KINDS[agent]:
        raise AgentConfigError(f"Credential type {credential.kind} cannot be used with {agent}")
    match agent:
        case AgentKind.CLAUDE:
            return _claude(model, credential, prompt)
        case AgentKind.CODEX:
            return _codex(model, credential, prompt)
        case AgentKind.OPENCODE:
            return _opencode(model, credential, prompt)
    raise AgentConfigError(f"Unknown agent {agent}")


def _vertex_env(c: ResolvedCredential) -> tuple[dict[str, str], dict[str, str]]:
    files = {VERTEX_SA_FILE: c.secret["service_account_json"]}
    env = {"GOOGLE_APPLICATION_CREDENTIALS": "{HOME}/" + VERTEX_SA_FILE}
    return env, files


def _claude(model: str | None, c: ResolvedCredential, prompt: str) -> AgentInvocation:
    argv = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--allowedTools",
        CLAUDE_ALLOWED_TOOLS,
        "--disallowedTools",
        CLAUDE_DISALLOWED_TOOLS,
    ]
    if model:
        argv += ["--model", model]

    env: dict[str, str] = {}
    files: dict[str, str] = {}
    report_cost = True
    match c.kind:
        case CredentialKind.ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = c.secret["api_key"]
            if c.public.get("base_url"):
                env["ANTHROPIC_BASE_URL"] = c.public["base_url"]
        case CredentialKind.ANTHROPIC_COMPATIBLE:
            env = _anthropic_gateway_env(model, c)
            report_cost = False
        case CredentialKind.CLAUDE_SUBSCRIPTION:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = c.secret["oauth_token"]
        case CredentialKind.VERTEX_AI:
            env, files = _vertex_env(c)
            env |= {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": c.public["project_id"],
                "CLOUD_ML_REGION": c.public["region"],
            }
    return AgentInvocation(
        argv=argv,
        env=env,
        files=files,
        stdin=prompt,
        output="claude_json",
        report_cost=report_cost,
    )


def _anthropic_gateway_env(model: str | None, c: ResolvedCredential) -> dict[str, str]:
    """Point Claude Code at an Anthropic-compatible gateway (e.g. a MaaS).

    Every model alias Claude Code may use internally (opus, sonnet, haiku and the
    small background model) is mapped to a model the gateway serves, so a review
    on, say, GLM never silently calls a different model.
    """
    key_var = (
        "ANTHROPIC_AUTH_TOKEN" if c.public.get("auth_style") == "bearer" else "ANTHROPIC_API_KEY"
    )
    env = {
        "ANTHROPIC_BASE_URL": c.public["base_url"],
        key_var: c.secret["api_key"],
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    }
    if model:
        small = c.public.get("small_model") or model
        env |= {
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": small,
            "ANTHROPIC_SMALL_FAST_MODEL": small,
        }
    return env


def _codex(model: str | None, c: ResolvedCredential, prompt: str) -> AgentInvocation:
    argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        CODEX_OUTPUT_FILE,
    ]
    if model:
        argv += ["-m", model]
    argv.append("-")  # read the prompt from stdin

    env = {"CODEX_HOME": "{HOME}/.codex"}
    files: dict[str, str] = {}
    match c.kind:
        case CredentialKind.OPENAI_API_KEY:
            env["OPENAI_API_KEY"] = c.secret["api_key"]
            if c.public.get("base_url"):
                env["OPENAI_BASE_URL"] = c.public["base_url"]
        case CredentialKind.CHATGPT_SUBSCRIPTION:
            files[".codex/auth.json"] = c.secret["auth_json"]
        case CredentialKind.OPENAI_COMPATIBLE:
            env["EAGLE_PROVIDER_API_KEY"] = c.secret["api_key"]
            name = c.public.get("provider_name") or "Eagle provider"
            files[".codex/config.toml"] = (
                'model_provider = "eagle"\n\n'
                "[model_providers.eagle]\n"
                f"name = {json.dumps(name)}\n"
                f"base_url = {json.dumps(c.public['base_url'])}\n"
                'env_key = "EAGLE_PROVIDER_API_KEY"\n'
                'wire_api = "chat"\n'
            )
    return AgentInvocation(
        argv=argv,
        env=env,
        files=files,
        stdin=prompt,
        output="file",
        output_file=CODEX_OUTPUT_FILE,
    )


_OPENCODE_PROVIDERS = {
    CredentialKind.ANTHROPIC_API_KEY: "anthropic",
    CredentialKind.OPENAI_API_KEY: "openai",
    CredentialKind.VERTEX_AI: "google-vertex",
    CredentialKind.OPENAI_COMPATIBLE: "eagle",
}


def _opencode(model: str | None, c: ResolvedCredential, prompt: str) -> AgentInvocation:
    provider = _OPENCODE_PROVIDERS[c.kind]
    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "deny",
            "webfetch": "deny",
            "bash": {"git diff*": "allow", "git log*": "allow", "git show*": "allow", "*": "deny"},
        },
    }
    env: dict[str, str] = {}
    files: dict[str, str] = {}
    match c.kind:
        case CredentialKind.ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = c.secret["api_key"]
        case CredentialKind.OPENAI_API_KEY:
            env["OPENAI_API_KEY"] = c.secret["api_key"]
        case CredentialKind.VERTEX_AI:
            env, files = _vertex_env(c)
            env |= {
                "GOOGLE_CLOUD_PROJECT": c.public["project_id"],
                "VERTEX_LOCATION": c.public["region"],
            }
        case CredentialKind.OPENAI_COMPATIBLE:
            env["EAGLE_PROVIDER_API_KEY"] = c.secret["api_key"]
            config["provider"] = {
                "eagle": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": c.public.get("provider_name") or "Eagle provider",
                    "options": {
                        "baseURL": c.public["base_url"],
                        "apiKey": "{env:EAGLE_PROVIDER_API_KEY}",
                    },
                    "models": {model: {}} if model else {},
                }
            }
    files[".config/opencode/opencode.json"] = json.dumps(config, indent=2)

    argv = ["opencode", "run"]
    if model:
        argv += ["-m", f"{provider}/{model}"]
    argv.append(prompt)
    return AgentInvocation(argv=argv, env=env, files=files, output="stdout")
