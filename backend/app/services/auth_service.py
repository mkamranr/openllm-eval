"""Sign-in (local password, Active Directory, Kerberos), sessions, password changes, API tokens."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords
from app.auth.dependencies import Principal, aware, now
from app.auth.kerberos import KerberosError, principal_username
from app.auth.ldap import (
    DirectoryError,
    DirectoryUser,
    InvalidCredentialsError,
    map_role,
    normalize_username,
)
from app.auth.lockout import Lockout
from app.auth.tokens import hash_token, new_api_token, new_session_token, token_display_prefix
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.db.models.auth import (
    SOURCE_LDAP,
    SOURCE_LOCAL,
    ApiToken,
    User,
    UserSession,
)
from app.schemas.auth import LdapSettings, TokenCreate, TokenCreated, TokenRead
from app.services import audit_service, auth_settings_service

logger = get_logger(__name__)

INVALID_CREDENTIALS = "Invalid username or password."


class LoginError(AppError):
    status_code = 401
    code = "invalid_credentials"


async def get_user_by_name(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username.lower()))


# ------------------------------------------------------------------------------------ sessions
async def create_session(
    session: AsyncSession, settings: Settings, user: User, request: Request, method: str
) -> str:
    token = new_session_token()
    current = now()
    # Housekeeping: expired sessions are removed at every sign-in.
    await session.execute(delete(UserSession).where(UserSession.expires_at < current))
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            auth_method=method,
            created_at=current,
            last_seen_at=current,
            expires_at=current + timedelta(hours=settings.auth_session_max_hours),
            ip=audit_service.client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    user.last_login_at = current
    return token


async def end_session(session: AsyncSession, principal: Principal) -> None:
    if principal.session_id is not None:
        await session.execute(delete(UserSession).where(UserSession.id == principal.session_id))


async def end_all_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, keep: uuid.UUID | None = None
) -> int:
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if keep is not None:
        query = query.where(UserSession.id != keep)
    result = await session.execute(query)
    return result.rowcount or 0  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------- sign-in
async def _upsert_directory_user(
    session: AsyncSession, existing: User | None, found: DirectoryUser, cfg: LdapSettings
) -> User:
    role = map_role(found.groups, cfg)
    if role is None:
        raise AppError(
            "Your directory account is not in a group that may use OpenLLM-Eval.",
            code="not_authorized",
            status_code=403,
        )
    user = existing
    if user is None:
        user = User(
            username=found.username,
            source=SOURCE_LDAP,
            role=role,
            groups=[],
            must_change_password=False,
            disabled=False,
            role_pinned=False,
        )
        session.add(user)
    elif not user.role_pinned:
        if user.role != role:
            logger.info("directory_role_changed", username=user.username, role=role)
        user.role = role
    user.display_name = found.display_name or user.display_name
    user.email = found.email or user.email
    user.directory_dn = found.dn
    user.groups = found.groups
    return user


async def login(
    session: AsyncSession,
    settings: Settings,
    redis: Redis,
    request: Request,
    raw_username: str,
    password: str,
) -> tuple[User, str]:
    """Verify credentials and open a session. Returns the user and the cookie value."""
    if not settings.auth_enabled:
        raise AppError(
            "Authentication is disabled on this server.", code="auth_disabled", status_code=409
        )
    username = normalize_username(raw_username)
    ip = audit_service.client_ip(request)
    lockout = Lockout(redis, settings)
    if not username:
        raise LoginError(INVALID_CREDENTIALS)
    wait = await lockout.locked_for(username, ip)
    if wait:
        raise AppError(
            f"Too many failed sign-in attempts. Try again in {max(1, round(wait / 60))} minute(s).",
            code="account_locked",
            status_code=429,
            details={"retry_after_seconds": wait},
        )

    async def fail(reason: str, error: AppError | None = None) -> AppError:
        await lockout.record_failure(username, ip)
        audit_service.record(
            session,
            "login_failed",
            actor_name=username,
            target=username,
            request=request,
            details={"reason": reason},
        )
        await session.commit()
        return error or LoginError(INVALID_CREDENTIALS)

    user = await get_user_by_name(session, username)
    method = "password"
    if user is not None and user.source == SOURCE_LOCAL:
        if not await passwords.verify_password_async(user.password_hash, password):
            raise await fail("bad_password")
    elif user is not None and user.source != SOURCE_LDAP:
        await passwords.verify_password_async(None, password)
        raise await fail("no_password_sign_in")
    else:
        config = await auth_settings_service.load(session, settings)
        if not config.ldap.enabled:
            await passwords.verify_password_async(None, password)
            raise await fail("unknown_user")
        directory = auth_settings_service.directory(
            request, settings, config.ldap, config.bind_password
        )
        try:
            found = await asyncio.to_thread(directory.authenticate, username, password)
        except InvalidCredentialsError:
            raise await fail("directory_rejected") from None
        except DirectoryError as exc:
            logger.warning("directory_unavailable", error=str(exc))
            audit_service.record(
                session,
                "login_failed",
                actor_name=username,
                target=username,
                request=request,
                details={"reason": "directory_error", "error": str(exc)},
            )
            await session.commit()
            raise AppError(
                "The directory server could not be used. Try again later or contact an "
                "administrator.",
                code="directory_unavailable",
                status_code=503,
            ) from None
        method = "ldap"
        if user is None and found.username != username:
            user = await get_user_by_name(session, found.username)
        if user is not None and user.source == SOURCE_LOCAL:
            raise await fail("name_collision")
        try:
            user = await _upsert_directory_user(session, user, found, config.ldap)
        except AppError as exc:
            raise await fail("not_in_allowed_group", exc) from None

    if user.disabled:
        raise await fail(
            "disabled",
            AppError("This account is disabled.", code="account_disabled", status_code=403),
        )
    try:
        token = await create_session(session, settings, user, request, method)
        audit_service.record(
            session,
            "login",
            actor_id=user.id,
            actor_name=user.username,
            target=user.username,
            request=request,
            details={"method": method, "role": user.role},
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(
            "Sign-in conflicted with a concurrent request; try again.",
            code="conflict",
            status_code=409,
        ) from None
    await lockout.clear_user(username)
    return user, token


async def login_kerberos(
    session: AsyncSession, settings: Settings, request: Request, ticket: bytes
) -> tuple[User, str, bytes | None]:
    config = await auth_settings_service.load(session, settings)
    if not (
        settings.auth_enabled and config.kerberos.enabled and config.keytab and config.ldap.enabled
    ):
        raise AppError(
            "Windows single sign-on is not enabled.", code="sso_disabled", status_code=404
        )
    acceptor = auth_settings_service.acceptor(request, config.kerberos, config.keytab)
    try:
        principal, reply = await asyncio.to_thread(acceptor.accept, ticket)
    except KerberosError as exc:
        audit_service.record(
            session,
            "login_failed",
            request=request,
            details={"reason": "kerberos", "error": str(exc)},
        )
        await session.commit()
        raise LoginError(
            "Windows sign-in failed; sign in with your username and password."
        ) from None
    username = principal_username(principal)
    directory = auth_settings_service.directory(
        request, settings, config.ldap, config.bind_password
    )
    try:
        found = await asyncio.to_thread(directory.lookup, username)
    except InvalidCredentialsError:
        audit_service.record(
            session,
            "login_failed",
            actor_name=username,
            request=request,
            details={"reason": "kerberos_user_not_in_directory"},
        )
        await session.commit()
        raise LoginError("Your Windows account was not found in the directory.") from None
    except DirectoryError as exc:
        logger.warning("directory_unavailable", error=str(exc))
        raise AppError(
            "The directory server could not be used.", code="directory_unavailable", status_code=503
        ) from None
    user = await get_user_by_name(session, found.username)
    if user is not None and user.source == SOURCE_LOCAL:
        raise LoginError("A local account with this name exists; sign in with its password.")
    try:
        user = await _upsert_directory_user(session, user, found, config.ldap)
    except AppError:
        audit_service.record(
            session,
            "login_failed",
            actor_name=username,
            request=request,
            details={"reason": "not_in_allowed_group"},
        )
        await session.commit()
        raise
    if user.disabled:
        raise AppError("This account is disabled.", code="account_disabled", status_code=403)
    token = await create_session(session, settings, user, request, "kerberos")
    audit_service.record(
        session,
        "login",
        actor_id=user.id,
        actor_name=user.username,
        target=user.username,
        request=request,
        details={"method": "kerberos", "role": user.role},
    )
    await session.commit()
    return user, token, reply


# --------------------------------------------------------------------------------- passwords
def forbidden_passwords(settings: Settings) -> tuple[str, ...]:
    initial = settings.admin_initial_password
    return (initial.get_secret_value(),) if initial else ()


async def change_password(
    session: AsyncSession,
    settings: Settings,
    request: Request,
    principal: Principal,
    current_password: str,
    new_password: str,
) -> None:
    user = principal.user
    if user is None:
        raise AppError(
            "Authentication is disabled; there is no account.",
            code="auth_disabled",
            status_code=409,
        )
    if user.source != SOURCE_LOCAL:
        raise AppError(
            "Directory accounts change their password in Windows / Active Directory.",
            code="directory_account",
            status_code=409,
        )
    if not await passwords.verify_password_async(user.password_hash, current_password):
        audit_service.record(
            session,
            "password_change_failed",
            actor_id=user.id,
            actor_name=user.username,
            request=request,
        )
        await session.commit()
        raise AppError(
            "The current password is wrong.", code="invalid_credentials", status_code=403
        )
    passwords.check_policy(
        new_password,
        username=user.username,
        forbidden=(current_password, *forbidden_passwords(settings)),
    )
    user.password_hash = await passwords.hash_password_async(new_password)
    user.must_change_password = False
    user.password_changed_at = now()
    # Other sessions and tokens of this user keep working only if they are this session.
    await end_all_sessions(session, user.id, keep=principal.session_id)
    audit_service.record(
        session,
        "password_changed",
        actor_id=user.id,
        actor_name=user.username,
        target=user.username,
        request=request,
    )
    await session.commit()


# ------------------------------------------------------------------------------------ tokens
async def list_tokens(session: AsyncSession, user_id: uuid.UUID) -> list[TokenRead]:
    rows = (
        await session.scalars(
            select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())
        )
    ).all()
    return [TokenRead.model_validate(r) for r in rows]


async def issue_token(
    session: AsyncSession, user: User, name: str, expires_in: timedelta | None
) -> TokenCreated:
    token = new_api_token()
    current = now()
    row = ApiToken(
        user_id=user.id,
        name=name.strip(),
        prefix=token_display_prefix(token),
        token_hash=hash_token(token),
        created_at=current,
        expires_at=current + expires_in if expires_in else None,
    )
    session.add(row)
    await session.flush()
    return TokenCreated(**TokenRead.model_validate(row).model_dump(), token=token)


async def create_token(
    session: AsyncSession, request: Request, principal: Principal, data: TokenCreate
) -> TokenCreated:
    user = principal.user
    if user is None:
        raise AppError(
            "Authentication is disabled; API tokens are not needed.",
            code="auth_disabled",
            status_code=409,
        )
    active = [t for t in await list_tokens(session, user.id) if t.revoked_at is None]
    if len(active) >= 50:
        raise AppError(
            "At most 50 active tokens per user; revoke some first.",
            code="too_many_tokens",
            status_code=409,
        )
    created = await issue_token(
        session,
        user,
        data.name,
        timedelta(days=data.expires_in_days) if data.expires_in_days else None,
    )
    audit_service.record(
        session,
        "token_created",
        actor_id=user.id,
        actor_name=user.username,
        target=data.name,
        request=request,
        details={"prefix": created.prefix, "expires_at": str(created.expires_at)},
    )
    await session.commit()
    return created


async def revoke_token(
    session: AsyncSession, request: Request, principal: Principal, token_id: uuid.UUID
) -> None:
    row = await session.get(ApiToken, token_id)
    if row is None or principal.user is None or row.user_id != principal.user.id:
        raise NotFoundError("Token not found")
    if row.revoked_at is None:
        row.revoked_at = now()
        audit_service.record(
            session,
            "token_revoked",
            actor_id=principal.user_id,
            actor_name=principal.username,
            target=row.name,
            request=request,
            details={"prefix": row.prefix},
        )
    await session.commit()


def token_is_active(row: ApiToken) -> bool:
    return row.revoked_at is None and (row.expires_at is None or aware(row.expires_at) > now())
