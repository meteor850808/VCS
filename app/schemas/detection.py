"""格式检测请求/响应模型。"""

from pydantic import BaseModel


class FieldSchemaResponse(BaseModel):
    name: str
    field_type: str
    nullable: bool = True


class FormatDetectionResponse(BaseModel):
    format_name: str
    encoding: str
    crs_info: str | None = None
    confidence: float
    magic_bytes: str | None = None
    attribute_schema: list[FieldSchemaResponse] = []
    feature_count: int | None = None
