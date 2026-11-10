"""Create the first administrator at startup when there are no users at all."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import passwords
from app.auth.crypto import MIN_KEY_LENGTH
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.auth import ROLE_ADMIN, SOURCE_LOCAL, User
from app.services import audit_service

logger = get_logger(__name__)


async def bootstrap_admin(factory: async_sessionmaker[AsyncSession], settings: Settings) -> str:
    """Returns what happened: created | exists | no_password | unavailable | disabled."""
    if not settings.auth_enabled:
        logger.warning(
            "auth_disabled",
            detail="AUTH_ENABLED=false: every request is treated as an administrator. "
            "Use this only on a trusted single-user machine.",
        )
        return "disabled"
    key = settings.auth_secret_key.get_secret_value() if settings.auth_secret_key else ""
    if len(key) < MIN_KEY_LENGTH:
        logger.warning(
            "auth_secret_key_missing",
            detail="AUTH_SECRET_KEY is not set; directory (LDAP/Kerberos) secrets cannot be saved.",
        )
    try:
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(User))
            if count:
                return "exists"
            if (
                settings.admin_initial_password is None
                or not settings.admin_initial_password.get_secret_value()
            ):
                logger.warning(
                    "auth_no_users",
                    detail="There are no users and ADMIN_INITIAL_PASSWORD is not set. Set it and "
                    "restart, or run: python -m app.cli.admin create-user <name> --role admin",
                )
                return "no_password"
            password = settings.admin_initial_password.get_secret_value()
            user = User(
                username=settings.admin_username,
                display_name="Administrator",
                source=SOURCE_LOCAL,
                password_hash=await passwords.hash_password_async(password),
                role=ROLE_ADMIN,
                role_pinned=False,
                must_change_password=True,
                disabled=False,
                groups=[],
            )
            session.add(user)
            await session.flush()
            audit_service.record(
                session,
                "user_created",
                actor_name="system",
                target=user.username,
                details={"role": ROLE_ADMIN, "bootstrap": True},
            )
            await session.commit()
            logger.info("auth_admin_created", username=user.username, must_change_password=True)
            return "created"
    except IntegrityError:
        return "exists"  # another API process created it first
    except SQLAlchemyError as exc:
        logger.warning("auth_bootstrap_unavailable", error_type=type(exc).__name__)
        return "unavailable"
