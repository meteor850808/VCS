"""基线迁移 — 创建全量表结构

Revision ID: 0001_baseline
Create Date: 2026-06-15
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── api_keys ─────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    # ── conversion_jobs ──────────────────────────────────────────────────────
    op.create_table(
        "conversion_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("filenames", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("sub_status", sa.String(80), nullable=True),
        sa.Column("sub_percent", sa.Integer(), server_default="0", nullable=True),
        sa.Column("src_format", sa.String(16), nullable=True),
        sa.Column("src_crs", sa.String(32), nullable=True),
        sa.Column("dst_crs", sa.String(32), nullable=True),
        sa.Column("output_format", sa.String(16), nullable=True),
        sa.Column("quality_check_enabled", sa.Boolean(), server_default="false", nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_path", sa.Text(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("script_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_status_created", "conversion_jobs", ["status", sa.text("created_at DESC")])
    op.create_index("ix_jobs_active", "conversion_jobs", ["created_at"], postgresql_where=sa.text("status IN ('pending', 'processing')"))
    op.create_index("ix_jobs_api_key_id", "conversion_jobs", ["api_key_id"])

    # ── batch_jobs ───────────────────────────────────────────────────────────
    op.create_table(
        "batch_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=True),
        sa.Column("completed", sa.Integer(), server_default="0", nullable=True),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=True),
        sa.Column("jobs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── quality_reports ──────────────────────────────────────────────────────
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["conversion_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_job_id", "quality_reports", ["job_id"])

    # ── stored_files ─────────────────────────────────────────────────────────
    op.create_table(
        "stored_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("format", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── spatial_features ─────────────────────────────────────────────────────
    op.create_table(
        "spatial_features",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.String(64), nullable=True),
        sa.Column("geometry", sa.Text(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column("bbox", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["stored_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── data_standards ───────────────────────────────────────────────────────
    op.create_table(
        "data_standards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=True),
        sa.Column("rule_sets", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("data_standards")
    op.drop_table("spatial_features")
    op.drop_table("stored_files")
    op.drop_table("quality_reports")
    op.drop_table("batch_jobs")
    op.drop_table("conversion_jobs")
    op.drop_table("api_keys")
