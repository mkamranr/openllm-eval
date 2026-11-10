"""Users, sessions, API tokens, directory settings, and the audit log (M8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
ROLES = (ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN)
ROLE_RANK = {ROLE_VIEWER: 1, ROLE_EDITOR: 2, ROLE_ADMIN: 3}

SOURCE_LOCAL = "local"
SOURCE_LDAP = "ldap"
SOURCE_KERBEROS = "kerberos"
# Non-interactive accounts that only hold API tokens (e.g. the smoke test); no password.
SOURCE_SERVICE = "service"
USER_SOURCES = (SOURCE_LOCAL, SOURCE_LDAP, SOURCE_KERBEROS, SOURCE_SERVICE)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Lower-case; directory users are stored by their short (sAMAccountName-style) name.
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_LOCAL)
    # argon2id; NULL for directory and service users.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_VIEWER)
    # Directory users: True keeps an administrator's role choice instead of the group mapping.
    role_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    directory_dn: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Group DNs seen at the last directory sign-in.
    groups: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UserSession(UUIDPrimaryKeyMixin, Base):
    """A browser session. Only the SHA-256 of the cookie value is stored."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ApiToken(UUIDPrimaryKeyMixin, Base):
    """A bearer token for the CLI and scripts. Shown once; only its SHA-256 is stored."""

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # First characters of the token, so people can tell their tokens apart.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuthSettings(Base):
    """Directory sign-in settings (single row, id = 1). Secrets are encrypted at rest."""

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ldap: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    kerberos: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    ldap_bind_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    kerberos_keytab_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"

    at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
