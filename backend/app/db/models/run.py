"""Benchmark runs and their per-request results (spec §8.1, §13.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

# --- Run state machine (spec §13.3) --------------------------------------------------------------
# created -> queued -> initializing -> running -> finalizing -> completed | completed_with_errors
# created / queued / initializing / running -> cancel_requested -> cancelled
# initializing / running / finalizing -> failed
#
# completed_with_errors: the run finished, but at least one measured request failed (after its
# retries) or was never completed. Failed requests stay in every denominator.
CREATED = "created"
QUEUED = "queued"
INITIALIZING = "initializing"
RUNNING = "running"
FINALIZING = "finalizing"
CANCEL_REQUESTED = "cancel_requested"
COMPLETED = "completed"
COMPLETED_WITH_ERRORS = "completed_with_errors"
CANCELLED = "cancelled"
FAILED = "failed"

RUN_STATUSES = (
    CREATED,
    QUEUED,
    INITIALIZING,
    RUNNING,
    FINALIZING,
    CANCEL_REQUESTED,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    CANCELLED,
    FAILED,
)
# Owned by a worker (lease) while in one of these.
ACTIVE_STATUSES = (INITIALIZING, RUNNING, FINALIZING, CANCEL_REQUESTED)
PENDING_STATUSES = (CREATED, QUEUED)
TERMINAL_STATUSES = (COMPLETED, COMPLETED_WITH_ERRORS, CANCELLED, FAILED)

# Result rows.
PHASE_WARMUP = "warmup"
PHASE_MEASURED = "measured"
RESULT_SUCCEEDED = "succeeded"
RESULT_FAILED = "failed"


class BenchmarkRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "benchmark_runs"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Evaluation profiles arrive with evaluators (M4); the column is reserved for them.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CREATED, index=True)
    # Immutable, secret-free snapshot of everything the run was created with.
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    framework_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # Live progress written by the worker (counts, current endpoint, phase).
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    # Execution passes: 1 = the original execution; resumes and retry-failed add passes.
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_failed_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    models: Mapped[list[RunModel]] = relationship(
        back_populates="run",
        order_by="RunModel.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    datasets: Mapped[list[RunDataset]] = relationship(
        order_by="RunDataset.position", cascade="all, delete-orphan", passive_deletes=True
    )


class RunDataset(UUIDPrimaryKeyMixin, Base):
    """Dataset versions a run uses, in order. Cases are concatenated in this order."""

    __tablename__ = "run_datasets"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # SET NULL keeps finished runs inspectable after a dataset is deleted (the snapshot keeps
    # name, version, and hash). Deleting a dataset used by an active run is refused.
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class RunModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "run_models"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_endpoints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Secret-free: secret *references* only, never resolved values.
    endpoint_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    declared_model_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    hardware_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    run: Mapped[BenchmarkRun] = relationship(back_populates="models")


class RunCaseResult(UUIDPrimaryKeyMixin, Base):
    """One attempt of one request. Retries are separate rows (spec §5.4)."""

    __tablename__ = "run_case_results"
    __table_args__ = (
        # Prevents duplicate results when work is retried or resumed (spec §13.2).
        UniqueConstraint("run_model_id", "phase", "request_index", "attempt_number"),
        Index("ix_run_case_results_run_status", "run_id", "status"),
        # Cursor for live events (SSE Last-Event-ID).
        Index("ix_run_case_results_run_sequence", "run_id", "sequence", unique=True),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    case_identifier: Mapped[str] = mapped_column(String(200), nullable=False)
    # Order in which results were recorded within the run (1, 2, ...).
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    request_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Ramp profile step (0-based); NULL for single-step profiles.
    load_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True for the attempt that decided the request's outcome (success, or the last failure).
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_warmup: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    response_data: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    performance_metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class ResourceSample(UUIDPrimaryKeyMixin, Base):
    """One telemetry scrape while a model was measured (GPU memory, utilization, power, ...).

    ``values`` holds canonical keys (``gpu_memory_used_mb``, ``gpu_utilization_pct``, ...) summed
    or averaged over the selected GPUs, plus a ``gpus`` list with per-device values.
    """

    __tablename__ = "resource_samples"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(nullable=False)
    # Seconds since the model's measured phase started.
    offset_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)


class RunAggregateMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "run_aggregate_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_models.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # NULL = all measured requests; otherwise the ramp step this row describes.
    load_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL = all context lengths; otherwise the requested context length (tokens) of the
    # context-window group this row describes (``context.*`` metrics only).
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL = not measurable (the reason is in metadata), never 0 (spec §2.3).
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
