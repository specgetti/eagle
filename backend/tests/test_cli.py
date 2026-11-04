import io
import subprocess
import sys

import pytest

from eagle import cli
from tests.conftest import login


@pytest.fixture
def cli_env(monkeypatch, settings):
    monkeypatch.setenv("EAGLE_DATABASE_URL", settings.database_url)
    cli.get_settings.cache_clear()
    yield
    cli.get_settings.cache_clear()


async def test_reset_password_cli(app, cli_env, make_client, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("brand-new-password-1\n"))
    # The CLI runs its own event loop, so call it from a thread.
    import asyncio

    await asyncio.to_thread(cli.main, ["reset-password", "admin", "--password-stdin"])
    assert "updated" in capsys.readouterr().out
    client = await make_client()
    await login(client, "admin", "brand-new-password-1")


async def test_reset_password_generate_and_short(app, cli_env, capsys):
    import asyncio

    await asyncio.to_thread(cli.main, ["reset-password", "admin", "--generate"])
    assert "New password:" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        await asyncio.to_thread(cli.main, ["reset-password", "nobody", "--generate"])


async def test_create_user_cli(app, cli_env, make_client, monkeypatch):
    import asyncio

    monkeypatch.setattr(sys, "stdin", io.StringIO("member-password-1\n"))
    await asyncio.to_thread(
        cli.main, ["create-user", "zed", "--role", "member", "--password-stdin"]
    )
    client = await make_client()
    await login(client, "zed", "member-password-1")
    assert (await client.get("/api/v1/auth/me")).json()["role"] == "member"


def test_generate_key(capsys):
    from cryptography.fernet import Fernet

    cli.main(["generate-key"])
    Fernet(capsys.readouterr().out.strip().encode())  # valid key


def test_migrate_cli_on_fresh_db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    proc = subprocess.run(
        [sys.executable, "-m", "eagle.cli", "migrate"],
        env={"EAGLE_DATABASE_URL": url, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "up to date" in proc.stdout
    # Idempotent
    proc = subprocess.run(
        [sys.executable, "-m", "eagle.cli", "migrate"],
        env={"EAGLE_DATABASE_URL": url, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_bootstrap_warns_when_password_ignored(tmp_path, caplog):
    import asyncio

    from eagle.bootstrap import bootstrap
    from eagle.config import Settings
    from eagle.db import Database
    from eagle.migrate import upgrade_database

    async def run():
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'b.db'}",
            bootstrap_admin_password="first-password-1",
        )
        db = Database(settings.database_url)
        await upgrade_database(db.engine)
        async with db.sessionmaker() as s:
            await bootstrap(s, settings)
        async with db.sessionmaker() as s:
            await bootstrap(s, settings)  # same password: silent
        assert "is set but users already exist" not in caplog.text
        changed = settings.model_copy(update={"bootstrap_admin_password": "other-password-1"})
        async with db.sessionmaker() as s:
            await bootstrap(s, changed)
        await db.dispose()

    asyncio.run(run())
    assert "is set but users already exist" in caplog.text
