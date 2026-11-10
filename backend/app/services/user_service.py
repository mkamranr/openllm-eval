"""User administration (admin only): local accounts, roles, disabling, password resets."""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords
from app.auth.dependencies import Principal, now
from app.auth.tokens import generate_password
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models.auth import ROLE_ADMIN, SOURCE_LOCAL, SOURCE_SERVICE, User
from app.schemas.auth import (
    PasswordResetRequest,
    PasswordResetResult,
    UserCreate,
    UserCreated,
    UserRead,
    UserUpdate,
)
from app.schemas.common import Page
from app.services import audit_service, auth_service


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


async def list_users(
    session: AsyncSession, *, q: str | None, limit: int, offset: int
) -> Page[UserRead]:
    query = select(User)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(
            or_(func.lower(User.username).like(like), func.lower(User.display_name).like(like))
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await session.scalars(query.order_by(User.username).limit(limit).offset(offset))).all()
    return Page(
        items=[UserRead.model_validate(u) for u in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def _active_admins(session: AsyncSession, *, excluding: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == ROLE_ADMIN, User.disabled.is_(False), User.id != excluding)
        )
    ) or 0


async def create_local_user(
    session: AsyncSession,
    settings: Settings,
    data: UserCreate,
    *,
    actor: Principal | None,
    request: Request | None,
    source: str = SOURCE_LOCAL,
) -> UserCreated:
    generated: str | None = None
    password_hash: str | None = None
    if source == SOURCE_LOCAL:
        if data.password is not None:
            password = data.password.get_secret_value()
            passwords.check_policy(
                password,
                username=data.username,
                forbidden=auth_service.forbidden_passwords(settings),
            )
        else:
            password = generated = generate_password()
        password_hash = await passwords.hash_password_async(password)
    user = User(
        username=data.username,
        display_name=data.display_name,
        email=data.email,
        source=source,
        password_hash=password_hash,
        role=data.role,
        role_pinned=False,
        must_change_password=data.must_change_password and source == SOURCE_LOCAL,
        disabled=False,
        groups=[],
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise AppError(
            f"A user named {data.username!r} already exists.", code="conflict", status_code=409
        ) from None
    audit_service.record(
        session,
        "user_created",
        actor_id=actor.user_id if actor else None,
        actor_name=actor.username if actor else "system",
        target=user.username,
        request=request,
        details={"role": user.role, "source": source},
    )
    await session.commit()
    return UserCreated(**UserRead.model_validate(user).model_dump(), initial_password=generated)


async def update_user(
    session: AsyncSession,
    data: UserUpdate,
    user_id: uuid.UUID,
    *,
    actor: Principal,
    request: Request,
) -> UserRead:
    user = await get_user(session, user_id)
    changes = data.model_dump(exclude_unset=True)
    is_self = actor.user_id == user.id
    demoting = "role" in changes and changes["role"] != ROLE_ADMIN and user.role == ROLE_ADMIN
    disabling = changes.get("disabled") is True and not user.disabled
    if is_self and (demoting or disabling):
        raise AppError(
            "You cannot demote or disable your own account.", code="forbidden", status_code=403
        )
    if (
        (demoting or disabling)
        and user.role == ROLE_ADMIN
        and await _active_admins(session, excluding=user.id) == 0
    ):
        raise AppError("This is the last active administrator.", code="last_admin", status_code=409)
    if "role_pinned" in changes and user.source == SOURCE_LOCAL and changes["role_pinned"]:
        changes.pop("role_pinned")  # pinning only matters for directory users
    for key, value in changes.items():
        if key in ("role", "disabled", "role_pinned") and value is None:
            continue
        setattr(user, key, value)
    if (
        "role" in changes
        and user.source != SOURCE_LOCAL
        and changes["role"] is not None
        and "role_pinned" not in changes
    ):
        # An administrator's explicit role for a directory user would be overwritten at the next
        # sign-in by the group mapping; pin it.
        user.role_pinned = True
    if disabling:
        await auth_service.end_all_sessions(session, user.id)
    audit_service.record(
        session,
        "user_updated",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target=user.username,
        request=request,
        details={k: v for k, v in changes.items()},
    )
    await session.commit()
    return UserRead.model_validate(user)


async def delete_user(
    session: AsyncSession, user_id: uuid.UUID, *, actor: Principal, request: Request
) -> None:
    user = await get_user(session, user_id)
    if actor.user_id == user.id:
        raise AppError("You cannot delete your own account.", code="forbidden", status_code=403)
    if (
        user.role == ROLE_ADMIN
        and not user.disabled
        and await _active_admins(session, excluding=user.id) == 0
    ):
        raise AppError("This is the last active administrator.", code="last_admin", status_code=409)
    name = user.username
    await session.delete(user)
    audit_service.record(
        session,
        "user_deleted",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target=name,
        request=request,
    )
    await session.commit()


async def reset_password(
    session: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    data: PasswordResetRequest,
    *,
    actor: Principal,
    request: Request,
) -> PasswordResetResult:
    user = await get_user(session, user_id)
    if user.source != SOURCE_LOCAL:
        raise AppError(
            "Only local accounts have a password here; directory passwords are reset in "
            "Active Directory.",
            code="directory_account",
            status_code=409,
        )
    generated = None
    if data.password is not None:
        password = data.password.get_secret_value()
        passwords.check_policy(
            password, username=user.username, forbidden=auth_service.forbidden_passwords(settings)
        )
    else:
        password = generated = generate_password()
    user.password_hash = await passwords.hash_password_async(password)
    user.must_change_password = True
    user.password_changed_at = now()
    await auth_service.end_all_sessions(session, user.id)
    audit_service.record(
        session,
        "password_reset",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target=user.username,
        request=request,
    )
    await session.commit()
    return PasswordResetResult(temporary_password=generated, must_change_password=True)


async def sign_out_everywhere(
    session: AsyncSession, user_id: uuid.UUID, *, actor: Principal, request: Request
) -> int:
    user = await get_user(session, user_id)
    count = await auth_service.end_all_sessions(session, user.id)
    audit_service.record(
        session,
        "sessions_revoked",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target=user.username,
        request=request,
        details={"sessions": count},
    )
    await session.commit()
    return count


__all__ = [
    "SOURCE_SERVICE",
    "create_local_user",
    "delete_user",
    "get_user",
    "list_users",
    "reset_password",
    "sign_out_everywhere",
    "update_user",
]
