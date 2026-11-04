"""Talk to inference providers directly, for admin tooling only.

Reviews never go through here (the agent CLIs call the models). This module backs
the admin panel's "Test" button and the model dropdowns: it checks a credential with
a cheap request and lists the models an endpoint serves.
"""

import json
import re
import time
from dataclasses import dataclass, field

import httpx
import jwt

from eagle.credentials import ResolvedCredential
from eagle.models import CredentialKind

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_API = "https://api.openai.com/v1"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
VERTEX_ANTHROPIC_VERSION = "vertex-2023-10-16"
MAX_MODEL_PAGES = 10
ERROR_DETAIL_CHARS = 300

# Offered when an endpoint cannot list its models. Claude Code also accepts the
# short aliases, which always resolve to the current model of that tier.
CLAUDE_SUGGESTIONS = [
    "opus",
    "sonnet",
    "haiku",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
]
SUGGESTED_MODELS: dict[CredentialKind, list[str]] = {
    CredentialKind.CLAUDE_SUBSCRIPTION: CLAUDE_SUGGESTIONS,
    CredentialKind.VERTEX_AI: CLAUDE_SUGGESTIONS,
}


class ProviderError(Exception):
    """A provider request failed; the message is safe to show an admin."""


@dataclass
class TestResult:
    # None means "could not be checked from here" (not a failure).
    ok: bool | None
    message: str
    latency_ms: int | None = None
    models_found: int | None = None


@dataclass
class ModelList:
    models: list[str] = field(default_factory=list)
    # "provider": listed by the endpoint. "suggested": built-in list, endpoint can't list.
    source: str = "provider"
    message: str | None = None


