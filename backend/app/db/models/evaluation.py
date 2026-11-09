"""Evaluation profiles and per-case evaluator results (spec §8.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class EvaluationProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable run configuration: suites, evaluator settings, generation, and load."""

    __tablename__ = "evaluation_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_evaluation_profiles_name"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suites: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    evaluator_configuration: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    generation_configuration: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    load_configuration: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)


class EvaluationResult(UUIDPrimaryKeyMixin, Base):
    """One evaluator's verdict on one request (the final, successful attempt)."""

    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_case_result_id", "evaluator_name"),
        Index("ix_evaluation_results_run_evaluator", "run_id", "evaluator_name"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_case_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_case_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    suite: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # completed | failed | skipped | not_applicable | requires_review
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL when there is no score (spec §7.7), never 0.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
