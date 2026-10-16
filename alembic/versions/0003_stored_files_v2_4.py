"""stored_files v2.4 — 新增数据分类、结构信息、切片上传支持。

Revision ID: 0003_stored_files_v2_4
Create Date: 2026-07-21

变更内容：
- 新增 data_type (数据类型分类: geometry/table/archive/unknown)
- 新增 file_format (文件格式标识)
- 新增 detected_crs (检测到的坐标系)
- 新增 layer_count (图层数量)
- 新增 feature_count (要素总数，物化字段)
- 新增 field_schema (JSONB，字段结构)
- 新增 data_standard_name (冗余字段，便于列表展示)
- 新增 upload_id (上传会话 ID，用于切片合并追踪)
- 新增 chunk_dir (临时切片目录路径)
- 添加相关索引
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "0003_stored_files_v2_4"
down_revision = "0002_data_standard_v2_2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 数据分类字段 ──
    op.add_column("stored_files", sa.Column(
        "data_type", sa.String(20), nullable=False,
        server_default="unknown",
        comment="数据类型: geometry/table/archive/unknown"
    ))
    op.add_column("stored_files", sa.Column(
        "file_format", sa.String(32), nullable=False,
        server_default="unknown",
        comment="文件格式: geojson/shp/gpkg/kml/dxf/gml/gdb/mdb/obj/csv/xlsx/tab/mif/zip/tar"
    ))
    op.add_column("stored_files", sa.Column(
        "detected_crs", sa.String(64), nullable=True,
        comment="检测到的坐标系: EPSG:4490 / EPSG:4326"
    ))
    op.add_column("stored_files", sa.Column(
        "layer_count", sa.Integer, nullable=False,
        server_default=sa.text("0"),
        comment="图层数量"
    ))
    op.add_column("stored_files", sa.Column(
        "feature_count", sa.BigInteger, nullable=False,
        server_default=sa.text("0"),
        comment="要素总数（物化字段）"
    ))
    op.add_column("stored_files", sa.Column(
        "field_schema", JSONB, nullable=True,
        comment="字段结构 [{name,type,nullable,sample}, ...]"
    ))

    # ── 标准关联冗余字段 ──
    op.add_column("stored_files", sa.Column(
        "data_standard_name", sa.String(256), nullable=True,
        comment="关联标准名称（冗余，便于列表展示）"
    ))

    # ── 切片上传支持 ──
    op.add_column("stored_files", sa.Column(
        "upload_id", sa.String(64), nullable=True,
        comment="上传会话 ID（文件 SHA-256），合并前追踪"
    ))
    op.add_column("stored_files", sa.Column(
        "chunk_dir", sa.String(1024), nullable=True,
        comment="临时切片目录路径"
    ))

    # ── 索引 ──
    op.create_index("ix_stored_files_data_type", "stored_files", ["data_type"])
    op.create_index("ix_stored_files_file_format", "stored_files", ["file_format"])
    op.create_index("ix_stored_files_upload_id", "stored_files", ["upload_id"])


def downgrade() -> None:
    # ── 删除索引 ──
    op.drop_index("ix_stored_files_upload_id", "stored_files")
    op.drop_index("ix_stored_files_file_format", "stored_files")
    op.drop_index("ix_stored_files_data_type", "stored_files")

    # ── 删除列 (按外键依赖逆序) ──
    op.drop_column("stored_files", "chunk_dir")
    op.drop_column("stored_files", "upload_id")
    op.drop_column("stored_files", "data_standard_name")
    op.drop_column("stored_files", "field_schema")
    op.drop_column("stored_files", "feature_count")
    op.drop_column("stored_files", "layer_count")
    op.drop_column("stored_files", "detected_crs")
    op.drop_column("stored_files", "file_format")
    op.drop_column("stored_files", "data_type")
