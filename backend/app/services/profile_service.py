"""Evaluation profiles: reusable run configurations (spec §8.1, §9.4).

Runs copy a profile's settings into their own immutable snapshot, so editing or deleting a
profile never changes a run that already used it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.db.models.evaluation import EvaluationProfile
from app.schemas.common import Page
from app.schemas.profile import ProfileCreate, ProfileRead, ProfileUpdate
from app.services.project_service import get_project


def to_read(p: EvaluationProfile) -> ProfileRead:
    return ProfileRead(
        id=p.id,
        project_id=p.project_id,
        name=p.name,
        description=p.description,
        suites=list(p.suites or []),
        evaluation=p.evaluator_configuration or {},
        generation=p.generation_configuration or {},
        load=p.load_configuration or {},
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _name_taken(name: str) -> AppError:
    return AppError(
        f"A profile named '{name}' already exists.",
        code="conflict",
        status_code=409,
        details={"field": "name"},
    )


async def get_profile(session: AsyncSession, profile_id: uuid.UUID) -> EvaluationProfile:
    profile = await session.get(EvaluationProfile, profile_id)
    if profile is None:
        raise NotFoundError("Profile not found", details={"profile_id": str(profile_id)})
    return profile


async def list_profiles(
    session: AsyncSession, *, project_id: uuid.UUID | None, limit: int, offset: int
) -> Page[ProfileRead]:
    conditions = [EvaluationProfile.project_id == project_id] if project_id else []
    total = await session.scalar(
        select(func.count()).select_from(EvaluationProfile).where(*conditions)
    )
    rows = (
        await session.scalars(
            select(EvaluationProfile)
            .where(*conditions)
            .order_by(func.lower(EvaluationProfile.name))
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(items=[to_read(p) for p in rows], total=total or 0, limit=limit, offset=offset)


async def create_profile(session: AsyncSession, data: ProfileCreate) -> EvaluationProfile:
    if data.project_id is not None:
        await get_project(session, data.project_id)
    profile = EvaluationProfile(
        project_id=data.project_id,
        name=data.name,
        description=data.description,
        suites=data.suites,
        evaluator_configuration=data.evaluation.model_dump(mode="json"),
        generation_configuration=data.generation.model_dump(mode="json"),
        load_configuration=data.load.model_dump(mode="json"),
    )
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _name_taken(data.name) from None
    await session.refresh(profile)
    return profile


async def update_profile(
    session: AsyncSession, profile_id: uuid.UUID, data: ProfileUpdate
) -> EvaluationProfile:
    profile = await get_profile(session, profile_id)
    fields = data.model_fields_set
    if "project_id" in fields and data.project_id is not None:
        await get_project(session, data.project_id)
    if "name" in fields and data.name:
        profile.name = data.name
    if "description" in fields:
        profile.description = data.description
    if "project_id" in fields:
        profile.project_id = data.project_id
    if data.suites is not None:
        profile.suites = data.suites
    if data.evaluation is not None:
        profile.evaluator_configuration = data.evaluation.model_dump(mode="json")
    if data.generation is not None:
        profile.generation_configuration = data.generation.model_dump(mode="json")
    if data.load is not None:
        profile.load_configuration = data.load.model_dump(mode="json")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _name_taken(data.name or profile.name) from None
    await session.refresh(profile)
    return profile


async def delete_profile(session: AsyncSession, profile_id: uuid.UUID) -> None:
    profile = await get_profile(session, profile_id)
    await session.delete(profile)
    await session.commit()
