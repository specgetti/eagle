from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import func, select

from eagle.deps import AppSettings, CurrentUser, DbSession, SubmitterUser, audit
from eagle.github import parse_pr_url
from eagle.models import (
    ACTIVE_REVIEW_STATUSES,
    AgentConfig,
    Review,
    ReviewStatus,
    ReviewTrigger,
    Role,
    User,
    utcnow,
)
from eagle.schemas import (
    AgentOption,
    ReviewCreate,
    ReviewDetail,
    ReviewOut,
    ReviewPage,
    RunnerResult,
)
from eagle.services import (
    ReviewOptionsError,
    allowed_git_hosts,
    apply_result,
    resolve_review_options,
    token_matches,
    track_pr,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])
internal_router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


async def _detail(db, review: Review) -> ReviewDetail:
    detail = ReviewDetail.model_validate(review)
    if review.submitted_by_id:
        detail.submitted_by = await db.scalar(
            select(User.username).where(User.id == review.submitted_by_id)
        )
    return detail


async def _get_or_404(db, review_id: int) -> Review:
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not found")
    return review


@router.get("/options", response_model=list[AgentOption])
async def review_options(_: CurrentUser, db: DbSession) -> list[AgentOption]:
    """Enabled agents with their default and allowed models, for the submit form."""
    configs = await db.scalars(
        select(AgentConfig).where(AgentConfig.enabled.is_(True)).order_by(AgentConfig.agent)
    )
    return [
        AgentOption(
            agent=c.agent,
            default_model=c.default_model,
            allowed_models=c.allowed_models or [],
            configured=c.credential_id is not None,
        )
        for c in configs
    ]


@router.get("", response_model=ReviewPage)
async def list_reviews(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    repo: str | None = None,
    pr_url: str | None = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewPage:
    query = select(Review)
    if status_filter is not None:
        query = query.where(Review.status == status_filter)
    if repo:
        query = query.where(Review.repo.ilike(f"%{repo.strip()}%"))
    if pr_url:
        query = query.where(Review.pr_url == pr_url)
    if mine:
        query = query.where(Review.submitted_by_id == user.id)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.scalars(
        query.order_by(Review.created_at.desc(), Review.id.desc()).limit(limit).offset(offset)
    )
    items = [ReviewOut.model_validate(r) for r in rows]
    ids = {i.submitted_by_id for i in items if i.submitted_by_id}
    if ids:
        names = dict(
            (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()
        )
        for item in items:
            item.submitted_by = names.get(item.submitted_by_id)
    return ReviewPage(items=items, total=total or 0)


@router.post("", response_model=ReviewDetail, status_code=status.HTTP_201_CREATED)
async def create_review(
    body: ReviewCreate, user: SubmitterUser, db: DbSession, settings: AppSettings
) -> ReviewDetail:
    try:
        ref = parse_pr_url(body.pr_url, await allowed_git_hosts(db, settings))
        agent, model, post_comment = await resolve_review_options(
            db, ref.full_name, body.agent, body.model, body.post_comment
        )
    except (ValueError, ReviewOptionsError) as exc:
        raise HTTPException(422, str(exc)) from exc

    review = Review(
        pr_url=ref.canonical_url,
        git_host=ref.host,
        repo=ref.full_name,
        pr_number=ref.number,
        submitted_by_id=user.id,
        trigger=ReviewTrigger.MANUAL,
        agent=agent,
        model=model,
        custom_prompt=body.custom_prompt,
        post_comment=post_comment,
        status=ReviewStatus.QUEUED,
    )
    db.add(review)
    await track_pr(db, review)
    audit(
        db,
        user,
        "review.submitted",
        target=ref.canonical_url,
        details={
            "agent": agent,
            "model": model,
            "has_custom_prompt": body.custom_prompt is not None,
        },
    )
    await db.commit()
    return await _detail(db, review)


@router.get("/{review_id}", response_model=ReviewDetail)
async def get_review(review_id: int, _: CurrentUser, db: DbSession) -> ReviewDetail:
    return await _detail(db, await _get_or_404(db, review_id))


@router.post("/{review_id}/cancel", response_model=ReviewDetail)
async def cancel_review(review_id: int, user: SubmitterUser, db: DbSession) -> ReviewDetail:
    review = await _get_or_404(db, review_id)
    if user.role != Role.ADMIN and review.submitted_by_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the submitter or an admin can cancel")
    if review.status not in ACTIVE_REVIEW_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Review is already {review.status}")

    review.status = ReviewStatus.CANCELLED
    review.finished_at = utcnow()
    review.callback_token_hash = None
    audit(db, user, "review.cancelled", target=review.pr_url)
    await db.commit()
    return await _detail(db, review)


@router.post("/{review_id}/rerun", response_model=ReviewDetail, status_code=201)
async def rerun_review(review_id: int, user: SubmitterUser, db: DbSession) -> ReviewDetail:
    """Queue a fresh review of the same PR with the same agent, model and context."""
    original = await _get_or_404(db, review_id)
    active = await db.scalar(
        select(Review.id).where(
            Review.pr_url == original.pr_url, Review.status.in_(ACTIVE_REVIEW_STATUSES)
        )
    )
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Review {active} of this PR is in progress")
    config = await db.get(AgentConfig, original.agent)
    if config is None or not config.enabled:
        raise HTTPException(422, f"Agent {original.agent} is not enabled")

    review = Review(
        pr_url=original.pr_url,
        git_host=original.git_host,
        repo=original.repo,
        pr_number=original.pr_number,
        submitted_by_id=user.id,
        trigger=ReviewTrigger.RERUN,
        previous_review_id=original.id,
        agent=original.agent,
        model=original.model,
        custom_prompt=original.custom_prompt,
        post_comment=original.post_comment,
        status=ReviewStatus.QUEUED,
    )
    db.add(review)
    await track_pr(db, review)
    audit(db, user, "review.rerun", target=original.pr_url, details={"from": original.id})
    await db.commit()
    return await _detail(db, review)


@internal_router.post("/reviews/{review_id}/result", status_code=status.HTTP_204_NO_CONTENT)
async def runner_result(
    review_id: int,
    body: RunnerResult,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Called by runner pods. Authenticated with a one-time, per-review token."""
    review = await db.get(Review, review_id)
    token = (authorization or "").removeprefix("Bearer ").strip()
    if review is None or not token or not token_matches(token, review.callback_token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid runner token")
    if not await apply_result(db, review_id, body.model_dump()):
        raise HTTPException(status.HTTP_409_CONFLICT, "Review is no longer running")
