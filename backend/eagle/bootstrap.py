import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from eagle.config import Settings
from eagle.models import Role, User
from eagle.security import hash_password, verify_password
from eagle.services import ensure_agent_configs

logger = logging.getLogger(__name__)


async def bootstrap(db: AsyncSession, settings: Settings) -> None:
    await ensure_agent_configs(db)
    await ensure_bootstrap_admin(db, settings)
    await db.commit()


async def ensure_bootstrap_admin(db: AsyncSession, settings: Settings) -> None:
    """Create the first admin account when the users table is empty."""
    user_count = await db.scalar(select(func.count()).select_from(User))
    if user_count:
        if settings.bootstrap_admin_password:
            admin = await db.scalar(
                select(User).where(User.username == settings.bootstrap_admin_username)
            )
            if admin is not None and verify_password(
                admin.password_hash, settings.bootstrap_admin_password
            ):
                return  # still in sync, nothing to report
            logger.warning(
                "EAGLE_BOOTSTRAP_ADMIN_PASSWORD is set but users already exist, so it is "
                "ignored. Use `eagle reset-password %s` to change a password.",
                settings.bootstrap_admin_username,
            )
        return

    password = settings.bootstrap_admin_password or secrets.token_urlsafe(18)
    db.add(
        User(
            username=settings.bootstrap_admin_username,
            password_hash=hash_password(password),
            role=Role.ADMIN,
        )
    )
    await db.flush()

    if settings.bootstrap_admin_password:
        logger.info("Created bootstrap admin %r", settings.bootstrap_admin_username)
    else:
        logger.warning(
            "Created bootstrap admin %r with generated password %r. Change it after first login.",
            settings.bootstrap_admin_username,
            password,
        )
