"""Tracked PRs (poller targets) and the audit log."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from eagle.deps import AdminUser, CurrentUser, DbSession, SubmitterUser, audit
from eagle.models import AuditLog, TrackedPR
from eagle.schemas import AuditPage, TrackedPROut, TrackedPRUpdate

router = APIRouter(tags=["tracking"])


@router.get("/tracked-prs", response_model=list[TrackedPROut])
async def list_tracked(
    _: CurrentUser, db: DbSession, include_closed: bool = False
) -> list[TrackedPR]:
    query = select(TrackedPR).order_by(TrackedPR.created_at.desc())
    if not include_closed:
        query = query.where(TrackedPR.closed.is_(False))
    return list(await db.scalars(query))


@router.patch("/tracked-prs/{tracked_id}", response_model=TrackedPROut)
async def update_tracked(
    tracked_id: int, body: TrackedPRUpdate, user: SubmitterUser, db: DbSession
) -> TrackedPR:
    row = await db.get(TrackedPR, tracked_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tracked PR not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    audit(db, user, "tracked_pr.updated", target=row.pr_url, details=changes)
    await db.commit()
    return row


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    _: AdminUser,
    db: DbSession,
    action: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.startswith(action))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.scalars(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return AuditPage(items=list(rows), total=total or 0)
