from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ComponentState = Literal["ok", "error", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ComponentStatus(BaseModel):
    status: ComponentState
    latency_ms: float | None = None
    detail: str | None = None


class WorkerStatus(BaseModel):
    status: ComponentState
    active_workers: int
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: ComponentStatus
    redis: ComponentStatus
    worker: WorkerStatus


class VersionResponse(BaseModel):
    name: str = "openllm-eval"
    version: str
    api_version: str
    python_version: str
