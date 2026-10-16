"""数据存储 ORM 模型 — 上传文件二进制内容存入 PostgreSQL BYTEA。

v2.4 变更：
- 添加 data_type / file_format / detected_crs / layer_count / feature_count
- 添加 field_schema (JSONB) 存储每层字段结构
- 添加 data_standard_name 冗余字段便于列表展示
- 添加 upload_id / chunk_dir 支持大文件切片上传
- 添加 standard_id / standard_table_id 支持数据标准关联
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class StoredFile(Base):
    __tablename__ = "stored_files"
    __table_args__ = (
        Index("ix_stored_files_api_created", "api_key_id", "created_at"),
        Index("ix_stored_files_data_type", "data_type"),
        Index("ix_stored_files_geometry_type", "geometry_type"),
        Index("ix_stored_files_file_format", "file_format"),
        Index("ix_stored_files_upload_id", "upload_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    layer_name: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
        comment="图层名/逻辑名称（从 original_name 中拆分，用于前端展示）"
    )
    content_type: Mapped[str] = mapped_column(String(256), nullable=False, default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_archive: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_archive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── 分片上传支持 ──
    file_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="文件 SHA-256 哈希，用于去重"
    )
    chunk_index: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="分片序号，0 表示完整文件"
    )
    total_chunks: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, comment="总分片数"
    )
    upload_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="上传会话 ID（文件 SHA-256），合并前追踪"
    )
    chunk_dir: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="临时切片目录路径"
    )

    # ── 数据标准关联 ──
    standard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="SET NULL"),
        nullable=True, index=True, comment="关联数据标准"
    )
    standard_table_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("standard_tables.id", ondelete="SET NULL"),
        nullable=True, comment="关联标准表"
    )
    data_standard_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="关联标准名称（冗余，便于列表展示）"
    )

    # ── 数据分类与结构信息 ──
    data_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown",
        comment="数据类型: geometry/table/archive/unknown"
    )
    geometry_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="几何细分类型: Point/LineString/Polygon/MultiPolygon/3d/table/null"
    )
    file_format: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown",
        comment="文件格式: geojson/shp/gpkg/kml/dxf/gml/gdb/mdb/obj/csv/xlsx/tab/mif/zip/tar"
    )
    detected_crs: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, comment="检测到的坐标系: EPSG:4490 / EPSG:4326 / 完整 CRS WKT（如 PROJCS）"
    )
    detected_crs_srid: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="入库要素实际使用的 SRID（EPSG 编码或 VCS-CUSTOM 自定义注册号）；NULL=历史数据未解析"
    )
    crs_is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="坐标系是否为自定义注册 SRID（非 EPSG 标准编码）"
    )
    layer_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="图层数量"
    )
    feature_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="要素总数（物化字段）"
    )
    # ── 图层 × 几何类型归类统计（v2.10.5，聚合冗余列，列表页零 JOIN）──
    point_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="点要素数（聚合冗余列）"
    )
    line_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="线要素数（聚合冗余列）"
    )
    polygon_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="面要素数（聚合冗余列）"
    )
    field_schema: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="字段结构 [{name,type,nullable,sample}, ...]"
    )

    # ── CAD 元数据（v2.18）──
    cad_metadata: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="CAD 元数据缓存: {version, units, extent_min, extent_max, entity_type_counts, ...}"
    )
    cad_preview_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True,
        comment="CAD 预览文件路径 (data/previews/{file_id}.svg)"
    )

    # ── 审计 ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