class ProviderClient:
    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20
    ) -> None:
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    # Model listing

    async def list_models(self, cred: ResolvedCredential) -> ModelList:
        match cred.kind:
            case CredentialKind.ANTHROPIC_API_KEY | CredentialKind.ANTHROPIC_COMPATIBLE:
                return await self._anthropic_models(cred)
            case CredentialKind.OPENAI_API_KEY | CredentialKind.OPENAI_COMPATIBLE:
                return ModelList(models=await self._openai_models(cred))
        return ModelList(
            models=list(SUGGESTED_MODELS.get(cred.kind, [])),
            source="suggested",
            message="This credential type cannot list models; showing common choices.",
        )

    async def _anthropic_models(self, cred: ResolvedCredential) -> ModelList:
        base = _anthropic_base(cred)
        headers = _anthropic_headers(cred)
        models: list[str] = []
        params: dict[str, str | int] = {"limit": 1000}
        async with self._client() as client:
            for _ in range(MAX_MODEL_PAGES):
                resp = await _send(client, "GET", f"{base}/v1/models", cred, headers, params)
                if (
                    resp.status_code in (404, 405)
                    and cred.kind == CredentialKind.ANTHROPIC_COMPATIBLE
                ):
                    return ModelList(
                        source="suggested",
                        message="This gateway does not list models; type the model name.",
                    )
                data = _json_or_raise(resp, cred)
                models += _ids(data)
                if not data.get("has_more") or not data.get("last_id"):
                    break
                params = {"limit": 1000, "after_id": data["last_id"]}
        return ModelList(models=_unique_sorted(models))

    async def _openai_models(self, cred: ResolvedCredential) -> list[str]:
        async with self._client() as client:
            resp = await _send(
                client, "GET", f"{_openai_base(cred)}/models", cred, _openai_headers(cred)
            )
            try:
                return _unique_sorted(_ids(_json_or_raise(resp, cred)))
            except ProviderError as exc:
                await self._raise_with_v1_hint(client, cred, exc)
                raise

    async def _raise_with_v1_hint(
        self, client: httpx.AsyncClient, cred: ResolvedCredential, exc: ProviderError
    ) -> None:
        """OpenAI-style base URLs include the version (".../v1"). A common mistake is
        leaving it out; if the endpoint answers under /v1, say exactly what to change."""
        base = _openai_base(cred)
        if not cred.public.get("base_url") or _VERSIONED.search(base):
            return
        try:
            probe = await client.get(f"{base}/v1/models", headers=_openai_headers(cred))
            data = probe.json() if probe.status_code < 400 else None
        except (httpx.HTTPError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            raise ProviderError(
                f"The API is at {base}/v1, not {base}. "
                f"Edit the credential and set the base URL to {base}/v1."
            ) from exc

    # Connection test

    async def test(self, cred: ResolvedCredential, model: str | None = None) -> TestResult:
        started = time.monotonic()
        try:
            result = await self._test(cred, model)
        except ProviderError as exc:
            result = TestResult(ok=False, message=str(exc))
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result

    async def _test(self, cred: ResolvedCredential, model: str | None) -> TestResult:
        match cred.kind:
            case CredentialKind.CLAUDE_SUBSCRIPTION | CredentialKind.CHATGPT_SUBSCRIPTION:
                return _check_subscription_format(cred)
            case CredentialKind.VERTEX_AI:
                return await self._test_vertex(cred, model)

        if model:
            await self._ping_model(cred, model)
            return TestResult(ok=True, message=f"The endpoint answered a request to {model}.")
        listed = await self.list_models(cred)
        if listed.source == "suggested":
            return TestResult(
                ok=None,
                message="Reached the endpoint, but it does not list models. "
                "Pick a model and test again to send a real request.",
            )
        return TestResult(
            ok=True,
            message=f"Key accepted; the endpoint serves {len(listed.models)} model(s).",
            models_found=len(listed.models),
        )

    async def _ping_model(self, cred: ResolvedCredential, model: str) -> None:
        """Send the smallest possible real request to the model."""
        async with self._client() as client:
            if cred.kind in (CredentialKind.ANTHROPIC_API_KEY, CredentialKind.ANTHROPIC_COMPATIBLE):
                url = f"{_anthropic_base(cred)}/v1/messages"
                headers = _anthropic_headers(cred)
                body = {"model": model, "max_tokens": 1, "messages": [_PING]}
            elif cred.kind == CredentialKind.OPENAI_API_KEY:
                url = f"{_openai_base(cred)}/responses"
                headers = _openai_headers(cred)
                body = {"model": model, "input": "ping", "max_output_tokens": 16}
            else:
                url = f"{_openai_base(cred)}/chat/completions"
                headers = _openai_headers(cred)
                body = {"model": model, "max_tokens": 1, "messages": [_PING]}
            resp = await _send(client, "POST", url, cred, headers, json_body=body)
            try:
                _json_or_raise(resp, cred)
            except ProviderError as exc:
                if cred.kind in (CredentialKind.OPENAI_API_KEY, CredentialKind.OPENAI_COMPATIBLE):
                    await self._raise_with_v1_hint(client, cred, exc)
                raise

    async def _test_vertex(self, cred: ResolvedCredential, model: str | None) -> TestResult:
        token = await self._vertex_token(cred)
        if not model:
            return TestResult(
                ok=True,
                message="The service account is valid. Pick a model and test again "
                "to check that it can call Claude in this project and region.",
            )
        project, region = cred.public["project_id"], cred.public["region"]
        host = (
            "aiplatform.googleapis.com"
            if region == "global"
            else f"{region}-aiplatform.googleapis.com"
        )
        url = (
            f"https://{host}/v1/projects/{project}/locations/{region}"
            f"/publishers/anthropic/models/{model}:rawPredict"
        )
        body = {
            "anthropic_version": VERTEX_ANTHROPIC_VERSION,
            "max_tokens": 1,
            "messages": [_PING],
        }
        async with self._client() as client:
            resp = await _send(
                client, "POST", url, cred, {"Authorization": f"Bearer {token}"}, json_body=body
            )
            _json_or_raise(resp, cred)
        return TestResult(ok=True, message=f"Vertex AI answered a request to {model}.")

    async def _vertex_token(self, cred: ResolvedCredential) -> str:
        try:
            sa = json.loads(cred.secret["service_account_json"])
            email, key = sa["client_email"], sa["private_key"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderError(
                "The service account JSON is missing client_email or private_key"
            ) from exc
        token_uri = sa.get("token_uri") or GOOGLE_TOKEN_URI
        if not token_uri.startswith("https://"):
            raise ProviderError("The service account token_uri must be https")
        now = int(time.time())
        claims = {
            "iss": email,
            "scope": VERTEX_SCOPE,
            "aud": token_uri,
            "iat": now,
            "exp": now + 600,
        }
        try:
            assertion = jwt.encode(
                claims, key, algorithm="RS256", headers={"kid": sa.get("private_key_id", "")}
            )
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            raise ProviderError("The service account private_key could not be read") from exc
        async with self._client() as client:
            resp = await _send(
                client,
                "POST",
                token_uri,
                cred,
                {},
                form={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            data = _json_or_raise(resp, cred)
        if not data.get("access_token"):
            raise ProviderError("Google did not return an access token")
        return data["access_token"]


_PING = {"role": "user", "content": "ping"}
# A base URL ending in a version segment such as /v1 or /v1beta.
_VERSIONED = re.compile(r"/v\d+[a-z0-9]*$")


def _anthropic_base(cred: ResolvedCredential) -> str:
    return (cred.public.get("base_url") or ANTHROPIC_API).rstrip("/")


def _anthropic_headers(cred: ResolvedCredential) -> dict[str, str]:
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if cred.public.get("auth_style") == "bearer":
        headers["Authorization"] = f"Bearer {cred.secret['api_key']}"
    else:
        headers["x-api-key"] = cred.secret["api_key"]
    return headers


def _openai_base(cred: ResolvedCredential) -> str:
    return (cred.public.get("base_url") or OPENAI_API).rstrip("/")


def _openai_headers(cred: ResolvedCredential) -> dict[str, str]:
    return {"Authorization": f"Bearer {cred.secret['api_key']}"}


async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    cred: ResolvedCredential,
    headers: dict[str, str],
    params: dict | None = None,
    json_body: dict | None = None,
    form: dict | None = None,
) -> httpx.Response:
    try:
        return await client.request(
            method, url, headers=headers, params=params, json=json_body, data=form
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(f"Timed out connecting to {_host(url)}") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(_redact(f"Could not reach {_host(url)}: {exc}", cred)) from exc


def _json_or_raise(resp: httpx.Response, cred: ResolvedCredential) -> dict:
    if resp.status_code >= 400:
        raise ProviderError(_describe_error(resp, cred))
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError(
            f"{_host(str(resp.request.url))} returned a non-JSON response "
            f"(HTTP {resp.status_code}); check the base URL"
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError("Unexpected response shape from the provider")
    return data


def _describe_error(resp: httpx.Response, cred: ResolvedCredential) -> str:
    if _is_html(resp):
        # A proxy or router page (e.g. OpenShift's 503 "Application is not available"):
        # the URL does not point at the API, and the page itself is noise.
        return (
            f"HTTP {resp.status_code}: {_host(str(resp.request.url))} returned a web page "
            "instead of an API response. Check the base URL."
        )
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or "")
        elif isinstance(err, str):
            detail = body.get("error_description") or err
        elif isinstance(body, dict):
            detail = str(body.get("message") or body.get("detail") or "")
    except ValueError:
        detail = resp.text
    hint = {
        401: "the key was rejected",
        403: "the key is not allowed to do this",
        404: "not found (check the base URL and model name)",
        429: "rate limited",
    }.get(resp.status_code, "request failed")
    message = f"HTTP {resp.status_code}: {hint}"
    if detail.strip():
        message += f" ({detail.strip()[:ERROR_DETAIL_CHARS]})"
    return _redact(message, cred)


def _is_html(resp: httpx.Response) -> bool:
    if "html" in resp.headers.get("content-type", ""):
        return True
    return resp.text.lstrip()[:15].lower().startswith(("<!doctype html", "<html"))


def _check_subscription_format(cred: ResolvedCredential) -> TestResult:
    if cred.kind == CredentialKind.CHATGPT_SUBSCRIPTION:
        try:
            data = json.loads(cred.secret["auth_json"])
        except json.JSONDecodeError:
            return TestResult(ok=False, message="auth.json is not valid JSON")
        if not isinstance(data, dict) or not (data.get("tokens") or data.get("OPENAI_API_KEY")):
            return TestResult(ok=False, message="auth.json has no tokens; run `codex login` again")
    return TestResult(
        ok=None,
        message="Subscription tokens can only be used by the agent CLI, so they cannot be "
        "tested from here. The format looks right; run a review to confirm.",
    )


def _ids(data: dict) -> list[str]:
    items = data.get("data")
    if not isinstance(items, list):
        raise ProviderError("The models response has no 'data' list")
    return [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]


def _unique_sorted(models: list[str]) -> list[str]:
    return sorted(set(models))


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host or url
    except Exception:  # noqa: BLE001
        return url


def _redact(text: str, cred: ResolvedCredential) -> str:
    for value in cred.secret.values():
        if value and len(value) >= 8:
            text = text.replace(value, "***")
    return text
