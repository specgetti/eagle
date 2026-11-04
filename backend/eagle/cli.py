"""eagle command line: migrate, worker, user management, keys, OpenAPI export."""

import argparse
import asyncio
import getpass
import json
import logging
import secrets
import signal
import sys

from sqlalchemy import select

from eagle.config import get_settings
from eagle.db import Database
from eagle.models import Role, User
from eagle.security import SecretBox, hash_password


def _read_password(from_stdin: bool, generate: bool) -> tuple[str, bool]:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n"), False
    if generate or not sys.stdin.isatty():
        return secrets.token_urlsafe(18), True
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    return first, False


async def _migrate() -> None:
    from eagle.migrate import upgrade_database

    db = Database(get_settings().database_url)
    try:
        await upgrade_database(db.engine)
    finally:
        await db.dispose()
    print("Database is up to date")


async def _reset_password(username: str, password: str, activate: bool) -> None:
    db = Database(get_settings().database_url)
    try:
        async with db.sessionmaker() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                raise SystemExit(f"No user named {username!r}")
            user.password_hash = hash_password(password)
            if activate:
                user.is_active = True
            await session.commit()
    finally:
        await db.dispose()


async def _create_user(username: str, password: str, role: Role, email: str | None) -> None:
    db = Database(get_settings().database_url)
    try:
        async with db.sessionmaker() as session:
            if await session.scalar(select(User.id).where(User.username == username)):
                raise SystemExit(f"User {username!r} already exists")
            session.add(
                User(
                    username=username, password_hash=hash_password(password), role=role, email=email
                )
            )
            await session.commit()
    finally:
        await db.dispose()


async def _worker() -> None:
    from eagle.bootstrap import bootstrap
    from eagle.credentials import secret_box
    from eagle.executors import create_executor
    from eagle.migrate import wait_for_schema
    from eagle.worker import Worker

    settings = get_settings()
    db = Database(settings.database_url)
    await wait_for_schema(db.engine)
    async with db.sessionmaker() as session:
        await bootstrap(session, settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    worker = Worker(db.sessionmaker, settings, create_executor(settings), secret_box(settings))
    try:
        await worker.run_forever(stop)
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eagle", description="Eagle PR review platform")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply database migrations")
    sub.add_parser("worker", help="Run the review worker")

    reset = sub.add_parser("reset-password", help="Set a user's password (break-glass)")
    reset.add_argument("username")
    reset.add_argument("--password-stdin", action="store_true", help="Read password from stdin")
    reset.add_argument("--generate", action="store_true", help="Generate and print a password")
    reset.add_argument("--activate", action="store_true", help="Also re-enable the account")

    create = sub.add_parser("create-user", help="Create a user")
    create.add_argument("username")
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.VIEWER.value)
    create.add_argument("--email")
    create.add_argument("--password-stdin", action="store_true")
    create.add_argument("--generate", action="store_true")

    sub.add_parser("generate-key", help="Print a new EAGLE_ENCRYPTION_KEY")

    openapi = sub.add_parser("openapi", help="Print the OpenAPI schema as JSON")
    openapi.add_argument("--out", help="Write to this file instead of stdout")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    match args.command:
        case "migrate":
            asyncio.run(_migrate())
        case "worker":
            asyncio.run(_worker())
        case "reset-password":
            password, generated = _read_password(args.password_stdin, args.generate)
            if len(password) < 12:
                raise SystemExit("Password must be at least 12 characters")
            asyncio.run(_reset_password(args.username, password, args.activate))
            print(f"Password for {args.username!r} updated")
            if generated:
                print(f"New password: {password}")
        case "create-user":
            password, generated = _read_password(args.password_stdin, args.generate)
            if len(password) < 12:
                raise SystemExit("Password must be at least 12 characters")
            asyncio.run(_create_user(args.username, password, Role(args.role), args.email))
            print(f"Created {args.role} {args.username!r}")
            if generated:
                print(f"Password: {password}")
        case "generate-key":
            print(SecretBox.generate_key())
        case "openapi":
            from eagle.main import create_app

            schema = json.dumps(create_app().openapi(), indent=2)
            if args.out:
                with open(args.out, "w") as fh:
                    fh.write(schema + "\n")
            else:
                print(schema)
    return 0


if __name__ == "__main__":
    sys.exit(main())
