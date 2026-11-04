"""OIDC authorization code flow with PKCE, for Red Hat SSO / Keycloak and similar."""

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from eagle.config import Settings
from eagle.models import Role

ROLE_ORDER = (Role.ADMIN, Role.MEMBER, Role.VIEWER)
METADATA_TTL = 3600


class OIDCError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def role_from_claims(claims: dict[str, Any], settings: Settings) -> Role | None:
    raw = claims.get(settings.oidc_groups_claim) or []
    groups = {raw} if isinstance(raw, str) else {str(g) for g in raw}
    # Keycloak can emit full paths ("/team/admins"); accept both forms.
    groups |= {g.rsplit("/", 1)[-1] for g in groups}
    mapping = {
        Role.ADMIN: settings.oidc_admin_groups,
        Role.MEMBER: settings.oidc_member_groups,
        Role.VIEWER: settings.oidc_viewer_groups,
    }
    for role in ROLE_ORDER:
        if groups & set(mapping[role]):
            return role
    return None if settings.oidc_default_role == "none" else Role(settings.oidc_default_role)


class OIDCClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0

    @property
    def redirect_uri(self) -> str:
        return f"{self._settings.public_url.rstrip('/')}/api/v1/auth/oidc/callback"

    async def _get_json(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=self._transport, timeout=15) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise OIDCError(f"Could not reach identity provider: {exc}") from exc
        if resp.status_code != 200:
            raise OIDCError(f"Identity provider returned {resp.status_code} for {url}")
        return resp.json()

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None or time.monotonic() - self._metadata_at > METADATA_TTL:
            issuer = self._settings.oidc_issuer.rstrip("/")
            self._metadata = await self._get_json(f"{issuer}/.well-known/openid-configuration")
            self._metadata_at = time.monotonic()
        return self._metadata

    async def authorization_url(self, state: str, nonce: str, code_challenge: str) -> str:
        meta = await self.metadata()
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self._settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def exchange(self, code: str, verifier: str, nonce: str) -> dict[str, Any]:
        """Exchange the code for tokens and return verified ID token claims."""
        meta = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "code_verifier": verifier,
        }
        if self._settings.oidc_client_secret:
            data["client_secret"] = self._settings.oidc_client_secret
        async with httpx.AsyncClient(transport=self._transport, timeout=15) as client:
            try:
                resp = await client.post(meta["token_endpoint"], data=data)
            except httpx.HTTPError as exc:
                raise OIDCError(f"Token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise OIDCError(f"Token endpoint returned {resp.status_code}")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise OIDCError("No id_token in token response")
        claims = await self._verify(id_token, meta)
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("ID token nonce mismatch")
        return claims

    async def _verify(self, id_token: str, meta: dict[str, Any]) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OIDCError(f"Malformed ID token: {exc}") from exc
        if header.get("alg") in (None, "none") or str(header.get("alg")).startswith("HS"):
            raise OIDCError("Unsupported ID token algorithm")
        jwks = jwt.PyJWKSet.from_dict(await self._get_json(meta["jwks_uri"]))
        kid = header.get("kid")
        candidates = [k for k in jwks.keys if kid is None or k.key_id == kid]
        if not candidates:
            raise OIDCError("No matching signing key for ID token")
        try:
            return jwt.decode(
                id_token,
                key=candidates[0],
                algorithms=[header["alg"]],
                audience=self._settings.oidc_client_id,
                issuer=meta.get("issuer", self._settings.oidc_issuer),
                options={"require": ["exp", "iat", "sub"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise OIDCError(f"Invalid ID token: {exc}") from exc
