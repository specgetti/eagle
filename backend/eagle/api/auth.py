from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from eagle.deps import SESSION_USER_KEY, AppSettings, CurrentUser, DbSession, audit
from eagle.models import User
from eagle.schemas import LoginRequest, PasswordChange, UserOut
from eagle.security import burn_password_check, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, request: Request, db: DbSession, settings: AppSettings) -> User:
    if not settings.local_login_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password login is disabled; use SSO")
    user = await db.scalar(select(User).where(User.username == body.username))
    if user is None:
        burn_password_check(body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if not verify_password(user.password_hash, body.password) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    audit(db, user, "auth.login")
    await db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    request.session.clear()


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChange, user: CurrentUser, db: DbSession) -> None:
    if user.auth_source != "local":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SSO accounts have no password")
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "auth.password_changed", target=user.username)
    await db.commit()
