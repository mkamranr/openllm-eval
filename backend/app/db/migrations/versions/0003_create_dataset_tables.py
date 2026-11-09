"""create dataset tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
CURRENT_VERSION_FK = "fk_datasets_current_version_id_dataset_versions"


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("sample_id", sa.String(length=64), nullable=True),
        sa.Column("license", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_datasets_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("name", name=op.f("uq_datasets_name")),
    )
    op.create_index(op.f("ix_datasets_project_id"), "datasets", ["project_id"], unique=False)

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=100), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("storage_location", sa.String(length=1024), nullable=True),
        sa.Column("source_format", sa.String(length=16), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("type_counts", JSONType, nullable=False),
        sa.Column("tag_counts", JSONType, nullable=False),
        sa.Column("split_counts", JSONType, nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.UniqueConstraint(
            "dataset_id", "content_hash", name="uq_dataset_versions_dataset_id_content_hash"
        ),
        sa.UniqueConstraint(
            "dataset_id", "version_number", name="uq_dataset_versions_dataset_id_version_number"
        ),
    )
    op.create_index(
        op.f("ix_dataset_versions_dataset_id"), "dataset_versions", ["dataset_id"], unique=False
    )

    # datasets.current_version_id -> dataset_versions.id is circular, so it is added once both
    # tables exist. SQLite cannot ALTER constraints and needs a batch (table copy) operation.
    with op.batch_alter_table("datasets") as batch:
        batch.create_foreign_key(
            CURRENT_VERSION_FK,
            "dataset_versions",
            ["current_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "dataset_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("case_identifier", sa.String(length=200), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column("tag_index", sa.Text(), nullable=False),
        sa.Column("content", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_dataset_cases_dataset_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_cases")),
        sa.UniqueConstraint(
            "dataset_version_id",
            "case_identifier",
            name="uq_dataset_cases_dataset_version_id_case_identifier",
        ),
    )
    op.create_index(
        "ix_dataset_cases_version_position",
        "dataset_cases",
        ["dataset_version_id", "position"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_cases_version_position", table_name="dataset_cases")
    op.drop_table("dataset_cases")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint(CURRENT_VERSION_FK, type_="foreignkey")
    op.drop_index(op.f("ix_dataset_versions_dataset_id"), table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_index(op.f("ix_datasets_project_id"), table_name="datasets")
    op.drop_table("datasets")
