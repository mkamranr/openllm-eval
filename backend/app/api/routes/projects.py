from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectRead])
async def list_projects(
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[ProjectRead]:
    return await project_service.list_projects(
        session, include_archived=include_archived, limit=limit, offset=offset
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate, session: AsyncSession = Depends(get_session)
) -> ProjectRead:
    return await project_service.create_project(session, data)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ProjectRead:
    return await project_service.read_project(session, project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID, data: ProjectUpdate, session: AsyncSession = Depends(get_session)
) -> ProjectRead:
    return await project_service.update_project(session, project_id, data)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project and its endpoints",
)
async def delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await project_service.delete_project(session, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
