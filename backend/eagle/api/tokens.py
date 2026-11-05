"""Personal API tokens for scripts, CI jobs and bots.

A token acts as its owner with the owner's current role (a viewer's token can
only read). Creating and revoking tokens needs a browser session, so a leaked
token cannot mint more. Admins can list and revoke every user's tokens.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from eagle.deps import DbSession, SessionUser, audit
from eagle.models import ApiToken, Role, User, utcnow
from eagle.schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenOut
from eagle.security import API_TOKEN_DISPLAY_LENGTH, generate_api_token, hash_api_token

router = APIRouter(prefix="/tokens", tags=["tokens"])

MAX_TOKENS_PER_USER = 25


def _out(token: ApiToken, username: str | None) -> ApiTokenOut:
    return ApiTokenOut.model_validate(token).model_copy(update={"username": username})


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(user: SessionUser, db: DbSession, all: bool = False) -> list[ApiTokenOut]:
    """Your tokens; with all=true (admins only) every user's tokens."""
    query = select(ApiToken, User.username).join(User, User.id == ApiToken.user_id)
    if all:
        if user.role != Role.ADMIN:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    else:
        query = query.where(ApiToken.user_id == user.id)
    rows = await db.execute(query.order_by(ApiToken.created_at.desc(), ApiToken.id.desc()))
    return [_out(token, username) for token, username in rows.all()]


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(body: ApiTokenCreate, user: SessionUser, db: DbSession) -> ApiTokenCreated:
    count = await db.scalar(
        select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)
    )
    if count >= MAX_TOKENS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have {MAX_TOKENS_PER_USER} tokens; revoke one first",
        )
    secret = generate_api_token()
    token = ApiToken(
        user_id=user.id,
        name=body.name,
        prefix=secret[:API_TOKEN_DISPLAY_LENGTH],
        token_hash=hash_api_token(secret),
        expires_at=utcnow() + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None,
        created_at=utcnow(),
    )
    db.add(token)
    audit(
        db,
        user,
        "token.created",
        target=user.username,
        details={
            "name": body.name,
            "prefix": token.prefix,
            "expires_in_days": body.expires_in_days,
        },
    )
    await db.commit()
    return ApiTokenCreated(**_out(token, user.username).model_dump(), token=secret)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: int, user: SessionUser, db: DbSession) -> None:
    token = await db.get(ApiToken, token_id)
    # Someone else's token looks the same as a missing one, unless you are an admin.
    if token is None or (token.user_id != user.id and user.role != Role.ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    owner = await db.get(User, token.user_id)
    await db.delete(token)
    audit(
        db,
        user,
        "token.revoked",
        target=owner.username if owner else None,
        details={"name": token.name, "prefix": token.prefix},
    )
    await db.commit()
