from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReportFormat = Literal["html", "json", "csv", "metrics_csv", "markdown"]


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ReportFormat = "html"
    # Leave out generated text, error messages, evaluator rationales, and claim evidence.
    redact_content: bool = False
    # Replace endpoint base URLs (judge and embedding endpoints too) with "[redacted]".
    redact_endpoints: bool = False


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    run_name: str | None = None
    report_type: str
    storage_location: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    options: dict[str, Any]
    report_metadata: dict[str, Any]
    generated_at: datetime


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: uuid.UUID | None = None
    run_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    # A run id (its first model) or a run-model id. None = the first run's first model.
    baseline: uuid.UUID | None = None

    @field_validator("run_ids")
    @classmethod
    def _unique(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("Each run may be selected once.")
        return v


class ComparisonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    description: str | None
    run_ids: list[str]
    baseline_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ComparisonDetail(ComparisonRead):
    result: dict[str, Any] | None = None
    # Set when a compared run has since been deleted.
    error: str | None = None
