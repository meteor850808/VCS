"""add batch_jobs, quality_reports, progress tracking

Revision ID: 0002
Revises: 0001_initial
Create Date: 2026-05-08 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_add_batch_quality'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 新增列: conversion_jobs ────────────────────────────────────
    op.add_column('conversion_jobs', sa.Column('batch_job_id', sa.String(36), nullable=True))
    op.add_column('conversion_jobs', sa.Column('quality_report_id', sa.String(36), nullable=True))
    op.add_column('conversion_jobs', sa.Column('progress', sa.Float, nullable=True, server_default='0'))
    op.add_column('conversion_jobs', sa.Column('current_step', sa.String(64), nullable=True))
    op.create_index('ix_conversion_jobs_batch', 'conversion_jobs', ['batch_job_id'])

    # ── 新建表: batch_jobs ─────────────────────────────────────────
    op.create_table(
        'batch_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('api_key_id', sa.String(36), nullable=True),
        sa.Column('name', sa.String(256), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('total_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('success_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('failed_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cancelled_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('output_format', sa.String(64), nullable=False, server_default='GeoJSON'),
        sa.Column('dst_crs', sa.String(256), nullable=False, server_default='EPSG:4490'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_batch_jobs_status', 'batch_jobs', ['status'])
    op.create_index('ix_batch_jobs_api_key', 'batch_jobs', ['api_key_id'])

    # ── 新建表: quality_reports ────────────────────────────────────
    op.create_table(
        'quality_reports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_id', sa.String(36), nullable=True),
        sa.Column('batch_job_id', sa.String(36), nullable=True),
        sa.Column('api_key_id', sa.String(36), nullable=True),
        sa.Column('file_name', sa.String(512), nullable=False),
        sa.Column('detected_format', sa.String(64), nullable=False),
        sa.Column('total_features', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_fields', sa.Integer, nullable=False, server_default='0'),
        sa.Column('completeness_score', sa.Float, nullable=False, server_default='1.0'),
        sa.Column('missing_fields', sa.JSON, nullable=True),
        sa.Column('null_value_counts', sa.JSON, nullable=True),
        sa.Column('critical_fields_status', sa.JSON, nullable=True),
        sa.Column('issues', sa.JSON, nullable=True),
        sa.Column('html_report_path', sa.String(1024), nullable=True),
        sa.Column('json_report_path', sa.String(1024), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_quality_reports_job', 'quality_reports', ['job_id'])
    op.create_index('ix_quality_reports_api_key', 'quality_reports', ['api_key_id'])


def downgrade() -> None:
    op.drop_table('quality_reports')
    op.drop_table('batch_jobs')
    op.drop_index('ix_conversion_jobs_batch', 'conversion_jobs')
    op.drop_column('conversion_jobs', 'current_step')
    op.drop_column('conversion_jobs', 'progress')
    op.drop_column('conversion_jobs', 'quality_report_id')
    op.drop_column('conversion_jobs', 'batch_job_id')
