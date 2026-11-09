"""create run tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table('benchmark_runs',
    sa.Column('project_id', sa.Uuid(), nullable=True),
    sa.Column('profile_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('configuration_snapshot', JSONType, nullable=False),
    sa.Column('framework_version', sa.String(length=32), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_summary', JSONType, nullable=True),
    sa.Column('progress', JSONType, nullable=False),
    sa.Column('pass_count', sa.Integer(), nullable=False),
    sa.Column('retry_failed_requested', sa.Boolean(), nullable=False),
    sa.Column('worker_id', sa.String(length=200), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_benchmark_runs_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_benchmark_runs'))
    )
    with op.batch_alter_table('benchmark_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_benchmark_runs_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_benchmark_runs_status'), ['status'], unique=False)

    op.create_table('run_models',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('endpoint_id', sa.Uuid(), nullable=True),
    sa.Column('endpoint_snapshot', JSONType, nullable=False),
    sa.Column('declared_model_metadata', JSONType, nullable=False),
    sa.Column('hardware_metadata', JSONType, nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('progress', JSONType, nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['endpoint_id'], ['model_endpoints.id'], name=op.f('fk_run_models_endpoint_id_model_endpoints'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], name=op.f('fk_run_models_run_id_benchmark_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_models')),
    sa.UniqueConstraint('run_id', 'position', name=op.f('uq_run_models_run_id'))
    )
    with op.batch_alter_table('run_models', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_models_endpoint_id'), ['endpoint_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_models_run_id'), ['run_id'], unique=False)

    op.create_table('run_aggregate_metrics',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('run_model_id', sa.Uuid(), nullable=True),
    sa.Column('load_step', sa.Integer(), nullable=True),
    sa.Column('metric_name', sa.String(length=64), nullable=False),
    sa.Column('aggregation', sa.String(length=32), nullable=False),
    sa.Column('metric_value', sa.Float(), nullable=True),
    sa.Column('metric_unit', sa.String(length=32), nullable=False),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('metadata', JSONType, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], name=op.f('fk_run_aggregate_metrics_run_id_benchmark_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_model_id'], ['run_models.id'], name=op.f('fk_run_aggregate_metrics_run_model_id_run_models'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_aggregate_metrics'))
    )
    with op.batch_alter_table('run_aggregate_metrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_aggregate_metrics_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_aggregate_metrics_run_model_id'), ['run_model_id'], unique=False)

    op.create_table('run_datasets',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('dataset_version_id', sa.Uuid(), nullable=True),
    sa.Column('dataset_id', sa.Uuid(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('record_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_version_id'], ['dataset_versions.id'], name=op.f('fk_run_datasets_dataset_version_id_dataset_versions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], name=op.f('fk_run_datasets_run_id_benchmark_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_datasets')),
    sa.UniqueConstraint('run_id', 'position', name=op.f('uq_run_datasets_run_id'))
    )
    with op.batch_alter_table('run_datasets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_datasets_dataset_version_id'), ['dataset_version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_datasets_run_id'), ['run_id'], unique=False)

    op.create_table('run_case_results',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('run_model_id', sa.Uuid(), nullable=False),
    sa.Column('dataset_case_id', sa.Uuid(), nullable=True),
    sa.Column('case_identifier', sa.String(length=200), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('phase', sa.String(length=16), nullable=False),
    sa.Column('request_index', sa.Integer(), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('pass_number', sa.Integer(), nullable=False),
    sa.Column('load_step', sa.Integer(), nullable=True),
    sa.Column('is_final', sa.Boolean(), nullable=False),
    sa.Column('is_warmup', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_type', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('ttft_ms', sa.Float(), nullable=True),
    sa.Column('request_metadata', JSONType, nullable=False),
    sa.Column('response_data', JSONType, nullable=False),
    sa.Column('performance_metrics', JSONType, nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_case_id'], ['dataset_cases.id'], name=op.f('fk_run_case_results_dataset_case_id_dataset_cases'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], name=op.f('fk_run_case_results_run_id_benchmark_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_model_id'], ['run_models.id'], name=op.f('fk_run_case_results_run_model_id_run_models'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_case_results')),
    sa.UniqueConstraint('run_model_id', 'phase', 'request_index', 'attempt_number', name=op.f('uq_run_case_results_run_model_id'))
    )
    with op.batch_alter_table('run_case_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_case_results_dataset_case_id'), ['dataset_case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_case_results_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_case_results_run_model_id'), ['run_model_id'], unique=False)
        batch_op.create_index('ix_run_case_results_run_status', ['run_id', 'status'], unique=False)
        batch_op.create_index('ix_run_case_results_run_sequence', ['run_id', 'sequence'], unique=True)



def downgrade() -> None:
    with op.batch_alter_table('run_case_results', schema=None) as batch_op:
        batch_op.drop_index('ix_run_case_results_run_sequence')
        batch_op.drop_index('ix_run_case_results_run_status')
        batch_op.drop_index(batch_op.f('ix_run_case_results_run_model_id'))
        batch_op.drop_index(batch_op.f('ix_run_case_results_run_id'))
        batch_op.drop_index(batch_op.f('ix_run_case_results_dataset_case_id'))

    op.drop_table('run_case_results')
    with op.batch_alter_table('run_datasets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_datasets_run_id'))
        batch_op.drop_index(batch_op.f('ix_run_datasets_dataset_version_id'))

    op.drop_table('run_datasets')
    with op.batch_alter_table('run_aggregate_metrics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_aggregate_metrics_run_model_id'))
        batch_op.drop_index(batch_op.f('ix_run_aggregate_metrics_run_id'))

    op.drop_table('run_aggregate_metrics')
    with op.batch_alter_table('run_models', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_models_run_id'))
        batch_op.drop_index(batch_op.f('ix_run_models_endpoint_id'))

    op.drop_table('run_models')
    with op.batch_alter_table('benchmark_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_benchmark_runs_status'))
        batch_op.drop_index(batch_op.f('ix_benchmark_runs_project_id'))

    op.drop_table('benchmark_runs')
