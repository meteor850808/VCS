"""API Key / Group 管理请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ── API Key ─────────────────────────────────────────────────────────────────

class ApiKeyCreateRequest(BaseModel):
    name: str
    description: str | None = None
    group_id: str | None = None


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str
    api_key: str
    message: str


class ApiKeyBrief(BaseModel):
    id: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    group_id: str | None = None
    group_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Key Group ───────────────────────────────────────────────────────────────

class KeyGroupCreateRequest(BaseModel):
    name: str
    description: str | None = None


class KeyGroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class KeyGroupBrief(BaseModel):
    id: str
    name: str
    description: str | None = None
    member_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KeyGroupDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: datetime
    api_keys: list[ApiKeyBrief] = []

    model_config = ConfigDict(from_attributes=True)


# ── Key-Group assignment ────────────────────────────────────────────────────

class SetKeyGroupRequest(BaseModel):
    group_id: str | None = None
