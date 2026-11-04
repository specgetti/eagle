import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


async def upgrade_database(engine: AsyncEngine) -> None:
    def _upgrade(connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )


async def wait_for_database(
    engine: AsyncEngine,
    timeout: float = 300,
    interval: float = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Block until the database accepts connections.

    On a fresh install the database pod starts at the same time as the web pod,
    whose `eagle migrate` init container would otherwise fail and crash loop
    until PostgreSQL is up.
    """
    waited = 0.0
    while True:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return
        except (OSError, SQLAlchemyError) as exc:
            if waited >= timeout:
                raise RuntimeError(
                    f"Database is not reachable after {timeout:.0f}s: {type(exc).__name__}"
                ) from exc
            logger.info("Waiting for the database (%s)", type(exc).__name__)
        await sleep(interval)
        waited += interval


async def wait_for_schema(
    engine: AsyncEngine,
    timeout: float = 600,
    interval: float = 5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Block until the database schema is at this release's head revision.

    Only the web pods run migrations (an init container on OpenShift). A worker
    that starts first, on a fresh install or during an upgrade, waits here
    instead of crashing on missing tables. It never migrates itself, so two
    processes can never race on the same migration.
    """
    head = head_revision()
    waited = 0.0
    while True:
        try:
            revision = await current_revision(engine)
            state = f"at {revision or 'no schema'}"
        except (OSError, SQLAlchemyError) as exc:
            revision, state = None, f"unreachable ({type(exc).__name__})"
        if revision == head:
            return
        if waited >= timeout:
            raise RuntimeError(
                f"Database schema is {state}, this release needs {head}. "
                "Run `eagle migrate` (the web pods do this on start)."
            )
        logger.info("Waiting for database migrations: schema is %s, need %s", state, head)
        await sleep(interval)
        waited += interval
