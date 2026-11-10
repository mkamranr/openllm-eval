"""Benchmark run routes (spec §9.5), including Server-Sent Events for live progress."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_redis, get_session
from app.core.config import Settings
from app.db.models.run import (
    CANCEL_REQUESTED,
    CANCELLED,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    RUN_STATUSES,
    RUNNING,
    TERMINAL_STATUSES,
    RunCaseResult,
)
from app.execution.queue import notify_run_queued
from app.schemas.common import Page
from app.schemas.run import (
    CaseResultDetail,
    CaseResultRead,
    RunCreate,
    RunDetail,
    RunMetrics,
    RunRead,
    RunSummary,
    RunTelemetry,
)
from app.services import run_service

router = APIRouter(tags=["runs"])

KEEPALIVE_SECONDS = 15.0
MAX_CASE_EVENTS_PER_POLL = 200
STATUS_EVENTS = {
    RUNNING: "run.started",
    CANCEL_REQUESTED: "run.cancel_requested",
    COMPLETED: "run.completed",
    COMPLETED_WITH_ERRORS: "run.completed",
    CANCELLED: "run.cancelled",
    FAILED: "run.failed",
}


@router.get("/runs", response_model=Page[RunRead])
async def list_runs(
    project_id: uuid.UUID | None = None,
    status_filter: list[Literal[RUN_STATUSES]] | None = Query(None, alias="status"),  # type: ignore[valid-type]
    q: str | None = Query(None, max_length=200, description="Name contains"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[RunRead]:
    return await run_service.list_runs(
        session, project_id=project_id, status=status_filter, q=q, limit=limit, offset=offset
    )


@router.get("/runs/summary", response_model=RunSummary, summary="Run counts by status")
async def runs_summary(session: AsyncSession = Depends(get_session)) -> RunSummary:
    return await run_service.summary(session)


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def create_run(
    data: RunCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    redis: Redis = Depends(get_redis),
) -> RunDetail:
    run = await run_service.create_run(session, settings, data)
    await notify_run_queued(redis, run.id)
    return run_service.to_detail(run)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> RunDetail:
    return run_service.to_detail(await run_service.get_run(session, run_id))


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Response:
    await run_service.delete_run(session, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> RunDetail:
    return run_service.to_detail(await run_service.cancel_run(session, run_id))


@router.post("/runs/{run_id}/retry-failed", response_model=RunDetail)
async def retry_failed(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RunDetail:
    run = await run_service.retry_failed(session, run_id)
    await notify_run_queued(redis, run.id)
    return run_service.to_detail(run)


@router.get("/runs/{run_id}/results", response_model=Page[CaseResultRead])
async def list_results(
    run_id: uuid.UUID,
    run_model_id: uuid.UUID | None = None,
    status_filter: Literal["succeeded", "failed"] | None = Query(None, alias="status"),
    phase: Literal["warmup", "measured"] | None = None,
    final_only: bool = Query(False, description="Only the deciding attempt of each request"),
    q: str | None = Query(None, max_length=200, description="Case id or error type contains"),
    evaluator: str | None = Query(
        None, max_length=64, description="Only results evaluated by this evaluator"
    ),
    evaluation: Literal["passed", "failed", "not_applicable", "skipped", "requires_review"]
    | None = Query(
        None,
        description="Only results with an evaluation in this state (any evaluator, or `evaluator`)",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CaseResultRead]:
    return await run_service.list_results(
        session,
        run_id,
        run_model_id=run_model_id,
        status=status_filter,
        phase=phase,
        final_only=final_only,
        q=q,
        limit=limit,
        offset=offset,
        evaluator=evaluator,
        evaluation=evaluation,
    )


@router.get("/runs/{run_id}/results/{result_id}", response_model=CaseResultDetail)
async def get_result(
    run_id: uuid.UUID, result_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> CaseResultDetail:
    return await run_service.get_result(session, run_id, result_id)


@router.get(
    "/runs/{run_id}/telemetry",
    response_model=RunTelemetry,
    summary="Resource telemetry samples (GPU memory, utilization, power, KV cache)",
)
async def run_telemetry(
    run_id: uuid.UUID,
    run_model_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> RunTelemetry:
    return await run_service.run_telemetry(session, run_id, run_model_id)


@router.get("/runs/{run_id}/metrics", response_model=RunMetrics)
async def run_metrics(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> RunMetrics:
    return await run_service.run_metrics(session, run_id)


# ---------------------------------------------------------------------------------- events


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(jsonable_encoder(data), separators=(",", ":"))
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


def _case_event(row: RunCaseResult) -> dict[str, Any]:
    return {
        "id": row.id,
        "sequence": row.sequence,
        "run_model_id": row.run_model_id,
        "case_identifier": row.case_identifier,
        "phase": row.phase,
        "request_index": row.request_index,
        "attempt_number": row.attempt_number,
        "is_final": row.is_final,
        "status": row.status,
        "error_type": row.error_type,
        "http_status": row.http_status,
        "latency_ms": row.latency_ms,
        "ttft_ms": row.ttft_ms,
        "output_tokens": (row.performance_metrics or {}).get("output_tokens"),
        "completed_at": row.completed_at,
    }


@router.get(
    "/runs/{run_id}/events",
    summary="Live run progress (Server-Sent Events)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    last_event_id: str | None = Header(None),
    after: int | None = Query(None, ge=0, description="Resume after this result sequence"),
) -> StreamingResponse:
    """Events: ``run.progress`` (full run state, on connect and whenever it changes), ``run.*``
    status transitions, ``case.completed`` / ``case.failed`` per recorded attempt (``id`` = result
    sequence, so reconnecting clients resume via ``Last-Event-ID``), and ``metric.updated`` once
    final metrics are stored. The stream ends after the run reaches a terminal status."""
    await run_service.get_run(session, run_id)  # 404 before streaming starts
    cursor = after or 0
    if last_event_id and last_event_id.isdigit():
        cursor = int(last_event_id)
    factory = request.app.state.session_factory
    interval = settings.run_progress_interval_seconds

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        last_progress: str | None = None
        last_status: str | None = None
        last_sent = time.monotonic()
        yield "retry: 3000\n\n"
        while True:
            async with factory() as s:
                run = await run_service.get_run(s, run_id)
                rows = (
                    await s.scalars(
                        select(RunCaseResult)
                        .where(RunCaseResult.run_id == run_id, RunCaseResult.sequence > cursor)
                        .order_by(RunCaseResult.sequence)
                        .limit(MAX_CASE_EVENTS_PER_POLL)
                    )
                ).all()
                read = run_service.to_read(run)
            out: list[str] = []
            for row in rows:
                cursor = row.sequence
                name = "case.completed" if row.status == "succeeded" else "case.failed"
                out.append(_sse(name, _case_event(row), row.sequence))
            snapshot = read.model_dump_json()
            if snapshot != last_progress:
                last_progress = snapshot
                out.append(_sse("run.progress", read))
            if read.status != last_status:
                if last_status is not None and read.status in STATUS_EVENTS:
                    out.append(_sse(STATUS_EVENTS[read.status], {"status": read.status}))
                last_status = read.status
            caught_up = len(rows) < MAX_CASE_EVENTS_PER_POLL
            if read.status in TERMINAL_STATUSES and caught_up:
                out.append(_sse("metric.updated", {"run_id": run_id}))
                out.append(_sse("run.end", {"status": read.status}))
                yield "".join(out)
                return
            if out:
                last_sent = time.monotonic()
                yield "".join(out)
            elif time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
                last_sent = time.monotonic()
                yield ": keepalive\n\n"
            if await request.is_disconnected():
                return
            if caught_up:
                await asyncio.sleep(interval)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx: deliver events immediately instead of buffering the response.
            "X-Accel-Buffering": "no",
        },
    )
