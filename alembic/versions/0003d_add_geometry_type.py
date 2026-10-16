"""fix: 为 stored_files 添加 geometry_type 列（几何细分类型）。

Revision ID: 0003d_add_geometry_type
Create Date: 2026-07-22

data_type 仅存 geometry/table/archive/unknown 粗分类，
新增 geometry_type 存储 Point/LineString/Polygon/MultiPolygon/3d/table 细分类，
使数据管理页可按几何子类型筛选和统计。
"""

from alembic import op
import sqlalchemy as sa

revision = "0003d_add_geometry_type"
down_revision = "0003c_fix_quality_reports_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stored_files", sa.Column("geometry_type", sa.String(32), nullable=True))
    op.create_index("ix_stored_files_geometry_type", "stored_files", ["geometry_type"])


def downgrade() -> None:
    op.drop_index("ix_stored_files_geometry_type", table_name="stored_files")
    op.drop_column("stored_files", "geometry_type")
