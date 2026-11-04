from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eagle.deps import AdminUser, DbSession, audit
from eagle.models import Role, User
from eagle.schemas import UserCreate, UserOut, UserUpdate
from eagle.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


async def _other_active_admins(db: AsyncSession, exclude_id: int) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(True), User.id != exclude_id)
    )


@router.get("", response_model=list[UserOut])
async def list_users(_: AdminUser, db: DbSession) -> list[User]:
    return list(await db.scalars(select(User).order_by(User.username)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, db: DbSession) -> User:
    if await db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(
        username=body.username,
        email=body.email,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    audit(db, admin, "user.created", target=body.username, details={"role": body.role})
    await db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DbSession) -> User:
    user = await _get_user_or_404(db, user_id)

    loses_admin = (body.role is not None and body.role != Role.ADMIN) or body.is_active is False
    if user.role == Role.ADMIN and loses_admin and not await _other_active_admins(db, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last active admin")

    changes: dict[str, object] = {}
    if body.email is not None:
        user.email = body.email
        changes["email"] = body.email
    if body.role is not None:
        user.role = body.role
        changes["role"] = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        changes["is_active"] = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        changes["password"] = "reset"

    audit(db, admin, "user.updated", target=user.username, details=changes)
    await db.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: AdminUser, db: DbSession) -> None:
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account")
    if user.role == Role.ADMIN and not await _other_active_admins(db, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last active admin")
    await db.delete(user)
    audit(db, admin, "user.deleted", target=user.username)
    await db.commit()
