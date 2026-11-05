import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from eagle.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return as_utc(value).astimezone(UTC) if value is not None else None

    def process_result_value(self, value, dialect):
        return as_utc(value)


def _enum(cls: type[enum.Enum]) -> Enum:
    # Store enum values ("admin"), not member names ("ADMIN").
    return Enum(
        cls,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        name=cls.__name__.lower(),
    )


def _ts() -> Mapped[datetime]:
    return mapped_column(UTCDateTime(), default=utcnow)


class Role(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class ReviewStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_REVIEW_STATUSES = (ReviewStatus.QUEUED, ReviewStatus.RUNNING)
TERMINAL_REVIEW_STATUSES = (ReviewStatus.SUCCEEDED, ReviewStatus.FAILED, ReviewStatus.CANCELLED)


class ReviewTrigger(enum.StrEnum):
    MANUAL = "manual"
    RERUN = "rerun"
    POLL = "poll"


class AgentKind(enum.StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"


class CredentialKind(enum.StrEnum):
    ANTHROPIC_API_KEY = "anthropic_api_key"
    # Any gateway speaking the Anthropic Messages API (e.g. a MaaS serving Opus or GLM).
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"
    CLAUDE_SUBSCRIPTION = "claude_subscription"
    VERTEX_AI = "vertex_ai"
    OPENAI_API_KEY = "openai_api_key"
    CHATGPT_SUBSCRIPTION = "chatgpt_subscription"
    OPENAI_COMPATIBLE = "openai_compatible"


class ChannelKind(enum.StrEnum):
    SLACK = "slack"
    SMTP = "smtp"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role))
    # "local" (password) or "oidc" (SSO; no usable password, role synced from groups).
    auth_source: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = _ts()


class ApiToken(Base):
    """A personal access token: acts as its user, with the user's current role.

    Only a SHA-256 hash of the token is stored; the token itself is shown once.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    # The first characters of the token ("eagle_AbCd12"), to recognise it in lists.
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _ts()


class InferenceCredential(Base):
    __tablename__ = "inference_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[CredentialKind] = mapped_column(_enum(CredentialKind))
    # Fernet-encrypted JSON of the secret fields (keys, tokens, service account JSON).
    encrypted_secret: Mapped[str] = mapped_column(Text)
    # Non-secret fields (project id, region, base URL), safe to return from the API.
    public_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AgentConfig(Base):
    """Admin settings for one agent: on/off, default model, credential, instructions."""

    __tablename__ = "agent_configs"

    agent: Mapped[AgentKind] = mapped_column(_enum(AgentKind), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    default_model: Mapped[str | None] = mapped_column(String(128))
    # Models users may pick. Empty list means any model name is accepted.
    allowed_models: Mapped[list[str]] = mapped_column(JSON, default=list)
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("inference_credentials.id", ondelete="SET NULL")
    )
    instructions: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)


class RepoSetting(Base):
    __tablename__ = "repo_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # fnmatch pattern against "owner/repo", e.g. "opendatahub-io/*".
    pattern: Mapped[str] = mapped_column(String(255), unique=True)
    instructions: Mapped[str | None] = mapped_column(Text)
    default_agent: Mapped[AgentKind | None] = mapped_column(_enum(AgentKind))
    # None inherits the platform default.
    post_comment: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = _ts()


class GitHost(Base):
    """GitHub or GitHub Enterprise host with an optional access token."""

    __tablename__ = "git_hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), unique=True)
    api_url: Mapped[str] = mapped_column(String(512))
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind))
    enabled: Mapped[bool] = mapped_column(default=True)
    # Events: "review.succeeded", "review.failed".
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    encrypted_secret: Mapped[str] = mapped_column(Text)
    public_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    pr_url: Mapped[str] = mapped_column(String(512), index=True)
    git_host: Mapped[str] = mapped_column(String(255))
    repo: Mapped[str] = mapped_column(String(255), index=True)  # "owner/name"
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    base_ref: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(512))
    author: Mapped[str | None] = mapped_column(String(255))

    submitted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    trigger: Mapped[ReviewTrigger] = mapped_column(
        _enum(ReviewTrigger), default=ReviewTrigger.MANUAL
    )
    previous_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("reviews.id", ondelete="SET NULL")
    )
    # For incremental re-reviews: the commit that was reviewed last time.
    since_sha: Mapped[str | None] = mapped_column(String(64))

    agent: Mapped[AgentKind] = mapped_column(_enum(AgentKind))
    model: Mapped[str | None] = mapped_column(String(128))
    # Where `model` came from: "requested" (the submitter picked it), "agent_default"
    # (the agent's default model) or None (no model: the agent CLI picks its own).
    model_source: Mapped[str | None] = mapped_column(String(16))
    # The model the agent said it used, from its own output (Claude Code's init event,
    # the Codex header). None when the agent does not report it.
    reported_model: Mapped[str | None] = mapped_column(String(128))
    # Optional extra context from the submitter, appended to the review instructions.
    custom_prompt: Mapped[str | None] = mapped_column(Text)
    post_comment: Mapped[bool] = mapped_column(default=False)

    status: Mapped[ReviewStatus] = mapped_column(
        _enum(ReviewStatus), default=ReviewStatus.QUEUED, index=True
    )
    result_markdown: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(32))
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    posted_comment_url: Mapped[str | None] = mapped_column(String(512))
    # Problems posting comments or sending notifications (does not fail the review).
    delivery_errors: Mapped[list[str] | None] = mapped_column(JSON)

    executor_ref: Mapped[str | None] = mapped_column(String(255))
    callback_token_hash: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Set once comment posting, notifications and PR tracking are done.
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)


class TrackedPR(Base):
    """A PR the poller watches for new commits (only when polling is enabled)."""

    __tablename__ = "tracked_prs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pr_url: Mapped[str] = mapped_column(String(512), unique=True)
    git_host: Mapped[str] = mapped_column(String(255))
    repo: Mapped[str] = mapped_column(String(255))
    pr_number: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Overrides the platform poll interval when set.
    poll_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    closed: Mapped[bool] = mapped_column(default=False)
    last_reviewed_sha: Mapped[str | None] = mapped_column(String(64))
    latest_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("reviews.id", ondelete="SET NULL")
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
