from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eagle.config import Settings
from eagle.models import ApiToken, AuditLog, Role, User, as_utc, utcnow
from eagle.security import API_TOKEN_PREFIX, SecretBox, hash_api_token

SESSION_USER_KEY = "uid"
# last_used_at is written at most this often per token, not on every request.
TOKEN_USE_RESOLUTION = timedelta(minutes=1)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessionmaker() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_secret_box(request: Request) -> SecretBox:
    return request.app.state.box


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Box = Annotated[SecretBox, Depends(get_secret_box)]


# Declares the Bearer scheme in the OpenAPI schema, which gives Swagger UI
# (/docs) its Authorize button. The header itself is parsed by _bearer_token.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="API token",
    description="A personal API token from Account > API tokens (eagle_...).",
)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Authorization header")
    return token.strip()


async def _token_user(token: str, db: AsyncSession) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or expired API token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token.startswith(API_TOKEN_PREFIX):
        raise unauthorized
    record = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_api_token(token)))
    now = utcnow()
    if record is None or (record.expires_at and as_utc(record.expires_at) <= now):
        raise unauthorized
    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise unauthorized
    last_used = as_utc(record.last_used_at)
    if last_used is None or now - last_used >= TOKEN_USE_RESOLUTION:
        record.last_used_at = now
        await db.commit()
    return user


async def current_user(
    request: Request,
    db: DbSession,
    _: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """The signed in user: from an API token (Authorization: Bearer) or the session.

    A request that sends a token is judged by the token alone, never by a
    session cookie it may also carry.
    """
    token = _bearer_token(request)
    if token is not None:
        request.state.auth_method = "token"
        return await _token_user(token, db)
    request.state.auth_method = "session"
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def session_user(request: Request, user: CurrentUser) -> User:
    """A user signed in through the browser session, not an API token.

    Used for account security actions (tokens, password): a leaked token must
    not be able to mint more tokens or lock its owner out.
    """
    if request.state.auth_method != "session":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This action needs a browser session, not an API token"
        )
    return user


SessionUser = Annotated[User, Depends(session_user)]


def require_roles(*roles: Role):
    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
# Roles allowed to submit and cancel reviews.
SubmitterUser = Annotated[User, Depends(require_roles(Role.ADMIN, Role.MEMBER))]


def audit(
    db: AsyncSession,
    actor: User,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id,
            actor_username=actor.username,
            action=action,
            target=target,
            details=details,
        )
    )
