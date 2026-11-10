"""Benchmark run API schemas (spec §5.4, §5.5, §9.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evaluation import EvaluationConfig, EvaluationResultRead, EvaluationSummary

LoadProfile = Literal["smoke", "sequential", "fixed_concurrency", "ramp", "sustained"]
LOAD_PROFILES: tuple[str, ...] = LoadProfile.__args__  # type: ignore[attr-defined]

# Suites that can run in this release, and the milestone that brings the others (spec §17).
# Performance is measured on every run; quality suites add evaluators on top.
IMPLEMENTED_SUITES = (
    "performance",
    "answer_quality",
    "structured_output",
    "tool_calling",
    "rag",
    "hallucination",
)
PLANNED_SUITES = {
    "quantization": 7,
    "context_window": 7,
}
SMOKE_MAX_REQUESTS = 5
SMOKE_MAX_CONCURRENCY = 2
MAX_RAMP_STEPS = 20


class GenerationConfig(BaseModel):
    """Run-level generation settings. ``None`` falls back to each endpoint's default."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=0.0, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=256, ge=1, le=1_000_000)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)


class LoadConfig(BaseModel):
    """Load profile (spec §5.5). Limits that depend on server settings are checked on create."""

    model_config = ConfigDict(extra="forbid")

    profile: LoadProfile = "fixed_concurrency"
    # Measured requests per model. None = one per dataset case (smoke: at most 5).
    requests: int | None = Field(default=None, ge=1)
    concurrency: int = Field(default=1, ge=1)
    warmup_requests: int = Field(default=0, ge=0, le=1000)
    stream: bool = True
    # None = each endpoint's own setting.
    request_timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    # ramp: concurrency per step, increasing; requests_per_step measured requests in each step.
    ramp_concurrency: list[int] = Field(default_factory=list, max_length=MAX_RAMP_STEPS)
    requests_per_step: int | None = Field(default=None, ge=1)
    # sustained: keep `concurrency` requests in flight for this long (`requests` = optional cap).
    duration_seconds: float | None = Field(default=None, ge=1)

    @field_validator("ramp_concurrency")
    @classmethod
    def _ramp(cls, v: list[int]) -> list[int]:
        if any(c < 1 for c in v):
            raise ValueError("Ramp concurrency values must be at least 1.")
        if any(b <= a for a, b in pairwise(v)):
            raise ValueError("Ramp concurrency values must increase step by step.")
        return v

    @model_validator(mode="after")
    def _profile_rules(self) -> LoadConfig:
        if self.profile == "sequential":
            if self.concurrency != 1:
                raise ValueError(
                    "The sequential profile runs one request at a time (concurrency 1)."
                )
        elif self.profile == "smoke":
            if self.requests is not None and self.requests > SMOKE_MAX_REQUESTS:
                raise ValueError(f"The smoke profile sends at most {SMOKE_MAX_REQUESTS} requests.")
            if self.concurrency > SMOKE_MAX_CONCURRENCY:
                raise ValueError(
                    f"The smoke profile uses low concurrency (at most {SMOKE_MAX_CONCURRENCY})."
                )
        elif self.profile == "ramp":
            if len(self.ramp_concurrency) < 2:
                raise ValueError("The ramp profile needs at least two concurrency steps.")
        elif self.profile == "sustained" and self.duration_seconds is None:
            raise ValueError("The sustained profile needs duration_seconds.")
        if self.profile != "ramp" and (self.ramp_concurrency or self.requests_per_step):
            raise ValueError(
                "ramp_concurrency and requests_per_step apply to the ramp profile only."
            )
        if self.profile != "sustained" and self.duration_seconds is not None:
            raise ValueError("duration_seconds applies to the sustained profile only.")
        return self


def validate_suites(v: list[str]) -> list[str]:
    unique = list(dict.fromkeys(v))
    for suite in unique:
        if suite in PLANNED_SUITES:
            raise ValueError(
                f"The {suite!r} suite arrives in Milestone {PLANNED_SUITES[suite]}; "
                f"available now: {', '.join(IMPLEMENTED_SUITES)}."
            )
        if suite not in IMPLEMENTED_SUITES:
            raise ValueError(f"Unknown suite {suite!r}.")
    return unique


class RunDatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID
    # None = the dataset's current version at creation time (then frozen in the snapshot).
    version_id: uuid.UUID | None = None


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: uuid.UUID | None = None
    endpoint_ids: list[uuid.UUID] = Field(min_length=1)
    datasets: list[RunDatasetRef] = Field(min_length=1, max_length=10)
    suites: list[str] = Field(default_factory=lambda: ["performance"], min_length=1)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    load: LoadConfig = Field(default_factory=LoadConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    # Start from a saved profile: its suites, evaluation, generation, and load are used for any
    # of those fields this request leaves out.
    profile_id: uuid.UUID | None = None

    @field_validator("endpoint_ids")
    @classmethod
    def _unique_endpoints(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("Each endpoint may be selected once.")
        return v

    @field_validator("datasets")
    @classmethod
    def _unique_datasets(cls, v: list[RunDatasetRef]) -> list[RunDatasetRef]:
        if len({d.dataset_id for d in v}) != len(v):
            raise ValueError("Each dataset may be selected once.")
        return v

    @field_validator("suites")
    @classmethod
    def _suites(cls, v: list[str]) -> list[str]:
        return validate_suites(v)


class RunDatasetRead(BaseModel):
    dataset_id: uuid.UUID | None
    dataset_version_id: uuid.UUID | None
    name: str
    dataset_type: str
    version_number: int
    content_hash: str
    record_count: int
    # False once the dataset version has been deleted (the run keeps its results).
    available: bool


class RunModelRead(BaseModel):
    id: uuid.UUID
    position: int
    endpoint_id: uuid.UUID | None
    name: str
    model_identifier: str
    adapter_type: str
    base_url: str
    status: str
    progress: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    declared_model_metadata: dict[str, Any]
    hardware_metadata: dict[str, Any]


class RunRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    description: str | None
    status: str
    suites: list[str]
    framework_version: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancel_requested_at: datetime | None
    error_summary: dict[str, Any] | None
    progress: dict[str, Any]
    pass_count: int
    retry_failed_requested: bool
    load: dict[str, Any]
    models: list[RunModelRead]
    datasets: list[RunDatasetRead]


class RunDetail(RunRead):
    configuration_snapshot: dict[str, Any]


class RunSummary(BaseModel):
    """Counts by status, for the dashboard and queue depth (spec §13.1)."""

    total: int
    by_status: dict[str, int]
    queued: int
    active: int


class CaseResultRead(BaseModel):
    id: uuid.UUID
    run_model_id: uuid.UUID
    dataset_case_id: uuid.UUID | None
    case_identifier: str
    sequence: int
    phase: Literal["warmup", "measured"]
    request_index: int
    attempt_number: int
    pass_number: int
    load_step: int | None
    is_final: bool
    is_warmup: bool
    status: Literal["succeeded", "failed"]
    error_type: str | None
    error_message: str | None
    http_status: int | None
    latency_ms: float | None
    ttft_ms: float | None
    request_metadata: dict[str, Any]
    response_data: dict[str, Any]
    performance_metrics: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    # True when response text was shortened for this listing (fetch the result for all of it).
    truncated: bool = False
    evaluations: list[EvaluationSummary] = Field(default_factory=list)


class CaseResultDetail(CaseResultRead):
    evaluations: list[EvaluationResultRead] = Field(default_factory=list)  # type: ignore[assignment]


class MetricRead(BaseModel):
    run_model_id: uuid.UUID | None
    load_step: int | None
    metric_name: str
    aggregation: str
    metric_value: float | None
    metric_unit: str
    sample_count: int
    metadata: dict[str, Any]


class RunMetrics(BaseModel):
    run_id: uuid.UUID
    # True while the run is still executing: values are computed from results so far.
    live: bool
    metrics_version: str
    items: list[MetricRead]
