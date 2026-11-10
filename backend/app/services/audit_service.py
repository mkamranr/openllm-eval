"""Audit log: who signed in, changed users or settings, created tokens, downloaded reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, redact_value
from app.db.models.auth import AuditEvent
from app.schemas.auth import AuditRead
from app.schemas.common import Page

logger = get_logger(__name__)


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host[:64]


def record(
    session: AsyncSession,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_name: str | None = None,
    target: str | None = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the session; the caller commits."""
    clean = redact_value(details or {})
    session.add(
        AuditEvent(
            actor_user_id=actor_id,
            actor_name=actor_name,
            action=action,
            target=(target or None) and target[:300],
            ip=client_ip(request),
            details=clean,
        )
    )
    logger.info("audit", action=action, actor=actor_name, target=target)


async def list_events(
    session: AsyncSession,
    *,
    actor: str | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    offset: int,
) -> Page[AuditRead]:
    query = select(AuditEvent)
    if actor:
        query = query.where(AuditEvent.actor_name == actor.strip().lower())
    if action:
        query = query.where(AuditEvent.action == action)
    if since:
        query = query.where(AuditEvent.at >= since)
    if until:
        query = query.where(AuditEvent.at <= until)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(query.order_by(AuditEvent.at.desc()).limit(limit).offset(offset))
    ).all()
    return Page(
        items=[AuditRead.model_validate(r) for r in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
