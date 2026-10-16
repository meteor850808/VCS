"""坐标转换结果 请求/响应 schema（v2.11）。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TransformResultBrief(BaseModel):
    """列表项（不含 output_data）。"""
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_file_id: str | None
    source_file_name: str
    source_crs: str
    target_crs: str
    source_srid: int | None
    target_srid: int | None
    feature_count: int
    output_size: int
    created_at: datetime


class TransformResultDetail(TransformResultBrief):
    """详情（含元数据但不含 output_data — 下载走专用端点）。"""
    pass
