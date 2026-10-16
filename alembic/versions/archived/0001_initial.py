"""initial tables

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id',           sa.String(36),  primary_key=True),
        sa.Column('key_hash',     sa.String(128), nullable=False, unique=True),
        sa.Column('name',         sa.String(128), nullable=False),
        sa.Column('description',  sa.Text,        nullable=True),
        sa.Column('is_active',    sa.Boolean,     nullable=False, server_default='true'),
        sa.Column('created_at',   sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_api_keys_key_hash', 'api_keys', ['key_hash'])

    op.create_table(
        'conversion_jobs',
        sa.Column('id',                   sa.String(36),   primary_key=True),
        sa.Column('celery_task_id',       sa.String(128),  nullable=True),
        sa.Column('api_key_id',           sa.String(36),   nullable=True, index=True),
        sa.Column('original_filename',    sa.String(512),  nullable=False),
        sa.Column('input_path',           sa.String(1024), nullable=False),
        sa.Column('output_path',          sa.String(1024), nullable=True),
        sa.Column('src_crs',              sa.String(256),  nullable=True),
        sa.Column('dst_crs',              sa.String(256),  nullable=False),
        sa.Column('output_format',        sa.String(64),   nullable=False, server_default='GeoJSON'),
        sa.Column('select_fields',        sa.JSON,         nullable=True),
        sa.Column('where_clause',         sa.Text,         nullable=True),
        sa.Column('repair_strategy',      sa.String(32),   nullable=False, server_default='auto_fix'),
        sa.Column('script_type',          sa.String(32),   nullable=False, server_default='ogr2ogr'),
        sa.Column('target_encoding',      sa.String(32),   nullable=False, server_default='UTF-8'),
        sa.Column('detected_format',      sa.String(64),   nullable=True),
        sa.Column('detected_encoding',    sa.String(32),   nullable=True),
        sa.Column('detected_crs',         sa.String(256),  nullable=True),
        sa.Column('detection_confidence', sa.Float,        nullable=True),
        sa.Column('generated_script',     sa.Text,         nullable=True),
        sa.Column('generated_script_type',sa.String(32),   nullable=True),
        sa.Column('feature_count_input',  sa.Integer,      nullable=True),
        sa.Column('feature_count_output', sa.Integer,      nullable=True),
        sa.Column('feature_fixed',        sa.Integer,      nullable=True),
        sa.Column('feature_dropped',      sa.Integer,      nullable=True),
        sa.Column('status',               sa.String(32),   nullable=False, server_default='pending', index=True),
        sa.Column('error_message',        sa.Text,         nullable=True),
        sa.Column('warnings',             sa.JSON,         nullable=True),
        sa.Column('sandbox_stdout',       sa.Text,         nullable=True),
        sa.Column('sandbox_stderr',       sa.Text,         nullable=True),
        sa.Column('created_at',           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('started_at',           sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at',          sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_conversion_jobs_status', 'conversion_jobs', ['status'])
    op.create_index('ix_conversion_jobs_celery_task_id', 'conversion_jobs', ['celery_task_id'])


def downgrade() -> None:
    op.drop_table('conversion_jobs')
    op.drop_table('api_keys')
