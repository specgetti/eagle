from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from eagle.config import Settings
from eagle.models import AuditLog, Role, User
from eagle.security import SecretBox

SESSION_USER_KEY = "uid"


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


async def current_user(request: Request, db: DbSession) -> User:
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


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
