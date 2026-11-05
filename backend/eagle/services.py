"""Review logic shared by the API, the worker and the poller."""

import fnmatch
import hashlib
import hmac
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eagle.config import Settings
from eagle.github import GitHubClient, default_api_url
from eagle.models import (
    AgentConfig,
    AgentKind,
    AppSetting,
    GitHost,
    RepoSetting,
    Review,
    ReviewStatus,
    TrackedPR,
    utcnow,
)
from eagle.security import SecretBox

PLATFORM_KEY = "platform"


class PlatformSettings(BaseModel):
    max_concurrent_reviews: int = Field(default=3, ge=1, le=50)
    # The PR poller is off by default; admins opt in.
    poller_enabled: bool = False
    poll_interval_minutes: int = Field(default=15, ge=1, le=1440)
    post_comment_default: bool = False
    default_agent: AgentKind = AgentKind.CLAUDE


async def get_platform_settings(db: AsyncSession) -> PlatformSettings:
    row = await db.get(AppSetting, PLATFORM_KEY)
    return PlatformSettings.model_validate(row.value) if row else PlatformSettings()


async def save_platform_settings(db: AsyncSession, value: PlatformSettings) -> None:
    row = await db.get(AppSetting, PLATFORM_KEY)
    data = value.model_dump(mode="json")
    if row is None:
        db.add(AppSetting(key=PLATFORM_KEY, value=data))
    else:
        row.value = data


async def ensure_agent_configs(db: AsyncSession) -> None:
    existing = set(await db.scalars(select(AgentConfig.agent)))
    for agent in AgentKind:
        if agent not in existing:
            db.add(AgentConfig(agent=agent, enabled=True, allowed_models=[]))


class ReviewOptionsError(ValueError):
    pass


async def matching_repo_settings(db: AsyncSession, repo: str) -> list[RepoSetting]:
    """Repo settings whose pattern matches, least specific first."""
    rows = await db.scalars(select(RepoSetting))
    matches = [r for r in rows if fnmatch.fnmatchcase(repo.lower(), r.pattern.lower())]
    # Fewer wildcards and longer patterns are more specific.
    return sorted(matches, key=lambda r: (-r.pattern.count("*"), len(r.pattern)))


async def resolve_review_options(
    db: AsyncSession,
    repo: str,
    agent: AgentKind | None,
    model: str | None,
    post_comment: bool | None,
) -> tuple[AgentKind, str | None, str | None, bool]:
    """Pick agent, model and comment posting from request, repo settings and defaults.

    Returns (agent, model, model_source, post_comment). model_source says where
    the model came from: "requested", "agent_default", or None when there is no
    model and the agent CLI uses its own default.
    """
    platform = await get_platform_settings(db)
    repo_settings = await matching_repo_settings(db, repo)

    if agent is None:
        repo_default = next(
            (r.default_agent for r in reversed(repo_settings) if r.default_agent), None
        )
        agent = repo_default or platform.default_agent

    config = await db.get(AgentConfig, agent)
    if config is None or not config.enabled:
        raise ReviewOptionsError(f"Agent {agent} is not enabled")

    model = (model or "").strip() or None
    model_source = "requested" if model else None
    if model is None and config.default_model:
        model, model_source = config.default_model, "agent_default"
    if model and config.allowed_models and model not in config.allowed_models:
        raise ReviewOptionsError(
            f"Model {model!r} is not allowed for {agent}. "
            f"Allowed: {', '.join(config.allowed_models)}"
        )

    if post_comment is None:
        repo_choice = next(
            (r.post_comment for r in reversed(repo_settings) if r.post_comment is not None), None
        )
        post_comment = repo_choice if repo_choice is not None else platform.post_comment_default
    return agent, model, model_source, post_comment


async def allowed_git_hosts(db: AsyncSession, settings: Settings) -> list[str]:
    hosts = set(settings.allowed_git_hosts)
    hosts.update(await db.scalars(select(GitHost.host)))
    return sorted(hosts)


async def github_client_for(
    db: AsyncSession, host: str, box: SecretBox, transport=None
) -> tuple[GitHubClient, str | None]:
    """Return a client for the host and its token (also used to clone private repos)."""
    row = await db.scalar(select(GitHost).where(GitHost.host == host))
    token = None
    api_url = default_api_url(host)
    if row is not None:
        api_url = row.api_url
        if row.encrypted_token:
            token = box.decrypt(row.encrypted_token)
    return GitHubClient(api_url, token, transport=transport), token


async def track_pr(db: AsyncSession, review: Review) -> TrackedPR:
    tracked = await db.scalar(select(TrackedPR).where(TrackedPR.pr_url == review.pr_url))
    if tracked is None:
        tracked = TrackedPR(
            pr_url=review.pr_url,
            git_host=review.git_host,
            repo=review.repo,
            pr_number=review.pr_number,
            enabled=True,
        )
        db.add(tracked)
    return tracked


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, token_hash: str | None) -> bool:
    return bool(token_hash) and hmac.compare_digest(hash_token(token), token_hash)


async def apply_result(db: AsyncSession, review_id: int, result: dict[str, Any]) -> bool:
    """Store a runner result. Only applies to running reviews, so callbacks and the
    reaper cannot overwrite each other or a cancellation. Returns True if applied."""
    values: dict[str, Any] = {"finished_at": utcnow(), "callback_token_hash": None}
    if result.get("model"):
        values["reported_model"] = str(result["model"])[:128]
    if result.get("status") == "succeeded" and result.get("markdown"):
        values |= {
            "status": ReviewStatus.SUCCEEDED,
            "result_markdown": result["markdown"],
            "summary": result.get("summary"),
            "verdict": result.get("verdict"),
            "token_usage": result.get("usage"),
            "cost_usd": result.get("cost_usd"),
            "error": None,
        }
        if result.get("head_sha"):
            values["head_sha"] = result["head_sha"]
    else:
        values |= {
            "status": ReviewStatus.FAILED,
            "error": str(result.get("error") or "Runner returned no review")[:10000],
        }
    outcome = await db.execute(
        update(Review)
        .where(Review.id == review_id, Review.status == ReviewStatus.RUNNING)
        .values(**values)
    )
    await db.commit()
    return outcome.rowcount == 1


async def fail_review(db: AsyncSession, review_id: int, error: str, from_status=None) -> bool:
    statuses = from_status or (ReviewStatus.QUEUED, ReviewStatus.RUNNING)
    outcome = await db.execute(
        update(Review)
        .where(Review.id == review_id, Review.status.in_(statuses))
        .values(
            status=ReviewStatus.FAILED,
            error=error[:10000],
            finished_at=utcnow(),
            callback_token_hash=None,
        )
    )
    await db.commit()
    return outcome.rowcount == 1
