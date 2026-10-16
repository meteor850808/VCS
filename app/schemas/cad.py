# -*- coding: utf-8 -*-
"""CAD API 响应模型。"""
from pydantic import BaseModel


class CADLayerResponse(BaseModel):
    name: str
    color_index: int | None = None
    color_rgb: list[int] | None = None
    linetype: str | None = None
    is_visible: bool = True
    is_frozen: bool = False
    is_locked: bool = False
    entity_count: int = 0
    geometry_type: str = "unknown"


class CADInfoResponse(BaseModel):
    file_id: str
    format: str
    version: str | None = None
    units: str | None = None
    extent_min: list[float] | None = None
    extent_max: list[float] | None = None
    entity_counts: dict[str, int] = {}
    entity_total: int = 0
    layer_count: int = 0
    has_text: bool = False
    has_dimensions: bool = False
    has_blocks: bool = False
    has_hatches: bool = False
    preview_url: str | None = None


class CADEntityResponse(BaseModel):
    handle: str
    entity_type: str
    layer: str
    color_index: int | None = None
    start_point: list[float] | None = None
    end_point: list[float] | None = None
    length: float | None = None
    area: float | None = None
    text_content: str | None = None


class CADEntityPageResponse(BaseModel):
    total: int
    offset: int
    limit: int
    entities: list[CADEntityResponse]


class CADLayersResponse(BaseModel):
    layers: list[CADLayerResponse]