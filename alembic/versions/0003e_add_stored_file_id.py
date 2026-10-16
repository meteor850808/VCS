"""Add stored_file_id to quality_reports for GeoJSON export geometry.

Revision ID: 0003e
Revises: 0003d
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0003e_add_stored_file_id"
down_revision = "0003d_add_geometry_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quality_reports",
        sa.Column(
            "stored_file_id",
            sa.String(36),
            sa.ForeignKey("stored_files.id", ondelete="SET NULL"),
            nullable=True,
            comment="关联入库文件，用于导出违规要素几何",
        ),
    )
    op.create_index(
        "ix_quality_reports_stored_file_id",
        "quality_reports",
        ["stored_file_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_quality_reports_stored_file_id", "quality_reports")
    op.drop_column("quality_reports", "stored_file_id")
