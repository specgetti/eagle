from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment-level configuration, read from EAGLE_* environment variables.

    Everything a team admin can change (agents, models, credentials, notifications,
    poller, concurrency) is stored in the database and managed from the admin panel.
    """

    model_config = SettingsConfigDict(env_prefix="EAGLE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./eagle.db"
    # Run Alembic migrations on startup. Production runs `eagle migrate` in an initContainer.
    auto_create_schema: bool = True

    # Signs session cookies. Must be a long random value in production.
    secret_key: str = "dev-insecure-change-me"
    # Fernet key that encrypts stored credentials. Derived from secret_key if unset (dev only).
    encryption_key: str | None = None

    session_max_age_seconds: int = 8 * 3600
    cookie_secure: bool = False

    bootstrap_admin_username: str = "admin"
    # If unset, a random password is generated and logged once on first start.
    bootstrap_admin_password: str | None = None

    # Hosts accepted in PR URLs, in addition to hosts configured in the admin panel.
    # Local username/password login. Keep it on as a break-glass path when SSO is enabled.
    local_login_enabled: bool = True

    # OIDC single sign-on (Red Hat SSO / Keycloak or any OIDC provider). Enabled when
    # issuer and client id are set. Redirect URI: <public_url>/api/v1/auth/oidc/callback
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid email profile"
    oidc_button_label: str = "Log in with SSO"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"
    # Group membership decides the role on every login, most privileged match wins.
    oidc_admin_groups: list[str] = []
    oidc_member_groups: list[str] = []
    oidc_viewer_groups: list[str] = []
    # Role for SSO users in none of the groups above. "none" denies access.
    oidc_default_role: Literal["admin", "member", "viewer", "none"] = "viewer"

    allowed_git_hosts: list[str] = ["github.com"]

    # External URL of the app, used for links in notifications and PR comments.
    public_url: str = "http://localhost:5173"

    # Directory with the built React UI. Served at "/" when present.
    frontend_dist: str | None = None

    # Worker / execution
    executor: Literal["local", "kubernetes", "mock"] = "local"
    worker_interval_seconds: float = 5.0
    # Extra time on top of an agent's timeout before the worker gives up on a run.
    timeout_grace_seconds: int = 120
    local_workdir: str | None = None

    # Kubernetes executor
    instance_name: str = "eagle"
    k8s_namespace: str = "eagle"
    runner_image: str = "quay.io/hmoghani/eagle-agent-runner:v0.1.0"
    runner_service_account: str = "eagle-runner"
    runner_cpu_limit: str = "2"
    runner_memory_limit: str = "4Gi"
    # In-cluster URL of eagle-web that runner pods report results to.
    callback_base_url: str = "http://eagle-web:8000"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
