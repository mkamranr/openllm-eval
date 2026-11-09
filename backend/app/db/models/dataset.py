from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

# Tags are also stored as "\n<tag>\n<tag>\n" so tag filters are a portable LIKE query
# (tags cannot contain control characters, see app.datasets.schema).
TAG_SEPARATOR = "\n"


def tag_index(tags: list[str]) -> str:
    return TAG_SEPARATOR + TAG_SEPARATOR.join(tags) + TAG_SEPARATOR if tags else ""


class Dataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named, versioned collection of evaluation cases (spec §5.3, §8.1)."""

    __tablename__ = "datasets"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # One of the case types, or "mixed".
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "dataset_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_current_version_id_dataset_versions",
        ),
        nullable=True,
    )
    # "user" or "sample"; samples record which bundled file they came from and its license.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    sample_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DatasetVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable snapshot of a dataset's records.

    ``content_hash`` is the SHA-256 of the normalized JSONL export (see
    ``app.datasets.validation``). Rows are never updated after insert.
    """

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "version_number", name="uq_dataset_versions_dataset_id_version_number"
        ),
        UniqueConstraint(
            "dataset_id", "content_hash", name="uq_dataset_versions_dataset_id_content_hash"
        ),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL = cases stored in dataset_cases (the only mode so far; files come later, spec §8.1).
    storage_location: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type_counts: Mapped[dict[str, int]] = mapped_column(JSONType, nullable=False, default=dict)
    tag_counts: Mapped[dict[str, int]] = mapped_column(JSONType, nullable=False, default=dict)
    split_counts: Mapped[dict[str, int]] = mapped_column(JSONType, nullable=False, default=dict)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class DatasetCase(UUIDPrimaryKeyMixin, Base):
    """One normalized record of a dataset version. Immutable."""

    __tablename__ = "dataset_cases"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "case_identifier",
            name="uq_dataset_cases_dataset_version_id_case_identifier",
        ),
        Index("ix_dataset_cases_version_position", "dataset_version_id", "position", unique=True),
    )

    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    case_identifier: Mapped[str] = mapped_column(String(200), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    tag_index: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class ImmutableRecordError(RuntimeError):
    pass


def _forbid_update(_mapper: Any, _connection: Any, target: Any) -> None:
    raise ImmutableRecordError(
        f"{type(target).__name__} rows are immutable; create a new dataset version instead."
    )


event.listen(DatasetVersion, "before_update", _forbid_update)
event.listen(DatasetCase, "before_update", _forbid_update)
