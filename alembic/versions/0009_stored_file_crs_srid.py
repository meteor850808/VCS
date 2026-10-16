"""Migration: stored_files 增加 CRS 解析标记列（v2.10.5）.

- detected_crs_srid: 入库要素实际使用的 SRID（可追溯；NULL=历史数据未解析）
- crs_is_custom: 坐标系是否为注册到 spatial_ref_sys 的自定义 SRID（非 EPSG）

背景：v2.10.5 前，自定义投影坐标系（如上海地方坐标系）入库时被回退标记
SRID=4326 但坐标仍为投影米值，导致质检 ST_Transform 失败、地图预览错位。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0009_stored_file_crs_srid"
down_revision = "0008_quality_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stored_files",
        sa.Column("detected_crs_srid", sa.Integer, nullable=True,
                   comment="入库要素实际使用的 SRID（EPSG 编码或 VCS-CUSTOM 自定义注册号）"),
    )
    op.add_column(
        "stored_files",
        sa.Column("crs_is_custom", sa.Boolean, nullable=False, server_default=sa.text("false"),
                   comment="坐标系是否为自定义注册 SRID（非 EPSG 标准编码）"),
    )


def downgrade() -> None:
    op.drop_column("stored_files", "crs_is_custom")
    op.drop_column("stored_files", "detected_crs_srid")
