"""Audit log (admin role)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.schemas.auth import AuditRead
from app.schemas.common import Page
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditRead])
async def list_audit_events(
    actor: str | None = Query(None, max_length=128),
    action: str | None = Query(None, max_length=64),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[AuditRead]:
    return await audit_service.list_events(
        session, actor=actor, action=action, since=since, until=until, limit=limit, offset=offset
    )
