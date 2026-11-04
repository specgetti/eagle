"""The worker waits for the web pods' migrations instead of crashing on a fresh DB."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from eagle.migrate import (
    current_revision,
    head_revision,
    upgrade_database,
    wait_for_database,
    wait_for_schema,
)


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
    yield eng
    await eng.dispose()


async def test_returns_immediately_when_schema_is_current(engine):
    await upgrade_database(engine)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await wait_for_schema(engine, sleep=sleep)
    assert sleeps == []
    assert await current_revision(engine) == head_revision()


async def test_waits_until_another_process_migrates(engine, caplog):
    calls = 0

    async def sleep(seconds: float) -> None:
        # The web pod's init container finishes its migration while we wait.
        nonlocal calls
        calls += 1
        if calls == 2:
            await upgrade_database(engine)

    caplog.set_level("INFO", logger="eagle.migrate")
    await wait_for_schema(engine, interval=1, sleep=sleep)
    assert calls == 2
    assert "Waiting for database migrations: schema is at no schema" in caplog.text


async def test_times_out_with_a_clear_message(engine):
    async def sleep(seconds: float) -> None:
        pass

    with pytest.raises(RuntimeError, match="needs .*Run `eagle migrate`"):
        await wait_for_schema(engine, timeout=3, interval=1, sleep=sleep)


async def test_unreachable_database_is_retried_not_fatal(tmp_path):
    # A directory that does not exist: connecting fails until the timeout.
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'x.db'}")
    attempts = 0

    async def sleep(seconds: float) -> None:
        nonlocal attempts
        attempts += 1

    with pytest.raises(RuntimeError, match="unreachable"):
        await wait_for_schema(eng, timeout=2, interval=1, sleep=sleep)
    assert attempts == 2
    await eng.dispose()


async def test_wait_for_database_returns_when_reachable(engine):
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await wait_for_database(engine, sleep=sleep)
    assert sleeps == []


async def test_wait_for_database_retries_until_it_is_up(tmp_path, caplog):
    # The database "starts" (its directory appears) after two failed attempts.
    folder = tmp_path / "later"
    eng = create_async_engine(f"sqlite+aiosqlite:///{folder / 'x.db'}")
    attempts = 0

    async def sleep(seconds: float) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            folder.mkdir()

    caplog.set_level("INFO", logger="eagle.migrate")
    await wait_for_database(eng, interval=1, sleep=sleep)
    assert attempts == 2
    assert "Waiting for the database" in caplog.text
    await eng.dispose()


async def test_wait_for_database_times_out(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'x.db'}")

    async def sleep(seconds: float) -> None:
        pass

    with pytest.raises(RuntimeError, match="not reachable after 2s"):
        await wait_for_database(eng, timeout=2, interval=1, sleep=sleep)
    await eng.dispose()
