"""Migration: 图层 × 几何类型归类统计 + 质检图层圈定（v2.10.5）.

- 新表 stored_file_layers: 文件-图层汇总（每图层一行，点/线/面三维计数）
  数据来源：三分区特征表实时聚合（源即真相），入库后由 aggregate_layer_stats 维护
- stored_files 增加 point_count / line_count / polygon_count 冗余列
  （列表页零 JOIN 展示类型构成）
- quality_reports 增加 checked_layers (JSON) 列
  （质检图层圈定范围快照；NULL=全部图层）

背景：此前 layer_name 仅作为要素行属性存在，没有任何聚合视图。
CAD（DXF/DWG）真实图层藏在 properties.Layer 中、fiona layer_name 恒为
'entities'；GDB 多图层虽正确打标但无统计出口；质检拓扑规则无法按图层圈定范围。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "0010_layer_stats_and_qc_scope"
down_revision = "0009_stored_file_crs_srid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_file_layers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stored_file_id", sa.String(36),
                  sa.ForeignKey("stored_files.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("api_key_id", sa.String(36), nullable=True),
        sa.Column("layer_name", sa.String(256), nullable=False),
        sa.Column("geometry_type", sa.String(32), nullable=True,
                  comment="主导几何类型 Point/LineString/Polygon/mixed"),
        sa.Column("point_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("line_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("polygon_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("feature_count", sa.Integer, nullable=False, server_default="0",
                  comment="要素总数（冗余，便于排序）"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("stored_file_id", "layer_name", name="uq_sfl_file_layer"),
    )
    op.create_index("ix_sfl_api_key", "stored_file_layers", ["api_key_id"])

    _cn = {"point_count": "点", "line_count": "线", "polygon_count": "面"}
    for col in ("point_count", "line_count", "polygon_count"):
        op.add_column(
            "stored_files",
            sa.Column(col, sa.Integer, nullable=False, server_default="0",
                      comment=f"该文件{_cn[col]}要素数（聚合冗余列）"),
        )

    op.add_column(
        "quality_reports",
        sa.Column("checked_layers", JSONB, nullable=True,
                  comment="质检圈定的图层名列表；NULL=全部图层"),
    )


def downgrade() -> None:
    op.drop_column("quality_reports", "checked_layers")
    for col in ("point_count", "line_count", "polygon_count"):
        op.drop_column("stored_files", col)
    op.drop_index("ix_sfl_api_key", table_name="stored_file_layers")
    op.drop_table("stored_file_layers")
