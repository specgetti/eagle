from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eagle.models import AgentKind, ChannelKind, CredentialKind, ReviewStatus, ReviewTrigger, Role

CUSTOM_PROMPT_MAX_LENGTH = 8000
INSTRUCTIONS_MAX_LENGTH = 20000
RESULT_MAX_LENGTH = 1_000_000

Username = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")]
Password = Annotated[str, Field(min_length=12, max_length=256)]
ModelName = Annotated[str, Field(min_length=1, max_length=128)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Auth and users


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(ORM):
    id: int
    username: str
    email: str | None
    role: Role
    is_active: bool
    auth_source: Literal["local", "oidc"] = "local"
    created_at: datetime


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # Omitted: the token never expires.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class ApiTokenOut(ORM):
    id: int
    name: str
    prefix: str
    username: str | None = None
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiTokenCreated(ApiTokenOut):
    # The token itself. Returned once, at creation; only its hash is stored.
    token: str


class UserCreate(BaseModel):
    username: Username
    password: Password
    email: str | None = None
    role: Role = Role.VIEWER


class UserUpdate(BaseModel):
    email: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: Password | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: Password


# Reviews


class ReviewCreate(BaseModel):
    pr_url: str = Field(max_length=512)
    # Omitted: repo default, then platform default.
    agent: AgentKind | None = None
    # Omitted: the agent's default model.
    model: str | None = Field(default=None, max_length=128)
    custom_prompt: str | None = Field(default=None, max_length=CUSTOM_PROMPT_MAX_LENGTH)
    # Omitted: repo setting, then platform default.
    post_comment: bool | None = None

    @field_validator("custom_prompt")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ReviewOut(ORM):
    id: int
    pr_url: str
    git_host: str
    repo: str
    pr_number: int
    head_sha: str | None
    title: str | None
    author: str | None
    submitted_by_id: int | None
    trigger: ReviewTrigger
    previous_review_id: int | None
    agent: AgentKind
    model: str | None
    post_comment: bool
    status: ReviewStatus
    summary: str | None
    verdict: str | None
    cost_usd: float | None
    error: str | None
    posted_comment_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    submitted_by: str | None = None


class ReviewDetail(ReviewOut):
    custom_prompt: str | None
    since_sha: str | None
    base_ref: str | None
    result_markdown: str | None
    token_usage: dict[str, Any] | None
    delivery_errors: list[str] | None


class ReviewPage(BaseModel):
    items: list[ReviewOut]
    total: int


class RunnerResult(BaseModel):
    status: Literal["succeeded", "failed"]
    markdown: str | None = Field(default=None, max_length=RESULT_MAX_LENGTH)
    verdict: str | None = Field(default=None, max_length=32)
    summary: str | None = Field(default=None, max_length=5000)
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    head_sha: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=20000)


class OIDCConfigOut(BaseModel):
    enabled: bool
    label: str


class AuthConfigOut(BaseModel):
    local_login: bool
    oidc: OIDCConfigOut


# Agents


class AgentOption(BaseModel):
    """What the submit form needs: enabled agents and their models."""

    agent: AgentKind
    default_model: str | None
    allowed_models: list[str]
    configured: bool


class AgentConfigOut(ORM):
    agent: AgentKind
    enabled: bool
    default_model: str | None
    allowed_models: list[str]
    credential_id: int | None
    instructions: str | None
    timeout_seconds: int
    compatible_credential_kinds: list[CredentialKind] = []


class AgentConfigUpdate(BaseModel):
    enabled: bool | None = None
    default_model: str | None = Field(default=None, max_length=128)
    allowed_models: list[ModelName] | None = Field(default=None, max_length=50)
    credential_id: int | None = None
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX_LENGTH)
    timeout_seconds: int | None = Field(default=None, ge=60, le=7200)


# Credentials


class CredentialFieldOut(BaseModel):
    name: str
    label: str
    secret: bool
    required: bool
    choices: list[str] | None = None
    help: str | None = None


class CredentialTypeOut(BaseModel):
    kind: CredentialKind
    fields: list[CredentialFieldOut]
    agents: list[AgentKind]
    subscription: bool


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: CredentialKind
    values: dict[str, Any]


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Secret fields left out or empty keep their stored value.
    values: dict[str, Any] = {}


class CredentialTestRequest(BaseModel):
    # Optional: also send a one-token request to this model.
    model: str | None = Field(default=None, max_length=128)


class CredentialTestOut(BaseModel):
    ok: bool | None  # None: cannot be checked from the server
    message: str
    latency_ms: int | None = None
    models_found: int | None = None


class ModelListOut(BaseModel):
    models: list[str]
    source: Literal["provider", "suggested"]
    message: str | None = None


class CredentialOut(BaseModel):
    id: int
    name: str
    kind: CredentialKind
    public_config: dict[str, Any]
    secret_fields: dict[str, str]  # field -> masked value
    used_by: list[AgentKind]
    created_at: datetime
    updated_at: datetime


# Repo settings, git hosts


class RepoSettingIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.*?/-]+$")
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX_LENGTH)
    default_agent: AgentKind | None = None
    post_comment: bool | None = None


class RepoSettingOut(ORM, RepoSettingIn):
    id: int
    created_at: datetime


class GitHostIn(BaseModel):
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    api_url: str | None = Field(default=None, max_length=512)
    # Omitted on update keeps the stored token; empty string removes it.
    token: str | None = None


class GitHostOut(BaseModel):
    id: int
    host: str
    api_url: str
    has_token: bool
    created_at: datetime


# Notifications


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: ChannelKind
    enabled: bool = True
    events: list[Literal["review.succeeded", "review.failed"]] = [
        "review.succeeded",
        "review.failed",
    ]
    config: dict[str, Any]


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    events: list[Literal["review.succeeded", "review.failed"]] | None = None
    config: dict[str, Any] | None = None


class ChannelOut(BaseModel):
    id: int
    name: str
    kind: ChannelKind
    enabled: bool
    events: list[str]
    public_config: dict[str, Any]
    secret_fields: dict[str, str]
    created_at: datetime


# Tracking, audit


class TrackedPROut(ORM):
    id: int
    pr_url: str
    repo: str
    pr_number: int
    enabled: bool
    poll_interval_minutes: int | None
    closed: bool
    last_reviewed_sha: str | None
    latest_review_id: int | None
    last_polled_at: datetime | None


class TrackedPRUpdate(BaseModel):
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class AuditOut(ORM):
    id: int
    actor_username: str
    action: str
    target: str | None
    details: dict[str, Any] | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
