"""create model_endpoints table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "model_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("adapter_type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("model_identifier", sa.String(length=512), nullable=False),
        sa.Column("secret_reference", sa.String(length=256), nullable=True),
        sa.Column("request_configuration", JSONType, nullable=False),
        sa.Column("model_metadata", JSONType, nullable=False),
        sa.Column("deployment_metadata", JSONType, nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_summary", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_model_endpoints_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_endpoints")),
        sa.UniqueConstraint("name", name=op.f("uq_model_endpoints_name")),
    )
    op.create_index(
        op.f("ix_model_endpoints_project_id"), "model_endpoints", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_model_endpoints_project_id"), table_name="model_endpoints")
    op.drop_table("model_endpoints")
