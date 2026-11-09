"""Evaluator registry and evaluation profile routes (spec §9.4)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.evaluators.registry import all_evaluators
from app.schemas.common import Page
from app.schemas.evaluation import EvaluatorInfo
from app.schemas.profile import ProfileCreate, ProfileRead, ProfileUpdate
from app.services import profile_service

router = APIRouter(tags=["evaluation"])


@router.get("/evaluators", response_model=list[EvaluatorInfo], summary="Registered evaluators")
async def list_evaluators() -> list[EvaluatorInfo]:
    return [EvaluatorInfo(**e.describe()) for e in all_evaluators()]


@router.get("/profiles", response_model=Page[ProfileRead])
async def list_profiles(
    project_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[ProfileRead]:
    return await profile_service.list_profiles(
        session, project_id=project_id, limit=limit, offset=offset
    )


@router.post("/profiles", response_model=ProfileRead, status_code=status.HTTP_201_CREATED)
async def create_profile(
    data: ProfileCreate, session: AsyncSession = Depends(get_session)
) -> ProfileRead:
    return profile_service.to_read(await profile_service.create_profile(session, data))


@router.get("/profiles/{profile_id}", response_model=ProfileRead)
async def get_profile(
    profile_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ProfileRead:
    return profile_service.to_read(await profile_service.get_profile(session, profile_id))


@router.patch("/profiles/{profile_id}", response_model=ProfileRead)
async def update_profile(
    profile_id: uuid.UUID, data: ProfileUpdate, session: AsyncSession = Depends(get_session)
) -> ProfileRead:
    return profile_service.to_read(await profile_service.update_profile(session, profile_id, data))


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await profile_service.delete_profile(session, profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
