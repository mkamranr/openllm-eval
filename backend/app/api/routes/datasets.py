from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_session
from app.api.uploads import read_upload
from app.core.config import Settings
from app.core.exceptions import AppError
from app.datasets.schema import CASE_TYPES
from app.schemas.common import Page
from app.schemas.dataset import (
    CaseRead,
    DatasetCreate,
    DatasetDuplicate,
    DatasetRead,
    DatasetType,
    DatasetUpdate,
    DatasetVersionRead,
    SampleRead,
    ValidateRequest,
    ValidationResult,
    VersionCreate,
)
from app.services import dataset_service

router = APIRouter(tags=["datasets"])

_UPLOAD_DOC = (
    "Body: JSON with the dataset text in `content`, or multipart/form-data with the file in a "
    "`file` part and the other fields as form fields."
)


@router.get("/datasets", response_model=Page[DatasetRead])
async def list_datasets(
    project_id: uuid.UUID | None = None,
    dataset_type: DatasetType | None = None,
    tag: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200, description="Name contains (case-insensitive)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[DatasetRead]:
    return await dataset_service.list_datasets(
        session,
        project_id=project_id,
        dataset_type=dataset_type,
        tag=tag,
        search=q,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/datasets/validate",
    response_model=ValidationResult,
    summary="Validate dataset content without saving it",
    description=_UPLOAD_DOC,
)
async def validate_dataset(
    request: Request, settings: Settings = Depends(get_app_settings)
) -> ValidationResult:
    upload = await read_upload(request, ValidateRequest, settings)
    if upload.raw is None:
        raise AppError(
            "Provide dataset content or a file.", code="validation_error", status_code=422
        )
    data = upload.data
    report = dataset_service.validate_upload(
        settings,
        upload.raw,
        dataset_type=data.dataset_type,
        filename=data.filename,
        fmt=data.format,
    )
    return dataset_service.to_result(report, settings, data.dataset_type)


@router.get("/datasets/samples", response_model=list[SampleRead], summary="Bundled sample datasets")
async def list_samples(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> list[SampleRead]:
    return await dataset_service.list_samples(session, settings)


@router.post(
    "/datasets/samples/{sample_id}/install",
    response_model=DatasetRead,
    status_code=status.HTTP_201_CREATED,
)
async def install_sample(
    sample_id: str,
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> DatasetRead:
    return await dataset_service.install_sample(session, settings, sample_id, project_id)


@router.get("/datasets/case-types", summary="Supported case types")
async def case_types() -> dict[str, str]:
    return CASE_TYPES


@router.post(
    "/datasets",
    response_model=DatasetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dataset, optionally with its first version",
    description=_UPLOAD_DOC,
)
async def create_dataset(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> DatasetRead:
    upload = await read_upload(request, DatasetCreate, settings)
    return await dataset_service.create_dataset(session, settings, upload.data, upload.raw)


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
async def get_dataset(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DatasetRead:
    return await dataset_service.read_dataset(session, dataset_id)


@router.patch("/datasets/{dataset_id}", response_model=DatasetRead)
async def update_dataset(
    dataset_id: uuid.UUID, data: DatasetUpdate, session: AsyncSession = Depends(get_session)
) -> DatasetRead:
    return await dataset_service.update_dataset(session, dataset_id, data)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await dataset_service.delete_dataset(session, dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/datasets/{dataset_id}/duplicate",
    response_model=DatasetRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_dataset(
    dataset_id: uuid.UUID,
    data: DatasetDuplicate = Body(default_factory=DatasetDuplicate),
    session: AsyncSession = Depends(get_session),
) -> DatasetRead:
    return await dataset_service.duplicate_dataset(session, dataset_id, data)


@router.get("/datasets/{dataset_id}/versions", response_model=list[DatasetVersionRead])
async def list_versions(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[DatasetVersionRead]:
    return await dataset_service.list_versions(session, dataset_id)


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=DatasetVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new immutable version (becomes the current version)",
    description=_UPLOAD_DOC,
)
async def create_version(
    dataset_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> DatasetVersionRead:
    upload = await read_upload(request, VersionCreate, settings)
    if upload.raw is None:
        raise AppError(
            "Provide dataset content or a file.", code="validation_error", status_code=422
        )
    data = upload.data
    return await dataset_service.create_version(
        session,
        settings,
        dataset_id,
        upload.raw,
        version_label=data.version_label,
        filename=data.filename,
        fmt=data.format,
    )


@router.get("/datasets/{dataset_id}/versions/{version_id}", response_model=DatasetVersionRead)
async def get_version(
    dataset_id: uuid.UUID, version_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DatasetVersionRead:
    return await dataset_service.get_version(session, dataset_id, version_id)


@router.get(
    "/datasets/{dataset_id}/preview",
    response_model=Page[CaseRead],
    summary="Page through cases (long text truncated); filter by tag, type, or case ID",
)
async def preview(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID | None = None,
    tag: str | None = Query(None, max_length=64),
    case_type: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=200, description="Case ID contains"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CaseRead]:
    return await dataset_service.preview_cases(
        session,
        dataset_id,
        version_id=version_id,
        tag=tag,
        case_type=case_type,
        search=q,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/datasets/{dataset_id}/cases/{case_identifier:path}",
    response_model=CaseRead,
    summary="One full record",
)
async def get_case(
    dataset_id: uuid.UUID,
    case_identifier: str,
    version_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> CaseRead:
    return await dataset_service.get_case(session, dataset_id, case_identifier, version_id)


def _filename(name: str, version_number: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.").lower() or "dataset"
    return f"{slug[:80]}-v{version_number}.jsonl"


@router.get(
    "/datasets/{dataset_id}/export",
    summary="Normalized JSONL export; its SHA-256 equals the version's content_hash",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
async def export_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    version_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    dataset = await dataset_service.get_dataset(session, dataset_id)
    version = await dataset_service.resolve_version(session, dataset, version_id)
    return StreamingResponse(
        dataset_service.export_lines(request.app.state.session_factory, version.id),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename(dataset.name, version.version_number)}"'
            ),
            "X-Content-SHA256": version.content_hash,
            "X-Dataset-Version": str(version.version_number),
        },
    )
