import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from eagle import models  # noqa: F401 (registers tables)
from eagle.db import Base

target_metadata = Base.metadata


def run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(run_with_connection)
    await engine.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    run_with_connection(connection)
else:
    from eagle.config import get_settings

    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    asyncio.run(run_async(url))
