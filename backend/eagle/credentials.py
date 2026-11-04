"""Inference credential types, their fields, and which agents can use them."""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from eagle.config import Settings
from eagle.models import AgentKind, CredentialKind, InferenceCredential
from eagle.security import SecretBox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    secret: bool
    required: bool = True
    # When set, the value must be one of these (the UI renders a dropdown).
    choices: tuple[str, ...] | None = None
    help: str | None = None


# How an Anthropic-compatible gateway expects the key:
# "x-api-key" -> ANTHROPIC_API_KEY, "bearer" -> ANTHROPIC_AUTH_TOKEN.
AUTH_STYLES = ("x-api-key", "bearer")

CREDENTIAL_FIELDS: dict[CredentialKind, list[FieldSpec]] = {
    CredentialKind.ANTHROPIC_API_KEY: [
        FieldSpec("api_key", "API key", secret=True),
        FieldSpec("base_url", "Base URL (gateway, optional)", secret=False, required=False),
    ],
    CredentialKind.ANTHROPIC_COMPATIBLE: [
        FieldSpec(
            "base_url",
            "Base URL",
            secret=False,
            help="Root of the gateway, without /v1 (e.g. https://api.example.com).",
        ),
        FieldSpec("api_key", "API key", secret=True),
        FieldSpec(
            "auth_style",
            "Send the key as",
            secret=False,
            required=False,
            choices=AUTH_STYLES,
            help="x-api-key (ANTHROPIC_API_KEY) or Authorization: Bearer (ANTHROPIC_AUTH_TOKEN).",
        ),
        FieldSpec(
            "small_model",
            "Background model (optional)",
            secret=False,
            required=False,
            help="Used for Claude Code's small background calls. Defaults to the review model.",
        ),
    ],
    CredentialKind.CLAUDE_SUBSCRIPTION: [
        FieldSpec("oauth_token", "OAuth token from `claude setup-token`", secret=True),
    ],
    CredentialKind.VERTEX_AI: [
        FieldSpec("service_account_json", "Service account JSON", secret=True),
        FieldSpec("project_id", "GCP project ID", secret=False),
        FieldSpec("region", "Region", secret=False),
    ],
    CredentialKind.OPENAI_API_KEY: [
        FieldSpec("api_key", "API key", secret=True),
        FieldSpec("base_url", "Base URL (optional)", secret=False, required=False),
    ],
    CredentialKind.CHATGPT_SUBSCRIPTION: [
        FieldSpec("auth_json", "Contents of ~/.codex/auth.json", secret=True),
    ],
    CredentialKind.OPENAI_COMPATIBLE: [
        FieldSpec("api_key", "API key", secret=True),
        FieldSpec(
            "base_url",
            "Base URL (OpenAI-compatible, e.g. MaaS)",
            secret=False,
            help="Include the version path, e.g. https://api.example.com/v1.",
        ),
        FieldSpec("provider_name", "Provider display name", secret=False, required=False),
    ],
}

# Personal subscriptions shared by a team service may conflict with provider terms.
SUBSCRIPTION_KINDS = {CredentialKind.CLAUDE_SUBSCRIPTION, CredentialKind.CHATGPT_SUBSCRIPTION}

AGENT_CREDENTIAL_KINDS: dict[AgentKind, set[CredentialKind]] = {
    AgentKind.CLAUDE: {
        CredentialKind.ANTHROPIC_API_KEY,
        CredentialKind.ANTHROPIC_COMPATIBLE,
        CredentialKind.CLAUDE_SUBSCRIPTION,
        CredentialKind.VERTEX_AI,
    },
    AgentKind.CODEX: {
        CredentialKind.OPENAI_API_KEY,
        CredentialKind.CHATGPT_SUBSCRIPTION,
        CredentialKind.OPENAI_COMPATIBLE,
    },
    AgentKind.OPENCODE: {
        CredentialKind.ANTHROPIC_API_KEY,
        CredentialKind.OPENAI_API_KEY,
        CredentialKind.VERTEX_AI,
        CredentialKind.OPENAI_COMPATIBLE,
    },
}


class CredentialError(ValueError):
    pass


def split_fields(
    kind: CredentialKind,
    values: dict[str, Any],
    existing_secret: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate submitted fields and split them into (secret, public) dicts.

    On update, secret fields that are omitted or empty keep their existing value,
    so the UI never needs to read secrets back.
    """
    specs = {f.name: f for f in CREDENTIAL_FIELDS[kind]}
    unknown = set(values) - set(specs)
    if unknown:
        raise CredentialError(f"Unknown fields for {kind}: {', '.join(sorted(unknown))}")

    secret: dict[str, str] = {}
    public: dict[str, str] = {}
    for spec in specs.values():
        raw = values.get(spec.name)
        value = raw.strip() if isinstance(raw, str) else raw
        if not value and spec.secret and existing_secret and spec.name in existing_secret:
            value = existing_secret[spec.name]
        if not value:
            if spec.required:
                raise CredentialError(f"Field {spec.name!r} is required")
            continue
        if not isinstance(value, str):
            raise CredentialError(f"Field {spec.name!r} must be a string")
        if spec.choices and value not in spec.choices:
            raise CredentialError(f"Field {spec.name!r} must be one of: {', '.join(spec.choices)}")
        (secret if spec.secret else public)[spec.name] = value

    if kind == CredentialKind.VERTEX_AI:
        _check_json(secret["service_account_json"], "service_account_json")
    if kind == CredentialKind.CHATGPT_SUBSCRIPTION:
        _check_json(secret["auth_json"], "auth_json")
    for url_field in ("base_url",):
        url = public.get(url_field)
        if url and not url.startswith(("https://", "http://")):
            raise CredentialError(f"Field {url_field!r} must be an http(s) URL")
        if url:
            url = url.rstrip("/")
            if kind == CredentialKind.ANTHROPIC_COMPATIBLE and url.endswith("/v1"):
                # Claude Code appends /v1/messages itself.
                url = url.removesuffix("/v1")
            public[url_field] = url
    return secret, public


def _check_json(value: str, field: str) -> None:
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        raise CredentialError(f"Field {field!r} must be valid JSON") from exc


def secret_box(settings: Settings) -> SecretBox:
    if settings.encryption_key:
        return SecretBox(settings.encryption_key)
    # Dev fallback: deterministic key derived from the session secret.
    digest = hashlib.sha256(b"eagle-encryption:" + settings.secret_key.encode()).digest()
    return SecretBox(base64.urlsafe_b64encode(digest).decode())


def encrypt_json(box: SecretBox, data: dict[str, str]) -> str:
    return box.encrypt(json.dumps(data))


def decrypt_json(box: SecretBox, token: str) -> dict[str, str]:
    return json.loads(box.decrypt(token))


@dataclass(frozen=True)
class ResolvedCredential:
    kind: CredentialKind
    secret: dict[str, str]
    public: dict[str, str]


def resolve(box: SecretBox, credential: InferenceCredential) -> ResolvedCredential:
    return ResolvedCredential(
        kind=credential.kind,
        secret=decrypt_json(box, credential.encrypted_secret),
        public=dict(credential.public_config or {}),
    )
