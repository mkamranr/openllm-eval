"""Dataset API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.datasets.schema import DATASET_TYPES
from app.schemas.endpoint import _clean_tags

DatasetType = Literal[
    "chat",
    "instruction",
    "qa",
    "structured_output",
    "tool_call",
    "rag",
    "long_context",
    "safety",
    "mixed",
]
assert set(DatasetType.__args__) == set(DATASET_TYPES)  # type: ignore[attr-defined]  # noqa: S101

UploadFormat = Literal["jsonl", "csv"]


class ContentInput(BaseModel):
    """Dataset content sent inline (JSON body). Multipart uploads use a ``file`` part instead."""

    content: str | None = Field(default=None, description="JSONL or CSV text")
    format: UploadFormat | None = Field(default=None, description="Detected when omitted")
    filename: str | None = Field(default=None, max_length=255)


class ValidateRequest(ContentInput):
    model_config = ConfigDict(extra="forbid")

    dataset_type: DatasetType = "mixed"


class DatasetCreate(ContentInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    project_id: uuid.UUID | None = None
    dataset_type: DatasetType
    tags: list[str] = Field(default_factory=list)
    version_label: str | None = Field(default=None, max_length=100)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_tags(v)


class VersionCreate(ContentInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version_label: str | None = Field(default=None, max_length=100)


class DatasetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    project_id: uuid.UUID | None = None
    tags: list[str] | None = None
    # Point the dataset at an existing (older or newer) version.
    current_version_id: uuid.UUID | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_tags(v)


class DatasetDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    version_id: uuid.UUID | None = Field(
        default=None, description="Defaults to the current version"
    )


class IssueRead(BaseModel):
    line: int | None
    case_id: str | None
    field: str | None
    message: str


class ValidationLimits(BaseModel):
    max_upload_bytes: int
    max_records: int
    max_record_bytes: int


class ValidationResult(BaseModel):
    valid: bool
    format: UploadFormat
    dataset_type: DatasetType
    schema_version: str
    rows: int = Field(description="Non-blank rows read")
    record_count: int = Field(description="Rows that passed validation")
    error_count: int
    warning_count: int
    errors: list[IssueRead] = Field(description="First 200 errors")
    warnings: list[IssueRead] = Field(description="First 200 warnings")
    type_counts: dict[str, int]
    tag_counts: dict[str, int]
    split_counts: dict[str, int]
    content_hash: str | None = Field(description="SHA-256 of the normalized JSONL; only if valid")
    preview: list[dict[str, Any]] = Field(description="First normalized records")
    limits: ValidationLimits


class DatasetVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    version_number: int
    version_label: str | None
    content_hash: str
    record_count: int
    schema_version: str
    source_format: str
    source_filename: str | None
    type_counts: dict[str, int]
    tag_counts: dict[str, int]
    split_counts: dict[str, int]
    warning_count: int
    created_at: datetime
    is_current: bool = False


class DatasetRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    description: str | None
    dataset_type: DatasetType
    tags: list[str]
    source: str
    sample_id: str | None
    license: str | None
    current_version: DatasetVersionRead | None
    version_count: int
    created_at: datetime
    updated_at: datetime


class CaseRead(BaseModel):
    position: int
    case_identifier: str
    case_type: str
    tags: list[str]
    content: dict[str, Any]
    truncated: bool = Field(
        default=False, description="Long text fields were shortened for the preview"
    )


class SampleRead(BaseModel):
    sample_id: str
    name: str
    description: str
    dataset_type: DatasetType
    record_count: int
    license: str
    source: str
    installed_dataset_id: uuid.UUID | None
