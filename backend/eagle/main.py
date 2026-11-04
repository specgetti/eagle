import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from eagle import __version__
from eagle.api import api_router
from eagle.api.health import router as health_router
from eagle.api.metrics import router as metrics_router
from eagle.bootstrap import bootstrap
from eagle.config import Settings, get_settings
from eagle.credentials import secret_box
from eagle.db import Database
from eagle.notify import Notifier
from eagle.oidc import OIDCClient
from eagle.providers import ProviderClient

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Serves the React app, falling back to index.html for client-side routes."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith(("api/", "api")):
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    if settings.secret_key == "dev-insecure-change-me":
        logger.warning("EAGLE_SECRET_KEY is not set. Do not use this configuration in production.")
    if not settings.encryption_key:
        logger.warning(
            "EAGLE_ENCRYPTION_KEY is not set; deriving one from the secret key (dev only)."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        app.state.db = db
        if settings.auto_create_schema:
            from eagle.migrate import upgrade_database

            await upgrade_database(db.engine)
        async with db.sessionmaker() as session:
            await bootstrap(session, settings)
        yield
        await db.dispose()

    app = FastAPI(title="Eagle", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.box = secret_box(settings)
    app.state.notifier = Notifier(app.state.box)
    app.state.providers = ProviderClient()
    app.state.oidc = OIDCClient(settings) if settings.oidc_enabled else None

    # SameSite=strict keeps cross-site requests from carrying the session, which
    # covers CSRF for this JSON API. Revisit when SSO (cross-site redirects) lands.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="eagle_session",
        max_age=settings.session_max_age_seconds,
        # SSO needs "lax": the IdP redirect back is a cross-site navigation. Lax still
        # withholds the cookie on cross-site POST/PUT/PATCH/DELETE.
        same_site="lax" if settings.oidc_enabled else "strict",
        https_only=settings.cookie_secure,
    )

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(api_router)

    if settings.frontend_dist and Path(settings.frontend_dist).is_dir():
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="ui")

    return app
