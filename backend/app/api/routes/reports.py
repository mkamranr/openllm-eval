"""Reports, run comparisons, and re-runs (spec §9.6)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_redis, get_session
from app.core.config import Settings
from app.core.exceptions import AppError
from app.execution.queue import notify_run_queued
from app.reporting import compare as compare_mod
from app.reporting.compare_html import render_comparison_html
from app.schemas.common import Page
from app.schemas.report import (
    ComparisonCreate,
    ComparisonDetail,
    ComparisonRead,
    ReportCreate,
    ReportRead,
)
from app.schemas.run import RunDetail
from app.services import report_service, run_service

router = APIRouter(tags=["reports"])

# Reports contain model output; even though every value is escaped, the browser is told to run
# nothing and load nothing.
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


def _download(content: bytes, media_type: str, filename: str, inline: bool) -> Response:
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Security-Policy": REPORT_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/runs/{run_id}/reports", response_model=ReportRead, status_code=status.HTTP_201_CREATED
)
async def create_report(
    run_id: uuid.UUID,
    data: ReportCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> ReportRead:
    return await report_service.create_report(session, settings, run_id, data)


@router.get("/runs/{run_id}/reports", response_model=Page[ReportRead])
async def list_run_reports(
    run_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[ReportRead]:
    await run_service.get_run(session, run_id)
    return await report_service.list_reports(session, run_id=run_id, limit=limit, offset=offset)


@router.get("/reports", response_model=Page[ReportRead])
async def list_reports(
    run_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[ReportRead]:
    return await report_service.list_reports(session, run_id=run_id, limit=limit, offset=offset)


@router.get("/reports/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ReportRead:
    return await report_service.report_read(session, report_id)


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: uuid.UUID,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Response:
    report = await report_service.get_report(session, report_id)
    return _download(report.content, report.media_type, report.filename, inline)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await report_service.delete_report(session, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs/{run_id}/rerun", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def rerun(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    redis: Redis = Depends(get_redis),
) -> RunDetail:
    run = await report_service.rerun(session, settings, run_id)
    await notify_run_queued(redis, run.id)
    return run_service.to_detail(run)


# ------------------------------------------------------------------------------ comparisons


def _run_ids(value: str) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(v.strip()) for v in value.split(",") if v.strip()]
    except ValueError:
        raise AppError(
            "run_ids must be a comma-separated list of run ids.",
            code="invalid_comparison",
            status_code=422,
        ) from None


ExportFormat = Literal["json", "csv", "html"]


def _export(result: dict, fmt: ExportFormat, name: str, inline: bool) -> Response:
    if fmt == "csv":
        return _download(
            compare_mod.comparison_csv(result).encode(),
            "text/csv; charset=utf-8",
            f"{name}.csv",
            inline,
        )
    if fmt == "html":
        return _download(
            render_comparison_html(result, name).encode(),
            "text/html; charset=utf-8",
            f"{name}.html",
            inline,
        )
    return _download(
        compare_mod.comparison_json(result).encode(), "application/json", f"{name}.json", inline
    )


@router.get("/compare")
async def compare_runs(
    run_ids: str = Query(..., description="Comma-separated run ids"),
    baseline: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Compare runs without saving the comparison."""
    return await compare_mod.compare(session, _run_ids(run_ids), baseline)


@router.get("/compare/export")
async def export_ad_hoc(
    run_ids: str = Query(...),
    baseline: uuid.UUID | None = None,
    format: ExportFormat = "json",
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Response:
    result = await compare_mod.compare(session, _run_ids(run_ids), baseline)
    return _export(result, format, "comparison", inline)


@router.post("/comparisons", response_model=ComparisonDetail, status_code=status.HTTP_201_CREATED)
async def create_comparison(
    data: ComparisonCreate, session: AsyncSession = Depends(get_session)
) -> ComparisonDetail:
    return await report_service.create_comparison(session, data)


@router.get("/comparisons", response_model=Page[ComparisonRead])
async def list_comparisons(
    project_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[ComparisonRead]:
    return await report_service.list_comparisons(
        session, project_id=project_id, limit=limit, offset=offset
    )


@router.get("/comparisons/{comparison_id}", response_model=ComparisonDetail)
async def get_comparison(
    comparison_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ComparisonDetail:
    return await report_service.comparison_detail(session, comparison_id)


@router.get("/comparisons/{comparison_id}/export")
async def export_comparison(
    comparison_id: uuid.UUID,
    format: ExportFormat = "json",
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Response:
    detail = await report_service.comparison_detail(session, comparison_id)
    if detail.result is None:
        raise AppError(
            detail.error or "Comparison unavailable.",
            code="comparison_unavailable",
            status_code=409,
        )
    slug = report_service._slug(detail.name)
    return _export(detail.result, format, slug, inline)


@router.delete("/comparisons/{comparison_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comparison(
    comparison_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await report_service.delete_comparison(session, comparison_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
