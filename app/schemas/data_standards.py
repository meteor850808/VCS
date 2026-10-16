"""数据标准管理请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DataStandardFieldResponse(BaseModel):
    id: str
    field_name: str
    field_type: str
    length: int | None = None
    nullable: bool = True
    allowed_values: list | None = None
    description: str | None = None
    ordering: int = 0

    model_config = ConfigDict(from_attributes=True)


class DataStandardSheetResponse(BaseModel):
    id: str
    sheet_name: str
    ordering: int = 0
    fields: list[DataStandardFieldResponse] = []

    model_config = ConfigDict(from_attributes=True)


class DataStandardBrief(BaseModel):
    id: str
    name: str
    description: str | None = None
    file_name: str | None = None
    sheet_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DataStandardDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    file_name: str
    sheet_count: int
    sheets: list[DataStandardSheetResponse] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
