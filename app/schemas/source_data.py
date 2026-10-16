"""源数据管理请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FieldDictLinkResponse(BaseModel):
    id: str
    field_id: str
    dict_id: str
    filter_condition: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SourceFieldResponse(BaseModel):
    id: str
    field_name: str
    field_alias: str | None = None
    field_type: str | None = None
    data_type: str = "string"
    length: int | None = None
    precision: int | None = None
    nullable: bool = True
    default_value: str | None = None
    description: str | None = None
    is_primary_key: bool = False
    is_required: bool = False
    allowed_values: list | None = None
    ordering: int = 0
    dict_links: list[FieldDictLinkResponse] = []

    model_config = ConfigDict(from_attributes=True)


class SourceTableResponse(BaseModel):
    id: str
    table_name: str
    table_code: str | None = None
    description: str | None = None
    geometry_type: str | None = None
    source: str | None = None
    version: str | None = None
    is_active: bool = True
    ordering: int = 0
    fields: list[SourceFieldResponse] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceTableBrief(BaseModel):
    id: str
    table_name: str
    table_code: str | None = None
    geometry_type: str | None = None
    source: str | None = None
    field_count: int = 0
    is_active: bool = True
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceFieldCreate(BaseModel):
    field_name: str
    field_alias: str | None = None
    field_type: str | None = None
    data_type: str = "string"
    length: int | None = None
    precision: int | None = None
    nullable: bool = True
    default_value: str | None = None
    description: str | None = None
    is_primary_key: bool = False
    is_required: bool = False
    allowed_values: list | None = None
    ordering: int = 0


class SourceTableCreate(BaseModel):
    table_name: str
    table_code: str | None = None
    description: str | None = None
    geometry_type: str | None = None
    source: str | None = None
    version: str | None = None
    is_active: bool = True
    ordering: int = 0
    fields: list[SourceFieldCreate] = []


class SourceTableUpdate(BaseModel):
    table_name: str | None = None
    table_code: str | None = None
    description: str | None = None
    geometry_type: str | None = None
    source: str | None = None
    version: str | None = None
    is_active: bool | None = None
    ordering: int | None = None


class DictionaryItemResponse(BaseModel):
    id: str
    item_code: str
    item_name: str
    parent_code: str | None = None
    description: str | None = None
    ordering: int = 0

    model_config = ConfigDict(from_attributes=True)


class DictionaryItemCreate(BaseModel):
    item_code: str
    item_name: str
    parent_code: str | None = None
    description: str | None = None
    ordering: int = 0


class SourceDictionaryResponse(BaseModel):
    id: str
    dict_name: str
    dict_code: str | None = None
    description: str | None = None
    is_system: bool = False
    items: list[DictionaryItemResponse] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceDictionaryBrief(BaseModel):
    id: str
    dict_name: str
    dict_code: str | None = None
    description: str | None = None
    item_count: int = 0
    is_system: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceDictionaryCreate(BaseModel):
    dict_name: str
    dict_code: str | None = None
    description: str | None = None
    items: list[DictionaryItemCreate] = []
