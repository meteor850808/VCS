"""文件存储请求/响应模型 — v2.4 支持数据分类、结构信息、切片上传。"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── 字段结构子模型 ──────────────────────────────────────────────────────────────

class FieldSchemaItem(BaseModel):
    """单个字段的结构信息。"""
    name: str
    field_type: str = "string"     # string/integer/float/date/geometry
    nullable: bool = True
    sample: Any = None             # 采样值，便于预览


class LayerStructure(BaseModel):
    """单个图层的结构信息。"""
    name: str
    geometry_type: Optional[str] = None   # Point/LineString/Polygon/mixed/null(表)
    field_count: int = 0
    feature_count: int = 0
    # 图层 × 类型三维计数（v2.10.5）
    point_count: int = 0
    line_count: int = 0
    polygon_count: int = 0
    fields: list[FieldSchemaItem] = []


# ── 列表 / 简要响应 ────────────────────────────────────────────────────────────

class StoredFileBrief(BaseModel):
    """文件列表项 — 包含所有新增分类与结构字段。"""
    id: str
    original_name: str
    layer_name: str | None = None
    content_type: str
    file_size: int
    description: str | None = None
    source_archive: str | None = None
    is_archive: bool = False

    # 数据分类
    data_type: str = "unknown"         # geometry/table/archive/unknown
    geometry_type: str | None = None   # Point/LineString/Polygon/3d/table
    file_format: str = "unknown"       # geojson/shp/gpkg/...
    detected_crs: str | None = None
    detected_crs_srid: int | None = None
    crs_is_custom: bool = False
    layer_count: int = 0
    feature_count: int = 0
    # 图层 × 类型三维计数（v2.10.5，聚合冗余列）
    point_count: int = 0
    line_count: int = 0
    polygon_count: int = 0

    # 标准关联
    standard_id: str | None = None
    standard_table_id: str | None = None
    data_standard_name: str | None = None

    # 审计
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedFiles(BaseModel):
    items: list[StoredFileBrief] = []
    total: int = 0
    page: int = 1
    page_size: int = 20


# ── 详情响应 ───────────────────────────────────────────────────────────────────

class StoredFileDetail(BaseModel):
    """文件详情 — 包含完整的字段结构。"""
    id: str
    api_key_id: str
    original_name: str
    layer_name: str | None = None
    content_type: str
    file_size: int
    description: str | None = None
    source_archive: str | None = None
    is_archive: bool = False

    # 数据分类
    data_type: str = "unknown"
    file_format: str = "unknown"
    detected_crs: str | None = None
    detected_crs_srid: int | None = None
    crs_is_custom: bool = False
    layer_count: int = 0
    feature_count: int = 0
    # 图层 × 类型三维计数（v2.10.5，聚合冗余列）
    point_count: int = 0
    line_count: int = 0
    polygon_count: int = 0
    field_schema: list[dict] | None = None   # JSONB → list[dict]

    # 标准关联
    standard_id: str | None = None
    standard_table_id: str | None = None
    data_standard_name: str | None = None

    # 分片
    file_hash: str | None = None
    chunk_index: int = 0
    total_chunks: int = 1

    # 审计
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ── 上传响应 ───────────────────────────────────────────────────────────────────

class StoredFileUploadResponse(BaseModel):
    id: str
    original_name: str
    file_size: int
    file_format: str = "unknown"
    data_type: str = "unknown"
    geometry_type: str | None = None
    detected_crs: str | None = None
    layer_count: int = 0
    feature_count: int = 0
    extracted_count: int = 0
    data_standard_name: str | None = None
    standard_id: str | None = None
    standard_table_id: str | None = None  # 【S4】关联的标准表 ID（上传关联层组后回填）
    warning: str | None = None
    message: str
    extraction_details: list[dict] | None = None  # 每个提取文件的详情


# ── 结构预览响应 ───────────────────────────────────────────────────────────────

class FileStructureResponse(BaseModel):
    """上传后返回的结构预览（用于前端弹窗）。"""
    file_id: str
    file_format: str
    data_type: str
    detected_crs: str | None = None
    layer_count: int = 0
    feature_count: int = 0
    # 文件级三维计数（v2.10.5）
    point_count: int = 0
    line_count: int = 0
    polygon_count: int = 0
    layers: list[LayerStructure] = []


# ── 元数据更新请求 ─────────────────────────────────────────────────────────────

class StoredFileUpdateRequest(BaseModel):
    """PATCH /data/{file_id} — 更新文件元数据。"""
    description: str | None = None
    standard_id: str | None = None
    standard_table_id: str | None = None
    data_standard_name: str | None = None


# ── 切片上传相关 ───────────────────────────────────────────────────────────────

class ChunkUploadRequest(BaseModel):
    """POST /data/upload/chunk — 单片上传参数。"""
    upload_id: str = Field(..., description="上传会话 ID（文件 SHA-256）")
    chunk_index: int = Field(..., ge=0, description="切片序号（从 0 开始）")
    total_chunks: int = Field(..., ge=1, description="总切片数")


class MergeRequest(BaseModel):
    """POST /data/upload/merge — 合并切片并入库。"""
    upload_id: str = Field(..., description="上传会话 ID")
    filename: str = Field(..., description="原始文件名")
    total_chunks: int = Field(..., ge=1)
    standard_id: str | None = Field(None, description="关联数据标准 ID")
    standard_table_id: str | None = Field(None, description="关联标准表 ID")
    auto_create_standard: bool = Field(False, description="是否根据结构自动创建标准表")
    description: str | None = None


class ChunkStatusResponse(BaseModel):
    """GET /data/upload/chunk-status — 查询上传进度。"""
    upload_id: str
    filename: str
    total_chunks: int
    uploaded_chunks: list[int] = []
    file_size: int = 0
    status: str = "uploading"    # uploading/merging/completed/failed


class InstantUploadResponse(BaseModel):
    """GET /data/upload/check — 秒传检查结果。"""
    exists: bool
    file_id: str | None = None
    original_name: str | None = None
    file_size: int | None = None
    feature_count: int | None = None
