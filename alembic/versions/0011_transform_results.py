"""Migration: 坐标转换结果独立表（v2.11）.

- 新表 transform_results: 保存坐标转换后的 GeoJSON 输出
  独立于原文件，提供下载/删除管理
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0011_transform_results"
down_revision = "0010_layer_stats_and_qc_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transform_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36),
                  sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("source_file_id", sa.String(36),
                  sa.ForeignKey("stored_files.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("source_file_name", sa.String(512), nullable=False),
        sa.Column("source_crs", sa.String(256), nullable=False),
        sa.Column("target_crs", sa.String(256), nullable=False),
        sa.Column("source_srid", sa.Integer, nullable=True),
        sa.Column("target_srid", sa.Integer, nullable=True),
        sa.Column("feature_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_size", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("output_data", sa.LargeBinary, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("transform_results")
