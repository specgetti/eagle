"""Background worker: dispatches queued reviews, watches running ones, finalizes
finished ones (PR comment, notifications) and polls tracked PRs for new commits.

Several worker replicas can run at once: every state change is a conditional
UPDATE on the review's current status, so a review is only claimed once.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from eagle.agents import AgentConfigError, build_invocation
from eagle.config import Settings
from eagle.credentials import resolve
from eagle.executors.base import Executor, RunSpec
from eagle.github import GitHubError, PullRequestRef
from eagle.models import (
    ACTIVE_REVIEW_STATUSES,
    TERMINAL_REVIEW_STATUSES,
    AgentConfig,
    InferenceCredential,
    NotificationChannel,
    Review,
    ReviewStatus,
    ReviewTrigger,
    TrackedPR,
    User,
    as_utc,
    utcnow,
)
from eagle.notify import NotificationError, Notifier, ReviewEvent
from eagle.prompt import PromptContext, build_prompt
from eagle.runner.progress import describe_model
from eagle.security import SecretBox
from eagle.services import (
    apply_result,
    fail_review,
    get_platform_settings,
    github_client_for,
    hash_token,
    matching_repo_settings,
)

logger = logging.getLogger(__name__)
FINALIZE_BATCH = 50


class StartError(Exception):
    pass


def pr_ref(review: Review | TrackedPR) -> PullRequestRef:
    owner, repo = review.repo.split("/", 1)
    return PullRequestRef(host=review.git_host, owner=owner, repo=repo, number=review.pr_number)


def review_url(settings: Settings, review_id: int) -> str:
    return f"{settings.public_url.rstrip('/')}/reviews/{review_id}"


def comment_body(review: Review, settings: Settings) -> str:
    used = review.reported_model or review.model
    model = f" ({used})" if used else ""
    sha = f" at `{review.head_sha[:10]}`" if review.head_sha else ""
    header = (
        f"## Eagle review\n\n_Reviewed by {review.agent}{model}{sha}. "
        f"[View in Eagle]({review_url(settings, review.id)})_\n\n"
    )
    footer = f"\n\n**Verdict:** `{review.verdict}`" if review.verdict else ""
    return header + (review.result_markdown or "") + footer


class Worker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        settings: Settings,
        executor: Executor,
        box: SecretBox,
        http_transport: httpx.AsyncBaseTransport | None = None,
        notifier: Notifier | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sm = sessionmaker
        self._settings = settings
        self._executor = executor
        self._box = box
        self._transport = http_transport
        self._notifier = notifier or Notifier(box, http_transport=http_transport)
        self._clock = clock

    async def tick(self) -> None:
        for step in (self.reap, self.dispatch, self.finalize, self.poll):
            try:
                await step()
            except Exception:
                logger.exception("Worker step %s failed", step.__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("Worker started (executor=%s)", type(self._executor).__name__)
        while not stop.is_set():
            await self.tick()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._settings.worker_interval_seconds)
        await self._executor.aclose()

    # Dispatch

    async def dispatch(self) -> None:
        async with self._sm() as db:
            platform = await get_platform_settings(db)
            running = await db.scalar(
                select(func.count())
                .select_from(Review)
                .where(Review.status == ReviewStatus.RUNNING)
            )
            slots = platform.max_concurrent_reviews - (running or 0)
            if slots <= 0:
                return
            queued = list(
                await db.scalars(
                    select(Review.id)
                    .where(Review.status == ReviewStatus.QUEUED)
                    .order_by(Review.created_at, Review.id)
                    .limit(slots)
                )
            )

        for review_id in queued:
            async with self._sm() as db:
                claimed = await db.execute(
                    update(Review)
                    .where(Review.id == review_id, Review.status == ReviewStatus.QUEUED)
                    .values(status=ReviewStatus.RUNNING, started_at=self._clock())
                )
                await db.commit()
            if claimed.rowcount != 1:
                continue
            try:
                await self._start(review_id)
            except (StartError, AgentConfigError, GitHubError) as exc:
                await self._fail(review_id, str(exc))
            except Exception as exc:
                logger.exception("Could not start review %s", review_id)
                await self._fail(review_id, f"Could not start review: {exc}")

    async def _fail(self, review_id: int, error: str) -> None:
        async with self._sm() as db:
            await fail_review(db, review_id, error)

    async def _start(self, review_id: int) -> None:
        async with self._sm() as db:
            review = await db.get(Review, review_id)
            config = await db.get(AgentConfig, review.agent)
            if config is None or not config.enabled:
                raise StartError(f"Agent {review.agent} is disabled")

            client, git_token = await github_client_for(
                db, review.git_host, self._box, self._transport
            )
            async with client:
                info = await client.get_pull(pr_ref(review))
            review.title = info.title[:512]
            review.author = info.author
            review.head_sha = info.head_sha
            review.base_ref = info.base_ref

            repo_settings = await matching_repo_settings(db, review.repo)
            prompt = build_prompt(
                PromptContext(
                    repo=review.repo,
                    pr_number=review.pr_number,
                    title=info.title,
                    author=info.author,
                    base_ref=info.base_ref,
                    agent_instructions=config.instructions,
                    repo_instructions=[r.instructions for r in repo_settings if r.instructions],
                    custom_prompt=review.custom_prompt,
                    since_sha=review.since_sha,
                )
            )

            if self._executor.requires_credentials:
                if config.credential_id is None:
                    raise StartError(f"No inference credential is configured for {review.agent}")
                credential = await db.get(InferenceCredential, config.credential_id)
                invocation = build_invocation(
                    review.agent, review.model, resolve(self._box, credential), prompt
                ).to_dict()
            else:
                invocation = {"argv": ["mock"], "stdin": prompt}

            spec = RunSpec(
                review_id=review.id,
                clone_url=info.clone_url,
                pr_number=review.pr_number,
                base_ref=info.base_ref,
                head_sha=info.head_sha,
                git_token=git_token,
                invocation=invocation,
                timeout_seconds=config.timeout_seconds,
                agent=review.agent,
                model=review.model,
                model_source=review.model_source,
                prompt=prompt,
            )
            started = f"{review.agent}, {describe_model(review.model, review.model_source)}"

            callback = None
            if self._executor.uses_callback:
                token = secrets.token_urlsafe(32)
                review.callback_token_hash = hash_token(token)
                base = self._settings.callback_base_url.rstrip("/")
                callback = {
                    "url": f"{base}/api/v1/internal/reviews/{review.id}/result",
                    "token": token,
                }
            # Persist metadata (and the callback hash) before the runner can report back.
            await db.commit()

            ref = await self._executor.launch(spec, callback)
            await db.execute(update(Review).where(Review.id == review_id).values(executor_ref=ref))
            await db.commit()
            status = await db.scalar(select(Review.status).where(Review.id == review_id))
        if status == ReviewStatus.CANCELLED:
            # Cancelled while launching: finalize may already have run without a ref.
            await self._executor.cancel(ref)
            await self._executor.cleanup(ref)
            return
        logger.info("Started review %s (%s) as %s", review_id, started, ref)

    # Reap

    async def reap(self) -> None:
        async with self._sm() as db:
            running = list(
                await db.scalars(select(Review).where(Review.status == ReviewStatus.RUNNING))
            )
            timeouts = {c.agent: c.timeout_seconds for c in await db.scalars(select(AgentConfig))}

        now = self._clock()
        for review in running:
            limit = timeouts.get(review.agent, 1800) + self._settings.timeout_grace_seconds
            started = as_utc(review.started_at) or now
            if started + timedelta(seconds=limit) < now:
                if review.executor_ref:
                    try:
                        await self._executor.cancel(review.executor_ref)
                    except Exception:
                        logger.exception("Could not stop timed out review %s", review.id)
                async with self._sm() as db:
                    await fail_review(
                        db,
                        review.id,
                        f"Review timed out after {limit} seconds",
                        from_status=(ReviewStatus.RUNNING,),
                    )
                continue

            if not review.executor_ref:
                continue  # still being launched
            try:
                state = await self._executor.status(review.executor_ref)
            except Exception:
                logger.exception("Could not get status of review %s", review.id)
                continue

            if state.state in ("finished", "failed"):
                elapsed = (now - started).total_seconds()
                outcome = (state.result or {}).get("status") if state.state == "finished" else None
                logger.info(
                    "Review %s %s after %.0fs%s",
                    review.id,
                    outcome or state.state,
                    elapsed,
                    f": {state.error}" if state.error else "",
                )
            async with self._sm() as db:
                if state.state == "finished":
                    if state.result is not None:
                        await apply_result(db, review.id, state.result)
                    else:
                        await fail_review(
                            db,
                            review.id,
                            "Runner finished without reporting a result",
                            from_status=(ReviewStatus.RUNNING,),
                        )
                elif state.state == "failed":
                    await fail_review(
                        db,
                        review.id,
                        state.error or "Runner failed",
                        from_status=(ReviewStatus.RUNNING,),
                    )

    # Finalize

    async def finalize(self) -> None:
        async with self._sm() as db:
            reviews = list(
                await db.scalars(
                    select(Review)
                    .where(
                        Review.status.in_(TERMINAL_REVIEW_STATUSES), Review.finalized_at.is_(None)
                    )
                    .order_by(Review.id)
                    .limit(FINALIZE_BATCH)
                )
            )
            for review in reviews:
                errors: list[str] = []
                await self._release(review)
                if review.status == ReviewStatus.SUCCEEDED:
                    if review.post_comment:
                        await self._post_comment(db, review, errors)
                    await self._update_tracking(db, review)
                if review.status in (ReviewStatus.SUCCEEDED, ReviewStatus.FAILED):
                    await self._notify(db, review, errors)
                review.delivery_errors = errors or None
                review.finalized_at = self._clock()
                await db.commit()

    async def _release(self, review: Review) -> None:
        if not review.executor_ref:
            return
        try:
            if review.status == ReviewStatus.CANCELLED:
                await self._executor.cancel(review.executor_ref)
            await self._executor.cleanup(review.executor_ref)
        except Exception:
            logger.exception("Could not clean up run for review %s", review.id)

    async def _post_comment(self, db, review: Review, errors: list[str]) -> None:
        try:
            client, _ = await github_client_for(db, review.git_host, self._box, self._transport)
            async with client:
                review.posted_comment_url = await client.post_comment(
                    pr_ref(review), comment_body(review, self._settings)
                )
        except GitHubError as exc:
            errors.append(f"PR comment: {exc}")

    async def _update_tracking(self, db, review: Review) -> None:
        tracked = await db.scalar(select(TrackedPR).where(TrackedPR.pr_url == review.pr_url))
        if tracked is not None and review.head_sha:
            tracked.last_reviewed_sha = review.head_sha
            tracked.latest_review_id = review.id

    async def _notify(self, db, review: Review, errors: list[str]) -> None:
        event_name = (
            "review.succeeded" if review.status == ReviewStatus.SUCCEEDED else "review.failed"
        )
        channels = [
            c
            for c in await db.scalars(
                select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
            )
            if event_name in (c.events or [])
        ]
        if not channels:
            return
        submitter = await db.get(User, review.submitted_by_id) if review.submitted_by_id else None
        event = ReviewEvent(
            event=event_name,
            review_id=review.id,
            repo=review.repo,
            pr_number=review.pr_number,
            pr_url=review.pr_url,
            title=review.title,
            agent=review.agent,
            model=review.reported_model or review.model,
            verdict=review.verdict,
            summary=review.summary,
            error=review.error,
            review_url=review_url(self._settings, review.id),
            submitter_email=submitter.email if submitter else None,
        )
        for channel in channels:
            try:
                await self._notifier.send(channel, event)
            except NotificationError as exc:
                errors.append(f"{channel.name}: {exc}")
            except Exception as exc:
                logger.exception("Notification channel %s failed", channel.name)
                errors.append(f"{channel.name}: {exc}")

    # Poller

    async def poll(self) -> None:
        async with self._sm() as db:
            platform = await get_platform_settings(db)
            if not platform.poller_enabled:
                return
            tracked_prs = list(
                await db.scalars(
                    select(TrackedPR).where(
                        TrackedPR.enabled.is_(True), TrackedPR.closed.is_(False)
                    )
                )
            )
            now = self._clock()
            for tracked in tracked_prs:
                interval = tracked.poll_interval_minutes or platform.poll_interval_minutes
                last = as_utc(tracked.last_polled_at)
                if last and last + timedelta(minutes=interval) > now:
                    continue
                tracked.last_polled_at = now
                await self._poll_one(db, tracked)
                await db.commit()

    async def _poll_one(self, db, tracked: TrackedPR) -> None:
        try:
            client, _ = await github_client_for(db, tracked.git_host, self._box, self._transport)
            async with client:
                info = await client.get_pull(pr_ref(tracked))
        except GitHubError as exc:
            logger.warning("Polling %s failed: %s", tracked.pr_url, exc)
            return

        if not info.is_open:
            tracked.closed = True
            return
        # Only re-review PRs that had a successful review and got new commits since.
        if not tracked.last_reviewed_sha or info.head_sha == tracked.last_reviewed_sha:
            return
        active = await db.scalar(
            select(Review.id).where(
                Review.pr_url == tracked.pr_url, Review.status.in_(ACTIVE_REVIEW_STATUSES)
            )
        )
        if active:
            return
        template = (
            await db.get(Review, tracked.latest_review_id) if tracked.latest_review_id else None
        )
        if template is None:
            return
        db.add(
            Review(
                pr_url=tracked.pr_url,
                git_host=tracked.git_host,
                repo=tracked.repo,
                pr_number=tracked.pr_number,
                title=info.title[:512],
                author=info.author,
                head_sha=info.head_sha,
                submitted_by_id=template.submitted_by_id,
                trigger=ReviewTrigger.POLL,
                previous_review_id=template.id,
                since_sha=tracked.last_reviewed_sha,
                agent=template.agent,
                model=template.model,
                model_source=template.model_source,
                custom_prompt=template.custom_prompt,
                post_comment=template.post_comment,
                status=ReviewStatus.QUEUED,
            )
        )
        logger.info("Queued re-review of %s at %s", tracked.pr_url, info.head_sha)
