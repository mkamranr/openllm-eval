"""Dataset management: validation, immutable versions, preview, export, samples (spec §5.3)."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from sqlalchemy import func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.datasets import generators
from app.datasets.samples import LICENSE as SAMPLE_LICENSE
from app.datasets.samples import SAMPLES
from app.datasets.samples import SOURCE as SAMPLE_SOURCE
from app.datasets.schema import SCHEMA_VERSION
from app.datasets.validation import (
    PREVIEW_SIZE,
    DatasetFormatError,
    Limits,
    ValidationReport,
    canonical_line,
    validate_bytes,
)
from app.db.models import Dataset, DatasetCase, DatasetVersion
from app.db.models.dataset import TAG_SEPARATOR, tag_index
from app.schemas.common import Page
from app.schemas.dataset import (
    CaseRead,
    DatasetCreate,
    DatasetDuplicate,
    DatasetRead,
    DatasetUpdate,
    DatasetVersionRead,
    IssueRead,
    SampleRead,
    ValidationLimits,
    ValidationResult,
)
from app.services.project_service import get_project

INSERT_BATCH = 1000
PREVIEW_TEXT_LIMIT = 1000


def limits_from(settings: Settings) -> Limits:
    return Limits(
        max_records=settings.dataset_max_records,
        max_record_bytes=settings.dataset_max_record_bytes,
    )


# --------------------------------------------------------------------------- validation


def validate_upload(
    settings: Settings,
    raw: bytes,
    *,
    dataset_type: str,
    filename: str | None,
    fmt: str | None,
) -> ValidationReport:
    if len(raw) > settings.dataset_max_upload_bytes:
        raise AppError(
            f"The upload is {len(raw)} bytes; the limit is {settings.dataset_max_upload_bytes} "
            "(DATASET_MAX_UPLOAD_BYTES).",
            code="payload_too_large",
            status_code=413,
        )
    try:
        return validate_bytes(
            raw,
            dataset_type=dataset_type,
            limits=limits_from(settings),
            filename=filename,
            fmt=fmt,
        )
    except DatasetFormatError as exc:
        raise AppError(str(exc), code="unsupported_dataset_format", status_code=415) from None


def to_result(report: ValidationReport, settings: Settings, dataset_type: str) -> ValidationResult:
    def issues(items: Sequence[Any]) -> list[IssueRead]:
        return [
            IssueRead(line=i.line, case_id=i.case_id, field=i.field, message=i.message)
            for i in items
        ]

    return ValidationResult(
        valid=report.valid,
        format=report.format,
        dataset_type=dataset_type,  # type: ignore[arg-type]
        schema_version=SCHEMA_VERSION,
        rows=report.rows,
        record_count=report.record_count,
        error_count=report.error_count,
        warning_count=report.warning_count,
        errors=issues(report.errors),
        warnings=issues(report.warnings),
        type_counts=dict(report.type_counts),
        tag_counts=dict(report.tag_counts.most_common()),
        split_counts=dict(report.split_counts),
        content_hash=report.content_hash,
        preview=[_truncate(c.content)[0] for c in report.cases[:PREVIEW_SIZE]],
        limits=ValidationLimits(
            max_upload_bytes=settings.dataset_max_upload_bytes,
            max_records=settings.dataset_max_records,
            max_record_bytes=settings.dataset_max_record_bytes,
        ),
    )


def _invalid(report: ValidationReport, settings: Settings, dataset_type: str) -> AppError:
    noun = "error" if report.error_count == 1 else "errors"
    return AppError(
        f"The dataset has {report.error_count} validation {noun}; nothing was imported.",
        code="dataset_invalid",
        status_code=422,
        details=to_result(report, settings, dataset_type).model_dump(mode="json"),
    )


# --------------------------------------------------------------------------- reads


def _version_read(version: DatasetVersion, current_id: uuid.UUID | None) -> DatasetVersionRead:
    read = DatasetVersionRead.model_validate(version)
    read.is_current = version.id == current_id
    return read


async def _version_counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await session.execute(
        select(DatasetVersion.dataset_id, func.count())
        .where(DatasetVersion.dataset_id.in_(ids))
        .group_by(DatasetVersion.dataset_id)
    )
    return {did: count for did, count in rows.all()}


async def _to_reads(session: AsyncSession, datasets: Sequence[Dataset]) -> list[DatasetRead]:
    current_ids = [d.current_version_id for d in datasets if d.current_version_id]
    versions: dict[uuid.UUID, DatasetVersion] = {}
    if current_ids:
        rows = await session.scalars(
            select(DatasetVersion).where(DatasetVersion.id.in_(current_ids))
        )
        versions = {v.id: v for v in rows}
    counts = await _version_counts(session, [d.id for d in datasets])
    out = []
    for d in datasets:
        current = versions.get(d.current_version_id) if d.current_version_id else None
        out.append(
            DatasetRead(
                id=d.id,
                project_id=d.project_id,
                name=d.name,
                description=d.description,
                dataset_type=d.dataset_type,  # type: ignore[arg-type]
                tags=d.tags,
                source=d.source,
                sample_id=d.sample_id,
                license=d.license,
                current_version=_version_read(current, d.current_version_id) if current else None,
                version_count=counts.get(d.id, 0),
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
        )
    return out


async def get_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError("Dataset not found", details={"dataset_id": str(dataset_id)})
    return dataset


async def read_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> DatasetRead:
    return (await _to_reads(session, [await get_dataset(session, dataset_id)]))[0]


async def list_datasets(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    dataset_type: str | None,
    tag: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> Page[DatasetRead]:
    query = select(Dataset)
    if project_id is not None:
        query = query.where(Dataset.project_id == project_id)
    if dataset_type is not None:
        query = query.where(Dataset.dataset_type == dataset_type)
    if search:
        query = query.where(func.lower(Dataset.name).contains(search.lower(), autoescape=True))
    rows: Sequence[Dataset]
    if tag:
        # Dataset-level tags are a short JSON list; filter in Python to stay portable.
        all_rows = (await session.scalars(query.order_by(Dataset.created_at.desc()))).all()
        matching = [d for d in all_rows if tag in d.tags]
        total, rows = len(matching), matching[offset : offset + limit]
    else:
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            await session.scalars(
                query.order_by(Dataset.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    return Page(items=await _to_reads(session, rows), total=total, limit=limit, offset=offset)


async def list_versions(session: AsyncSession, dataset_id: uuid.UUID) -> list[DatasetVersionRead]:
    dataset = await get_dataset(session, dataset_id)
    rows = await session.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number.desc())
    )
    return [_version_read(v, dataset.current_version_id) for v in rows]


async def resolve_version(
    session: AsyncSession, dataset: Dataset, version_id: uuid.UUID | None
) -> DatasetVersion:
    target = version_id or dataset.current_version_id
    if target is None:
        raise NotFoundError(
            "The dataset has no versions yet", details={"dataset_id": str(dataset.id)}
        )
    version = await session.get(DatasetVersion, target)
    if version is None or version.dataset_id != dataset.id:
        raise NotFoundError("Dataset version not found", details={"version_id": str(target)})
    return version


async def get_version(
    session: AsyncSession, dataset_id: uuid.UUID, version_id: uuid.UUID
) -> DatasetVersionRead:
    dataset = await get_dataset(session, dataset_id)
    version = await resolve_version(session, dataset, version_id)
    return _version_read(version, dataset.current_version_id)


def _truncate(value: Any, limit: int = PREVIEW_TEXT_LIMIT) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) > limit:
            return value[:limit] + f"… [{len(value) - limit} more characters]", True
        return value, False
    if isinstance(value, list):
        items = [_truncate(v, limit) for v in value]
        return [v for v, _ in items], any(t for _, t in items)
    if isinstance(value, dict):
        pairs = {k: _truncate(v, limit) for k, v in value.items()}
        return {k: v for k, (v, _) in pairs.items()}, any(t for _, t in pairs.values())
    return value, False


def _case_read(case: DatasetCase, truncate: bool) -> CaseRead:
    content, truncated = _truncate(case.content) if truncate else (case.content, False)
    return CaseRead(
        position=case.position,
        case_identifier=case.case_identifier,
        case_type=case.case_type,
        tags=case.tags,
        content=content,
        truncated=truncated,
    )


def _like_escape(text: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", text)


async def preview_cases(
    session: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    version_id: uuid.UUID | None,
    tag: str | None,
    case_type: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> Page[CaseRead]:
    dataset = await get_dataset(session, dataset_id)
    version = await resolve_version(session, dataset, version_id)
    query = select(DatasetCase).where(DatasetCase.dataset_version_id == version.id)
    if tag:
        query = query.where(
            DatasetCase.tag_index.like(
                f"%{TAG_SEPARATOR}{_like_escape(tag)}{TAG_SEPARATOR}%", escape="\\"
            )
        )
    if case_type:
        query = query.where(DatasetCase.case_type == case_type)
    if search:
        pattern = f"%{_like_escape(search.lower())}%"
        query = query.where(or_(func.lower(DatasetCase.case_identifier).like(pattern, escape="\\")))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(query.order_by(DatasetCase.position).limit(limit).offset(offset))
    return Page(
        items=[_case_read(c, truncate=True) for c in rows], total=total, limit=limit, offset=offset
    )


async def get_case(
    session: AsyncSession,
    dataset_id: uuid.UUID,
    case_identifier: str,
    version_id: uuid.UUID | None,
) -> CaseRead:
    dataset = await get_dataset(session, dataset_id)
    version = await resolve_version(session, dataset, version_id)
    case = await session.scalar(
        select(DatasetCase).where(
            DatasetCase.dataset_version_id == version.id,
            DatasetCase.case_identifier == case_identifier,
        )
    )
    if case is None:
        raise NotFoundError("Case not found", details={"case_identifier": case_identifier})
    return _case_read(case, truncate=False)


async def export_lines(
    session_factory: async_sessionmaker[AsyncSession], version_id: uuid.UUID
) -> AsyncIterator[bytes]:
    """Yield the normalized JSONL export; its SHA-256 equals the version's content_hash.

    Uses its own session: a streaming body outlives the request-scoped session dependency.
    """
    position = -1
    async with session_factory() as session:
        while True:
            rows = (
                await session.scalars(
                    select(DatasetCase)
                    .where(
                        DatasetCase.dataset_version_id == version_id,
                        DatasetCase.position > position,
                    )
                    .order_by(DatasetCase.position)
                    .limit(INSERT_BATCH)
                )
            ).all()
            if not rows:
                return
            yield "".join(canonical_line(c.content) for c in rows).encode("utf-8")
            position = rows[-1].position


# --------------------------------------------------------------------------- writes


def _conflict(message: str, **details: Any) -> AppError:
    return AppError(message, code="conflict", status_code=409, details=details or None)


async def _ensure_name_free(
    session: AsyncSession, name: str, exclude: uuid.UUID | None = None
) -> None:
    query = select(Dataset.id).where(Dataset.name == name)
    if exclude:
        query = query.where(Dataset.id != exclude)
    if await session.scalar(query):
        raise _conflict(f"A dataset named '{name}' already exists.")


async def _insert_version(
    session: AsyncSession,
    dataset: Dataset,
    report: ValidationReport,
    *,
    version_label: str | None,
    filename: str | None,
) -> DatasetVersion:
    assert report.content_hash is not None  # noqa: S101 - only called for valid reports
    existing = await session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.content_hash == report.content_hash,
        )
    )
    if existing is not None:
        raise _conflict(
            f"This content is identical to version {existing.version_number} "
            f"(sha256 {existing.content_hash[:12]}…); no new version was created.",
            version_id=str(existing.id),
            version_number=existing.version_number,
        )
    latest = await session.scalar(
        select(func.max(DatasetVersion.version_number)).where(
            DatasetVersion.dataset_id == dataset.id
        )
    )
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_number=(latest or 0) + 1,
        version_label=version_label,
        content_hash=report.content_hash,
        record_count=report.record_count,
        schema_version=SCHEMA_VERSION,
        storage_location=None,
        source_format=report.format,
        source_filename=filename,
        type_counts=dict(report.type_counts),
        tag_counts=dict(report.tag_counts.most_common()),
        split_counts=dict(report.split_counts),
        warning_count=report.warning_count,
    )
    session.add(version)
    await session.flush()
    rows = [
        {
            "dataset_version_id": version.id,
            "position": i,
            "case_identifier": c.case_identifier,
            "case_type": c.case_type,
            "tags": c.tags,
            "tag_index": tag_index(c.tags),
            "content": c.content,
        }
        for i, c in enumerate(report.cases)
    ]
    for start in range(0, len(rows), INSERT_BATCH):
        await session.execute(insert(DatasetCase), rows[start : start + INSERT_BATCH])
    dataset.current_version_id = version.id
    return version


async def _commit(session: AsyncSession, name: str) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _conflict(
            f"Could not save dataset '{name}': a dataset with that name or an identical version "
            "was created at the same time. Reload and try again."
        ) from None


async def create_dataset(
    session: AsyncSession,
    settings: Settings,
    data: DatasetCreate,
    raw: bytes | None,
    *,
    source: str = "user",
    sample_id: str | None = None,
    license_: str | None = None,
) -> DatasetRead:
    if data.project_id is not None:
        await get_project(session, data.project_id)
    await _ensure_name_free(session, data.name)
    report = None
    if raw is not None:
        report = validate_upload(
            settings, raw, dataset_type=data.dataset_type, filename=data.filename, fmt=data.format
        )
        if not report.valid:
            raise _invalid(report, settings, data.dataset_type)
    dataset = Dataset(
        project_id=data.project_id,
        name=data.name,
        description=data.description,
        dataset_type=data.dataset_type,
        tags=data.tags,
        source=source,
        sample_id=sample_id,
        license=license_,
    )
    session.add(dataset)
    await session.flush()
    if report is not None:
        await _insert_version(
            session, dataset, report, version_label=data.version_label, filename=data.filename
        )
    await _commit(session, data.name)
    return await read_dataset(session, dataset.id)


async def create_version(
    session: AsyncSession,
    settings: Settings,
    dataset_id: uuid.UUID,
    raw: bytes,
    *,
    version_label: str | None,
    filename: str | None,
    fmt: str | None,
) -> DatasetVersionRead:
    dataset = await get_dataset(session, dataset_id)
    report = validate_upload(
        settings, raw, dataset_type=dataset.dataset_type, filename=filename, fmt=fmt
    )
    if not report.valid:
        raise _invalid(report, settings, dataset.dataset_type)
    version = await _insert_version(
        session, dataset, report, version_label=version_label, filename=filename
    )
    await _commit(session, dataset.name)
    await session.refresh(version)
    return _version_read(version, version.id)


async def update_dataset(
    session: AsyncSession, dataset_id: uuid.UUID, data: DatasetUpdate
) -> DatasetRead:
    dataset = await get_dataset(session, dataset_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") and changes["name"] != dataset.name:
        await _ensure_name_free(session, changes["name"], exclude=dataset.id)
    if "project_id" in changes and changes["project_id"] is not None:
        await get_project(session, changes["project_id"])
    if "current_version_id" in changes:
        if changes["current_version_id"] is None:
            raise AppError(
                "current_version_id cannot be cleared.", code="validation_error", status_code=422
            )
        await resolve_version(session, dataset, changes["current_version_id"])
    for key, value in changes.items():
        if value is None and key in ("name", "tags"):
            continue
        setattr(dataset, key, value)
    await _commit(session, dataset.name)
    return await read_dataset(session, dataset.id)


async def delete_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> None:
    from app.services.run_service import active_runs_using_datasets

    dataset = await get_dataset(session, dataset_id)
    if active := await active_runs_using_datasets(session, dataset_id):
        raise AppError(
            f"{active} queued or running benchmark run(s) use this dataset. "
            "Cancel them or wait for them to finish.",
            code="dataset_in_use",
            status_code=409,
        )
    # Break the circular FK first so the delete order doesn't matter on any database.
    dataset.current_version_id = None
    await session.flush()
    await session.delete(dataset)
    await session.commit()


async def duplicate_dataset(
    session: AsyncSession, dataset_id: uuid.UUID, data: DatasetDuplicate
) -> DatasetRead:
    source = await get_dataset(session, dataset_id)
    version = await resolve_version(session, source, data.version_id)
    name = data.name or await _copy_name(session, source.name)
    await _ensure_name_free(session, name)
    copy = Dataset(
        project_id=source.project_id,
        name=name,
        description=source.description,
        dataset_type=source.dataset_type,
        tags=list(source.tags),
        source="user",
        sample_id=None,
        license=source.license,
    )
    session.add(copy)
    await session.flush()
    new_version = DatasetVersion(
        dataset_id=copy.id,
        version_number=1,
        version_label=f"copy of {source.name} v{version.version_number}",
        content_hash=version.content_hash,
        record_count=version.record_count,
        schema_version=version.schema_version,
        storage_location=None,
        source_format=version.source_format,
        source_filename=version.source_filename,
        type_counts=dict(version.type_counts),
        tag_counts=dict(version.tag_counts),
        split_counts=dict(version.split_counts),
        warning_count=version.warning_count,
    )
    session.add(new_version)
    await session.flush()
    position = -1
    while True:
        cases = (
            await session.scalars(
                select(DatasetCase)
                .where(
                    DatasetCase.dataset_version_id == version.id, DatasetCase.position > position
                )
                .order_by(DatasetCase.position)
                .limit(INSERT_BATCH)
            )
        ).all()
        if not cases:
            break
        await session.execute(
            insert(DatasetCase),
            [
                {
                    "dataset_version_id": new_version.id,
                    "position": c.position,
                    "case_identifier": c.case_identifier,
                    "case_type": c.case_type,
                    "tags": c.tags,
                    "tag_index": c.tag_index,
                    "content": c.content,
                }
                for c in cases
            ],
        )
        position = cases[-1].position
    copy.current_version_id = new_version.id
    await _commit(session, name)
    return await read_dataset(session, copy.id)


async def _copy_name(session: AsyncSession, base: str) -> str:
    candidate = f"{base} (copy)"[:200]
    n = 2
    while await session.scalar(select(Dataset.id).where(Dataset.name == candidate)):
        candidate = f"{base} (copy) {n}"[:200]
        n += 1
    return candidate


# --------------------------------------------------------------------------- samples


async def list_samples(session: AsyncSession, settings: Settings) -> list[SampleRead]:
    installed = {
        sample_id: did
        for sample_id, did in (
            await session.execute(
                select(Dataset.sample_id, Dataset.id).where(Dataset.sample_id.is_not(None))
            )
        ).all()
    }
    out = []
    for sample in SAMPLES.values():
        report = validate_bytes(
            sample.read_bytes(),
            dataset_type=sample.dataset_type,
            limits=limits_from(settings),
            filename=sample.filename,
        )
        out.append(
            SampleRead(
                sample_id=sample.sample_id,
                name=sample.name,
                description=sample.description,
                dataset_type=sample.dataset_type,  # type: ignore[arg-type]
                record_count=report.record_count,
                license=SAMPLE_LICENSE,
                source=SAMPLE_SOURCE,
                installed_dataset_id=installed.get(sample.sample_id),
            )
        )
    return out


async def install_sample(
    session: AsyncSession, settings: Settings, sample_id: str, project_id: uuid.UUID | None
) -> DatasetRead:
    sample = SAMPLES.get(sample_id)
    if sample is None:
        raise NotFoundError("Sample not found", details={"sample_id": sample_id})
    existing = await session.scalar(select(Dataset.id).where(Dataset.sample_id == sample_id))
    if existing is not None:
        raise _conflict(
            f"The sample '{sample.name}' is already installed.", dataset_id=str(existing)
        )
    data = DatasetCreate(
        name=sample.name,
        description=f"{sample.description}\n\nSource: {SAMPLE_SOURCE}. License: {SAMPLE_LICENSE}.",
        project_id=project_id,
        dataset_type=sample.dataset_type,  # type: ignore[arg-type]
        tags=list(sample.tags),
        filename=sample.filename,
        version_label="bundled sample",
    )
    return await create_dataset(
        session,
        settings,
        data,
        sample.read_bytes(),
        source="sample",
        sample_id=sample.sample_id,
        license_=SAMPLE_LICENSE,
    )


async def generate_dataset(
    session: AsyncSession, settings: Settings, request: generators.GenerateRequest
) -> DatasetRead:
    """Create a synthetic context-window dataset (deterministic for the same parameters)."""
    try:
        cases = generators.generate_needle_cases(request.params)
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=422) from None
    description = generators.describe(request.params)
    if request.description:
        description = f"{request.description}\n\n{description}"
    data = DatasetCreate(
        name=request.name,
        description=description,
        project_id=request.project_id,
        dataset_type="long_context",
        tags=list(dict.fromkeys([*request.tags, "generated", "context-window"])),
        filename="generated-needle.jsonl",
        version_label=f"needle v{generators.GENERATOR_VERSION} seed {request.params.seed}",
    )
    raw = generators.to_jsonl(cases).encode("utf-8")
    return await create_dataset(session, settings, data, raw, source="generated")
