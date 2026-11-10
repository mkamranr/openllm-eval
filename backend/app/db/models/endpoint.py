from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin


class ModelEndpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A registered model endpoint (spec §5.2, §8.1).

    ``secret_reference`` holds a reference such as ``env:OPENLLM_SECRET_X``, never a key.
    ``model_metadata`` / ``deployment_metadata`` are *declared* by the user and unverified.
    """

    __tablename__ = "model_endpoints"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    model_identifier: Mapped[str] = mapped_column(String(512), nullable=False)
    secret_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    deployment_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    # Optional GPU telemetry source scraped while this endpoint is measured (Milestone 7).
    telemetry_configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_test_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
