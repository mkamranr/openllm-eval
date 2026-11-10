"""context lengths, telemetry configuration, and resource samples

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("run_aggregate_metrics") as batch:
        batch.add_column(sa.Column("context_length", sa.Integer(), nullable=True))
    with op.batch_alter_table("model_endpoints") as batch:
        batch.add_column(sa.Column("telemetry_configuration", JSONType, nullable=True))
    op.create_table(
        "resource_samples",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_model_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_seconds", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("values", JSONType, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["benchmark_runs.id"],
            name=op.f("fk_resource_samples_run_id_benchmark_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_model_id"],
            ["run_models.id"],
            name=op.f("fk_resource_samples_run_model_id_run_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_samples")),
    )
    op.create_index(
        op.f("ix_resource_samples_run_id"), "resource_samples", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_resource_samples_run_model_id"),
        "resource_samples",
        ["run_model_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_resource_samples_run_model_id"), table_name="resource_samples")
    op.drop_index(op.f("ix_resource_samples_run_id"), table_name="resource_samples")
    op.drop_table("resource_samples")
    with op.batch_alter_table("model_endpoints") as batch:
        batch.drop_column("telemetry_configuration")
    with op.batch_alter_table("run_aggregate_metrics") as batch:
        batch.drop_column("context_length")
