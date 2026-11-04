from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select

from eagle.deps import DbSession
from eagle.models import Review, ReviewStatus

router = APIRouter(tags=["health"])


@router.get("/metrics", include_in_schema=False)
async def metrics(db: DbSession) -> Response:
    """Review counts from the database. Computed per scrape so web and worker
    replicas always agree."""
    registry = CollectorRegistry()
    reviews = Gauge("eagle_reviews", "Reviews by status", ["status", "agent"], registry=registry)
    cost = Gauge(
        "eagle_review_cost_usd", "Total reported cost in USD", ["agent"], registry=registry
    )

    rows = await db.execute(
        select(
            Review.status, Review.agent, func.count(), func.coalesce(func.sum(Review.cost_usd), 0)
        ).group_by(Review.status, Review.agent)
    )
    totals: dict[str, float] = {}
    for status, agent, count, spent in rows:
        reviews.labels(status=str(status), agent=str(agent)).set(count)
        totals[str(agent)] = totals.get(str(agent), 0.0) + float(spent or 0)
    for agent, spent in totals.items():
        cost.labels(agent=agent).set(spent)
    queued = await db.scalar(
        select(func.count()).select_from(Review).where(Review.status == ReviewStatus.QUEUED)
    )
    Gauge("eagle_queue_depth", "Reviews waiting to start", registry=registry).set(queued or 0)
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
