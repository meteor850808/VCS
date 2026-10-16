"""空间要素分表模型 — 按几何类型拆为 point / line / polygon 三张分区表。

每张表是 PostgreSQL 声明式分区父表（PARTITION BY LIST (stored_file_id)），
每个数据源（stored_file_id）自动对应一个物理分区——实现"一个数据源一个表"，
同时对外保持单表语义，现有 ORM/SQL 查询透明。

v2.10 变更：
- 表结构改为 LIST 分区（分区键 stored_file_id）
- 主键改为复合 (stored_file_id, id)（分区表主键须含分区键）
- stored_file_id 改为 NOT NULL
- 新增 (stored_file_id, feature_index) 复合索引与 properties GIN 索引（质检热点）
- properties 由 JSON 改为 JSONB（PG 推荐；GIN 索引要求 jsonb，且支持 JSON 运算符质检过滤）
- 注意：DB 中不建 stored_file_id → stored_files 的 FK（子任务独立会话写入早于
  子记录提交，FK 会违反时序）；模型声明仅作文档用途
"""

import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Index, Integer, String, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.db.session import Base


# ── 几何类型 → 表名 / PostGIS 类型 ──────────────────────────────────────────
GEOMETRY_TABLE_MAP: dict[str, tuple[str, str]] = {
    # geometry_type -> (tablename, postgis_type)
    "Point":            ("point_features",   "POINT"),
    "MultiPoint":       ("point_features",   "MULTIPOINT"),
    "LineString":       ("line_features",    "LINESTRING"),
    "MultiLineString":  ("line_features",    "MULTILINESTRING"),
    "Polygon":          ("polygon_features", "POLYGON"),
    "MultiPolygon":     ("polygon_features", "MULTIPOLYGON"),
}

ALL_GEOMETRY_TYPES = frozenset(GEOMETRY_TABLE_MAP.keys())


def resolve_table(geometry_type: str) -> tuple[str, str] | None:
    """根据几何类型返回 (表名, PostGIS 类型)，非标准类型返回 None。"""
    return GEOMETRY_TABLE_MAP.get(geometry_type)


def partition_name(table_name: str, stored_file_id: str) -> str:
    """分区名：<table>_p_<stored_file_id>。

    stored_file_id 为 uuid（含 '-'），'-' 在 PG 标识符中需引号，故替换为 '_'
    （确定性映射，单向使用：仅用于生成分区名；FOR VALUES 仍用原始 sid）。
    """
    safe_id = stored_file_id.replace("-", "_")
    return f"{table_name}_p_{safe_id}"


# ── 抽象基类（不创建物理表）────────────────────────────────────────────────

class _FeatureBase:
    """三张分区表共用的列定义（abstract mixin）。

    主键为复合 (stored_file_id, id)：分区表要求主键必须包含分区键。
    """
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    stored_file_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, nullable=False, index=True,
        comment="数据源/文件 ID，也是 LIST 分区键（每源一个分区）",
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    layer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_name: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", index=True,
        comment="来源文件名（不含路径，如 data.geojson）"
    )
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ── 点要素表 ─────────────────────────────────────────────────────────────────

class PointFeature(Base, _FeatureBase):
    __tablename__ = "point_features"
    __table_args__ = (
        CheckConstraint(
            "geometry IS NULL OR ST_IsValid(geometry)",
            name="ck_point_features_valid"
        ),
        Index("ix_point_features_geometry", "geometry", postgresql_using="gist"),
        Index("ix_point_features_src_idx", "stored_file_id", "feature_index"),
        Index("ix_point_features_props", "properties", postgresql_using="gin"),
        {"postgresql_partition_by": "LIST (stored_file_id)"},
    )

    geometry_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="Point", index=True
    )
    geometry: Mapped[bytes | None] = mapped_column(
        Geometry('GEOMETRY', spatial_index=False), nullable=True
    )


# ── 线要素表 ─────────────────────────────────────────────────────────────────

class LineFeature(Base, _FeatureBase):
    __tablename__ = "line_features"
    __table_args__ = (
        CheckConstraint(
            "geometry IS NULL OR ST_IsValid(geometry)",
            name="ck_line_features_valid"
        ),
        Index("ix_line_features_geometry", "geometry", postgresql_using="gist"),
        Index("ix_line_features_src_idx", "stored_file_id", "feature_index"),
        Index("ix_line_features_props", "properties", postgresql_using="gin"),
        {"postgresql_partition_by": "LIST (stored_file_id)"},
    )

    geometry_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="LineString", index=True
    )
    geometry: Mapped[bytes | None] = mapped_column(
        Geometry('GEOMETRY', spatial_index=False), nullable=True
    )


# ── 面要素表 ─────────────────────────────────────────────────────────────────

class PolygonFeature(Base, _FeatureBase):
    __tablename__ = "polygon_features"
    __table_args__ = (
        CheckConstraint(
            "geometry IS NULL OR ST_IsValid(geometry)",
            name="ck_polygon_features_valid"
        ),
        Index("ix_polygon_features_geometry", "geometry", postgresql_using="gist"),
        Index("ix_polygon_features_src_idx", "stored_file_id", "feature_index"),
        Index("ix_polygon_features_props", "properties", postgresql_using="gin"),
        {"postgresql_partition_by": "LIST (stored_file_id)"},
    )

    geometry_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="Polygon", index=True
    )
    geometry: Mapped[bytes | None] = mapped_column(
        Geometry('GEOMETRY', spatial_index=False), nullable=True
    )


# ── 几何类型路由 ──────────────────────────────────────────────────────────────

FEATURE_MODEL_MAP: dict[str, type[Base]] = {
    "Point": PointFeature,
    "MultiPoint": PointFeature,
    "LineString": LineFeature,
    "MultiLineString": LineFeature,
    "Polygon": PolygonFeature,
    "MultiPolygon": PolygonFeature,
}

ALL_FEATURE_TABLES = [PointFeature, LineFeature, PolygonFeature]
