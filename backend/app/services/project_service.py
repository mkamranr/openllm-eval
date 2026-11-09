from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.db.base import utcnow
from app.db.models import Dataset, ModelEndpoint, Project
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

Counts = dict[uuid.UUID, tuple[int, int]]


async def _counts(session: AsyncSession, ids: list[uuid.UUID]) -> Counts:
    """(endpoint_count, dataset_count) per project."""
    if not ids:
        return {}
    out: dict[uuid.UUID, list[int]] = {pid: [0, 0] for pid in ids}
    for index, model in enumerate((ModelEndpoint, Dataset)):
        rows = await session.execute(
            select(model.project_id, func.count())
            .where(model.project_id.in_(ids))
            .group_by(model.project_id)
        )
        for pid, count in rows.all():
            out[pid][index] = count
    return {pid: (e, d) for pid, (e, d) in out.items()}


def _read(project: Project, counts: tuple[int, int] = (0, 0)) -> ProjectRead:
    read = ProjectRead.model_validate(project)
    read.endpoint_count, read.dataset_count = counts
    return read


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found", details={"project_id": str(project_id)})
    return project


async def list_projects(
    session: AsyncSession, *, include_archived: bool, limit: int, offset: int
) -> Page[ProjectRead]:
    query = select(Project)
    if not include_archived:
        query = query.where(Project.archived_at.is_(None))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.scalars(query.order_by(Project.created_at.desc()).limit(limit).offset(offset))
    ).all()
    counts = await _counts(session, [p.id for p in rows])
    return Page(
        items=[_read(p, counts.get(p.id, (0, 0))) for p in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def read_project(session: AsyncSession, project_id: uuid.UUID) -> ProjectRead:
    project = await get_project(session, project_id)
    counts = await _counts(session, [project.id])
    return _read(project, counts.get(project.id, (0, 0)))


async def create_project(session: AsyncSession, data: ProjectCreate) -> ProjectRead:
    project = Project(name=data.name, description=data.description, tags=data.tags)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return _read(project)


async def update_project(
    session: AsyncSession, project_id: uuid.UUID, data: ProjectUpdate
) -> ProjectRead:
    project = await get_project(session, project_id)
    changes = data.model_dump(exclude_unset=True)
    archived = changes.pop("archived", None)
    for key, value in changes.items():
        if value is None and key == "name":
            continue
        setattr(project, key, value)
    if archived is True and project.archived_at is None:
        project.archived_at = utcnow()
    elif archived is False:
        project.archived_at = None
    await session.commit()
    await session.refresh(project)
    return await read_project(session, project.id)


async def delete_project(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Permanently delete a project; ON DELETE CASCADE removes its endpoints and datasets."""
    from app.services.run_service import active_runs_in_project

    project = await get_project(session, project_id)
    if active := await active_runs_in_project(session, project_id):
        raise AppError(
            f"{active} queued or running benchmark run(s) belong to this project. "
            "Cancel them or wait for them to finish.",
            code="project_in_use",
            status_code=409,
        )
    await session.delete(project)
    await session.commit()
