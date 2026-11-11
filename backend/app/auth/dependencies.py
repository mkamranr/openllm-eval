"""Who is calling, and may they? Resolves a session cookie or API token into a Principal.

Authorization is applied centrally in ``app/api/router.py``: every router except health and
auth requires the viewer role for reads and the editor role for changes; administration routers
require admin. Route modules do not need their own checks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_session
from app.auth.tokens import API_TOKEN_PREFIX, CSRF_HEADER, SESSION_COOKIE, hash_token
from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.auth import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_RANK,
    ROLE_VIEWER,
    ApiToken,
    User,
    UserSession,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# How often last_seen_at / last_used_at are written (not on every request).
TOUCH_INTERVAL = timedelta(seconds=60)


def aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they are stored as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Principal:
    user: User | None
    username: str
    role: str
    method: str  # session | token | anonymous
    session_id: uuid.UUID | None = None
    token_id: uuid.UUID | None = None

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def must_change_password(self) -> bool:
        return bool(self.user and self.user.must_change_password)


ANONYMOUS_ADMIN = Principal(user=None, username="anonymous", role=ROLE_ADMIN, method="anonymous")


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "not_authenticated"


def _unauthenticated(message: str = "Sign in required.") -> NotAuthenticatedError:
    return NotAuthenticatedError(message)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def _origin_allowed(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        # Browsers send Origin on cross-site requests; its absence plus the custom header
        # (which cross-site forms cannot set) is enough.
        return True
    if origin == "null":
        return False
    allowed = {
        o.rstrip("/").lower() for o in (*settings.cors_origins, *settings.auth_trusted_origins)
    }
    if origin.rstrip("/").lower() in allowed:
        return True
    try:
        parts = urlsplit(origin)
        origin_host, origin_port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return False
    for header in ("x-forwarded-host", "host"):
        value = (request.headers.get(header) or "").split(",")[0].strip().lower()
        if not value:
            continue
        if value == parts.netloc.lower():
            return True
        host, _, port = value.rpartition(":") if not value.endswith("]") else (value, "", "")
        if not host or not port.isdigit():
            host, port = value, ""
        # A proxy that forwards "Host: name" without the port (nginx $host) still means the
        # same site; the custom CSRF header is what cross-site pages cannot send.
        host = host.strip("[]")
        if host == origin_host and (not port or origin_port is None or int(port) == origin_port):
            return True
    return False


def check_csrf(request: Request, settings: Settings) -> None:
    """Cookie-authenticated changes must come from this site's own pages."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get(CSRF_HEADER) != "1" or not _origin_allowed(request, settings):
        raise AppError(
            "Cross-site request rejected (missing CSRF header or foreign origin).",
            code="csrf_failed",
            status_code=403,
        )


async def _from_token(session: AsyncSession, token: str) -> Principal:
    if not token.startswith(API_TOKEN_PREFIX):
        raise _unauthenticated("Invalid API token.")
    row = (
        await session.execute(
            select(ApiToken, User)
            .join(User, User.id == ApiToken.user_id)
            .where(ApiToken.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        raise _unauthenticated("Invalid API token.")
    api_token, user = row
    current = now()
    if api_token.revoked_at is not None:
        raise _unauthenticated("This API token has been revoked.")
    if api_token.expires_at is not None and aware(api_token.expires_at) <= current:
        raise _unauthenticated("This API token has expired.")
    if user.disabled:
        raise _unauthenticated("This account is disabled.")
    if api_token.last_used_at is None or current - aware(api_token.last_used_at) > TOUCH_INTERVAL:
        api_token.last_used_at = current
        await session.commit()
    return Principal(
        user=user, username=user.username, role=user.role, method="token", token_id=api_token.id
    )


async def _from_cookie(session: AsyncSession, settings: Settings, cookie: str) -> Principal:
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(cookie))
        )
    ).first()
    if row is None:
        raise _unauthenticated("Your session has ended. Sign in again.")
    user_session, user = row
    current = now()
    idle = timedelta(minutes=settings.auth_session_idle_minutes)
    if (
        aware(user_session.expires_at) <= current
        or current - aware(user_session.last_seen_at) > idle
    ):
        await session.delete(user_session)
        await session.commit()
        raise _unauthenticated("Your session has expired. Sign in again.")
    if user.disabled:
        raise _unauthenticated("This account is disabled.")
    if current - aware(user_session.last_seen_at) > TOUCH_INTERVAL:
        user_session.last_seen_at = current
        await session.commit()
    return Principal(
        user=user,
        username=user.username,
        role=user.role,
        method="session",
        session_id=user_session.id,
    )


async def get_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> Principal:
    """The authenticated caller (401 if none). Cached for the request."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    if not settings.auth_enabled:
        principal = ANONYMOUS_ADMIN
    elif (token := _bearer(request)) is not None:
        principal = await _from_token(session, token)
    elif cookie := request.cookies.get(SESSION_COOKIE):
        principal = await _from_cookie(session, settings, cookie)
        check_csrf(request, settings)
    else:
        raise _unauthenticated()
    request.state.principal = principal
    return principal


def require_role(min_role: str | None = None):
    """Dependency: the caller's role must be at least ``min_role``. With None, reads need
    viewer and changes need editor. Users who must change their password are refused."""

    async def dependency(
        request: Request, principal: Principal = Depends(get_principal)
    ) -> Principal:
        if principal.must_change_password:
            raise AppError(
                "You must change your password before continuing.",
                code="password_change_required",
                status_code=403,
            )
        needed = min_role or (ROLE_VIEWER if request.method in SAFE_METHODS else ROLE_EDITOR)
        if ROLE_RANK.get(principal.role, 0) < ROLE_RANK[needed]:
            raise AppError(
                f"This action needs the {needed} role (you are {principal.role}).",
                code="forbidden",
                status_code=403,
            )
        return principal

    return dependency


authorize = require_role()
require_viewer = require_role(ROLE_VIEWER)
require_admin = require_role(ROLE_ADMIN)
