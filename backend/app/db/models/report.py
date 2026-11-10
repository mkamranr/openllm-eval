"""Generated reports and saved run comparisons (spec §8.1, §9.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class Report(UUIDPrimaryKeyMixin, Base):
    """An immutable report artifact generated from a finished run.

    The content is stored in the database (``storage_location = "database"``), so a report keeps
    showing what the run looked like when it was generated, even after a later retry.
    """

    __tablename__ = "reports"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # html | json | csv | metrics_csv | markdown
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_location: Mapped[str] = mapped_column(String(32), nullable=False, default="database")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    report_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class Comparison(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved selection of runs to compare. The comparison itself is computed on read."""

    __tablename__ = "comparisons"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
