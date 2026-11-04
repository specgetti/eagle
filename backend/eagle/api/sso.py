import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from eagle.deps import SESSION_USER_KEY, AppSettings, DbSession, audit
from eagle.models import User
from eagle.oidc import OIDCError, pkce_pair, role_from_claims
from eagle.schemas import AuthConfigOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
OIDC_SESSION_KEY = "oidc"
# Argon2 never produces this, so SSO accounts cannot log in with a password.
NO_PASSWORD = "!oidc"


@router.get("/config", response_model=AuthConfigOut)
async def auth_config(settings: AppSettings) -> dict:
    """What the login page should offer. Public."""
    return {
        "local_login": settings.local_login_enabled,
        "oidc": {"enabled": settings.oidc_enabled, "label": settings.oidc_button_label},
    }


def _fail(message: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={quote(message)}", status_code=status.HTTP_302_FOUND)


@router.get("/oidc/login")
async def oidc_login(request: Request, settings: AppSettings) -> RedirectResponse:
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not configured")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    try:
        url = await request.app.state.oidc.authorization_url(state, nonce, challenge)
    except OIDCError as exc:
        logger.warning("SSO login failed: %s", exc)
        return _fail("Single sign-on is unavailable right now")
    request.session[OIDC_SESSION_KEY] = {"state": state, "nonce": nonce, "verifier": verifier}
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    pending = request.session.pop(OIDC_SESSION_KEY, None)
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO is not configured")
    if error:
        return _fail(f"Sign-in was not completed ({error})")
    if not pending or not code or not state or not secrets.compare_digest(state, pending["state"]):
        return _fail("Sign-in session expired, please try again")

    try:
        claims = await request.app.state.oidc.exchange(code, pending["verifier"], pending["nonce"])
    except OIDCError as exc:
        logger.warning("SSO callback failed: %s", exc)
        return _fail("Could not verify your identity")

    username = str(claims.get(settings.oidc_username_claim) or "").strip()
    if not username or len(username) > 64:
        return _fail("Your account has no usable username")
    role = role_from_claims(claims, settings)
    if role is None:
        return _fail("Your account is not in a group allowed to use Eagle")

    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            username=username,
            email=claims.get("email"),
            password_hash=NO_PASSWORD,
            role=role,
            auth_source="oidc",
        )
        db.add(user)
        await db.flush()
        audit(db, user, "user.created_sso", target=username, details={"role": role})
    elif user.auth_source != "oidc":
        # Never let an SSO identity take over a local account with the same name.
        return _fail("A local account with this username exists; ask an admin")
    elif not user.is_active:
        return _fail("Your account is disabled")
    else:
        if user.role != role:
            audit(db, user, "user.role_synced", target=username, details={"role": role})
        user.role = role
        user.email = claims.get("email") or user.email

    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    audit(db, user, "auth.login_sso")
    await db.commit()
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
