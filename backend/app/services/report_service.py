"""Reports (immutable artifacts generated from finished runs), saved comparisons, and re-runs."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models import Comparison, Report
from app.db.models.run import TERMINAL_STATUSES, BenchmarkRun
from app.reporting import compare as compare_mod
from app.reporting.data import collect, rerun_request
from app.reporting.exports import FORMATS, render
from app.schemas.common import Page
from app.schemas.report import (
    ComparisonCreate,
    ComparisonDetail,
    ComparisonRead,
    ReportCreate,
    ReportRead,
)
from app.schemas.run import RunCreate
from app.services import run_service
from app.services.project_service import get_project


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:80] or "run"


def _read(report: Report, run_name: str | None = None) -> ReportRead:
    read = ReportRead.model_validate(report)
    read.run_name = run_name
    return read


async def create_report(
    session: AsyncSession, settings: Settings, run_id: uuid.UUID, data: ReportCreate
) -> ReportRead:
    run = await run_service.get_run(session, run_id)
    if run.status not in TERMINAL_STATUSES:
        raise AppError(
            "Reports can be generated once the run has finished.",
            code="run_not_finished",
            status_code=409,
            details={"status": run.status},
        )
    collected = await collect(
        session,
        run,
        redact_content=data.redact_content,
        redact_endpoints=data.redact_endpoints,
        max_results=settings.report_max_results,
    )
    content = render(collected, data.format)
    media_type, ext = FORMATS[data.format]
    report = Report(
        run_id=run.id,
        report_type=data.format,
        storage_location="database",
        filename=f"{_slug(run.name)}-{str(run.id)[:8]}.{ext}",
        media_type=media_type,
        content=content,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        options=data.model_dump(exclude={"format"}),
        report_metadata={
            "report_version": collected["report_version"],
            "framework_version": collected["framework_version"],
            "run_status": run.status,
            "results_included": len(collected["results"]),
            "results_total": collected["results_total"],
            "results_truncated": collected["results_truncated"],
            "warnings": len(collected["warnings"]),
        },
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return _read(report, run.name)


async def list_reports(
    session: AsyncSession, *, run_id: uuid.UUID | None, limit: int, offset: int
) -> Page[ReportRead]:
    conditions = [Report.run_id == run_id] if run_id else []
    total = await session.scalar(select(func.count()).select_from(Report).where(*conditions))
    rows = await session.execute(
        select(Report, BenchmarkRun.name)
        .join(BenchmarkRun, BenchmarkRun.id == Report.run_id)
        .where(*conditions)
        .order_by(Report.generated_at.desc(), Report.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[_read(r, name) for r, name in rows.all()],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_report(session: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFoundError("Report not found")
    return report


async def report_read(session: AsyncSession, report_id: uuid.UUID) -> ReportRead:
    report = await get_report(session, report_id)
    run = await session.get(BenchmarkRun, report.run_id)
    return _read(report, run.name if run else None)


async def delete_report(session: AsyncSession, report_id: uuid.UUID) -> None:
    await session.delete(await get_report(session, report_id))
    await session.commit()


# ------------------------------------------------------------------------------- comparisons


async def create_comparison(session: AsyncSession, data: ComparisonCreate) -> ComparisonDetail:
    if data.project_id is not None:
        await get_project(session, data.project_id)
    result = await compare_mod.compare(session, data.run_ids, data.baseline)
    comparison = Comparison(
        project_id=data.project_id,
        name=data.name,
        description=data.description,
        run_ids=[str(r) for r in data.run_ids],
        baseline_run_id=data.baseline,
    )
    session.add(comparison)
    await session.commit()
    await session.refresh(comparison)
    return ComparisonDetail(**ComparisonRead.model_validate(comparison).model_dump(), result=result)


async def list_comparisons(
    session: AsyncSession, *, project_id: uuid.UUID | None, limit: int, offset: int
) -> Page[ComparisonRead]:
    conditions = [Comparison.project_id == project_id] if project_id else []
    total = await session.scalar(select(func.count()).select_from(Comparison).where(*conditions))
    rows = (
        await session.scalars(
            select(Comparison)
            .where(*conditions)
            .order_by(Comparison.created_at.desc(), Comparison.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[ComparisonRead.model_validate(r) for r in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_comparison(session: AsyncSession, comparison_id: uuid.UUID) -> Comparison:
    comparison = await session.get(Comparison, comparison_id)
    if comparison is None:
        raise NotFoundError("Comparison not found")
    return comparison


async def comparison_detail(session: AsyncSession, comparison_id: uuid.UUID) -> ComparisonDetail:
    """Computed on read from the runs' stored results, so it reflects retries since saving."""
    comparison = await get_comparison(session, comparison_id)
    base = ComparisonRead.model_validate(comparison).model_dump()
    try:
        result = await compare_mod.compare(
            session, [uuid.UUID(r) for r in comparison.run_ids], comparison.baseline_run_id
        )
    except NotFoundError:
        return ComparisonDetail(**base, error="A compared run has been deleted.")
    return ComparisonDetail(**base, result=result)


async def delete_comparison(session: AsyncSession, comparison_id: uuid.UUID) -> None:
    await session.delete(await get_comparison(session, comparison_id))
    await session.commit()


# ------------------------------------------------------------------------------------ rerun


async def rerun(session: AsyncSession, settings: Settings, run_id: uuid.UUID) -> BenchmarkRun:
    """Create a new run with the same configuration (spec §11.3: reproducibility)."""
    run = await run_service.get_run(session, run_id)
    if any(m.endpoint_id is None for m in run.models):
        raise AppError(
            "An endpoint used by this run has been deleted, so it cannot be re-run.",
            code="rerun_unavailable",
            status_code=409,
        )
    body: dict[str, Any] = rerun_request(run)
    if any(d["version_id"] is None for d in body["datasets"]) or any(
        rd.dataset_version_id is None for rd in run.datasets
    ):
        raise AppError(
            "A dataset version used by this run has been deleted, so it cannot be re-run.",
            code="rerun_unavailable",
            status_code=409,
        )
    try:
        data = RunCreate.model_validate(body)
    except ValidationError as exc:
        raise AppError(
            "This run's configuration is no longer valid for a new run.",
            code="rerun_unavailable",
            status_code=409,
            details=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()],
        ) from None
    return await run_service.create_run(session, settings, data)
