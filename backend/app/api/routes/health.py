"""Liveness, readiness, and version endpoints."""

from __future__ import annotations

import asyncio
import platform
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import API_VERSION, __version__
from app.api.deps import get_app_settings, get_engine, get_redis
from app.core.config import Settings
from app.core.logging import get_logger
from app.execution.heartbeat import count_live_workers
from app.schemas.health import (
    ComponentStatus,
    HealthResponse,
    ReadinessResponse,
    VersionResponse,
    WorkerStatus,
)

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Returns 200 whenever the API process is serving requests. Does not touch dependencies."""
    return HealthResponse()


async def _check_database(engine: AsyncEngine, limit_s: float) -> ComponentStatus:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(limit_s):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness_database_failed", error_type=type(exc).__name__)
        return ComponentStatus(status="error", detail=type(exc).__name__)
    return ComponentStatus(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _check_redis(redis: Redis, limit_s: float) -> ComponentStatus:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(limit_s):
            await redis.ping()
    except Exception as exc:
        logger.warning("readiness_redis_failed", error_type=type(exc).__name__)
        return ComponentStatus(status="error", detail=type(exc).__name__)
    return ComponentStatus(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _check_worker(redis: Redis, redis_ok: bool, limit_s: float) -> WorkerStatus:
    if not redis_ok:
        return WorkerStatus(status="unavailable", active_workers=0, detail="redis unreachable")
    try:
        async with asyncio.timeout(limit_s):
            count = await count_live_workers(redis)
    except Exception as exc:
        return WorkerStatus(status="error", active_workers=0, detail=type(exc).__name__)
    if count == 0:
        return WorkerStatus(status="unavailable", active_workers=0, detail="no worker heartbeat")
    return WorkerStatus(status="ok", active_workers=count)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A required dependency is down"}},
)
async def ready(
    settings: Settings = Depends(get_app_settings),
    engine: AsyncEngine = Depends(get_engine),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """Checks the database and Redis. Worker presence is reported but does not gate readiness."""
    database, redis_status = await asyncio.gather(
        _check_database(engine, settings.db_connect_timeout_seconds),
        _check_redis(redis, settings.redis_connect_timeout_seconds),
    )
    worker = await _check_worker(
        redis, redis_status.status == "ok", settings.redis_connect_timeout_seconds
    )
    healthy = database.status == "ok" and redis_status.status == "ok"
    tool_runner = await _check_tool_runner(settings) if settings.tool_execution_enabled else None
    body = ReadinessResponse(
        status="ok" if healthy else "degraded",
        database=database,
        redis=redis_status,
        worker=worker,
        auth_enabled=settings.auth_enabled,
        tool_runner=tool_runner,
    )
    return JSONResponse(status_code=200 if healthy else 503, content=body.model_dump())


async def _check_tool_runner(settings: Settings) -> ComponentStatus:
    from app.services.tool_service import fetch_registry

    started = time.perf_counter()
    registry, error = await fetch_registry(settings)
    latency = round((time.perf_counter() - started) * 1000, 2)
    if registry is None:
        return ComponentStatus(status="unavailable", latency_ms=latency, detail=error)
    if registry.get("error"):
        return ComponentStatus(status="error", latency_ms=latency, detail=registry["error"][:300])
    return ComponentStatus(
        status="ok", latency_ms=latency, detail=f"{len(registry.get('tools') or [])} tool(s)"
    )


@router.get("/version", response_model=VersionResponse, summary="Framework version")
async def version() -> VersionResponse:
    return VersionResponse(
        version=__version__, api_version=API_VERSION, python_version=platform.python_version()
    )
