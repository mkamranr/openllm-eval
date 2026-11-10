"""User administration (admin role; enforced in app/api/router.py)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_session
from app.auth.dependencies import Principal, get_principal
from app.core.config import Settings
from app.schemas.auth import (
    PasswordResetRequest,
    PasswordResetResult,
    UserCreate,
    UserCreated,
    UserRead,
    UserUpdate,
)
from app.schemas.common import Page
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserRead])
async def list_users(
    q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[UserRead]:
    return await user_service.list_users(session, q=q, limit=limit, offset=offset)


@router.post("", response_model=UserCreated, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> UserCreated:
    """Create a local account. Without a password, one is generated and returned once."""
    return await user_service.create_local_user(
        session, settings, data, actor=principal, request=request
    )


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> UserRead:
    return UserRead.model_validate(await user_service.get_user(session, user_id))


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    return await user_service.update_user(session, data, user_id, actor=principal, request=request)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await user_service.delete_user(session, user_id, actor=principal, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResult)
async def reset_password(
    user_id: uuid.UUID,
    request: Request,
    data: PasswordResetRequest | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> PasswordResetResult:
    """Set a new password (or generate one, returned once); the user must change it."""
    return await user_service.reset_password(
        session, settings, user_id, data or PasswordResetRequest(), actor=principal, request=request
    )


@router.delete("/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_everywhere(
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await user_service.sign_out_everywhere(session, user_id, actor=principal, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
