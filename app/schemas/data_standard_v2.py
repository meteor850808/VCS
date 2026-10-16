"""数据标准 v2.2 Schema 定义。"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── 通用分页 ──────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    """通用分页响应。"""
    items: list = []
    total: int = 0
    page: int = 1
    page_size: int = 20


# ── 数据标准 ──────────────────────────────────────────────────────────────────

class DataStandardCreate(BaseModel):
    """创建数据标准。"""
    name: str = Field(..., max_length=256, description="标准名称")
    code: str | None = Field(None, max_length=64, description="标准编码（导入回传的匹配键）")
    description: str | None = Field(None, description="标准描述")
    category: str = Field("custom", description="分类: national/industry/custom")


class DataStandardUpdate(BaseModel):
    """更新数据标准。"""
    name: str | None = Field(None, max_length=256)
    code: str | None = Field(None, max_length=64)
    description: str | None = None
    category: str | None = None
    status: str | None = Field(None, description="状态: draft/published/archived")


class DataStandardBrief(BaseModel):
    """数据标准列表项。"""
    id: str
    code: str | None = None
    name: str
    description: str | None = None
    category: str
    status: str
    version: int
    file_name: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


class DataStandardDetail(BaseModel):
    """数据标准详情（含完整层级结构）。

    - layer_groups：仅根层组在顶层，子层组嵌在 children 中
      （模型关系返回全部层组，须过滤，否则树重复渲染）
    - dictionaries：模型属性名为 standard_dictionaries，须用校验别名映射
    """
    id: str
    code: str | None = None
    name: str
    description: str | None = None
    category: str
    status: str
    version: int
    file_name: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    layer_groups: list["LayerGroupResponse"] = []
    dictionaries: list["DictionaryBrief"] = Field(
        default_factory=list,
        validation_alias="standard_dictionaries",
        description="标准下的字典列表（模型属性为 standard_dictionaries）",
    )
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _keep_root_groups_only(self):
        """顶层只保留根层组；子层组通过 children 嵌套呈现。"""
        self.layer_groups = [g for g in self.layer_groups if g.parent_id is None]
        return self


# ── 层组 ──────────────────────────────────────────────────────────────────────

class LayerGroupCreate(BaseModel):
    """创建层组。"""
    group_name: str = Field(..., max_length=256, description="层组名称")
    group_code: str | None = Field(None, max_length=64, description="层组编码")
    parent_id: str | None = Field(None, description="父层组 ID，NULL 表示顶层")
    description: str | None = None
    ordering: int = 0


class LayerGroupUpdate(BaseModel):
    """更新层组。"""
    group_name: str | None = Field(None, max_length=256)
    group_code: str | None = None
    description: str | None = None
    ordering: int | None = None


class LayerGroupMove(BaseModel):
    """移动层组。"""
    new_parent_id: str | None = Field(None, description="新父层组 ID，NULL 表示移到顶层")


class LayerGroupResponse(BaseModel):
    """层组响应（含子层组和表）。"""
    id: str
    standard_id: str
    parent_id: str | None = None
    group_name: str
    group_code: str | None = None
    description: str | None = None
    ordering: int
    created_at: datetime
    updated_at: datetime | None = None
    children: list["LayerGroupResponse"] = []
    tables: list["TableBrief"] = []
    table_count: int = 0
    model_config = ConfigDict(from_attributes=True)


# ── 数据表 ────────────────────────────────────────────────────────────────────

class TableCreate(BaseModel):
    """创建数据表。"""
    table_name: str = Field(..., max_length=256, description="表名")
    table_code: str | None = Field(None, max_length=64, description="表编码")
    table_type: str = Field("spatial", description="类型: spatial/attribute/relationship")
    geometry_type: str | None = Field(None, description="几何类型: Point/LineString/Polygon/None")
    description: str | None = None
    ordering: int = 0
    fields: list["FieldCreate"] = []


class TableUpdate(BaseModel):
    """更新数据表。"""
    table_name: str | None = Field(None, max_length=256)
    table_code: str | None = None
    table_type: str | None = None
    geometry_type: str | None = None
    description: str | None = None
    ordering: int | None = None


class TableBrief(BaseModel):
    """数据表列表项。"""
    id: str
    layer_id: str
    table_name: str
    table_code: str | None = None
    table_type: str
    geometry_type: str | None = None
    description: str | None = None
    ordering: int
    field_count: int = 0
    created_at: datetime
    is_primary: bool = True
    model_config = ConfigDict(from_attributes=True)


class TableDetail(BaseModel):
    """数据表详情（含字段）。"""
    id: str
    layer_id: str
    standard_id: str
    table_name: str
    table_code: str | None = None
    table_type: str
    geometry_type: str | None = None
    description: str | None = None
    ordering: int
    created_at: datetime
    updated_at: datetime | None = None
    fields: list["FieldResponse"] = []
    model_config = ConfigDict(from_attributes=True)


# ── S9 表多分类（多挂/归属） ───────────────────────────────────────────────────

class TableAttachRequest(BaseModel):
    """挂载数据表到层组（多分类）。"""
    is_primary: bool = Field(False, description="是否设为主归属（True 会转移主归属）")
    ordering: int | None = Field(None, description="层组内排序（默认 0）")


class LayerGroupBrief(BaseModel):
    """层组简要（归属面板/下拉用）。"""
    id: str
    standard_id: str
    group_name: str
    group_code: str | None = None
    parent_id: str | None = None
    model_config = ConfigDict(from_attributes=True)


class TableLayerLink(BaseModel):
    """数据表的归属信息。"""
    layer_group: LayerGroupBrief
    is_primary: bool = False
    ordering: int = 0


# ── 字段 ──────────────────────────────────────────────────────────────────────

class FieldCreate(BaseModel):
    """创建字段。"""
    field_name: str = Field(..., max_length=256, description="字段名")
    field_alias: str | None = Field(None, max_length=256, description="中文名")
    data_type: str = Field("string", description="数据类型: string/integer/float/date/datetime/geometry/boolean")
    length: int | None = Field(None, description="最大长度")
    precision: int | None = Field(None, description="小数精度")
    nullable: bool = True
    is_primary_key: bool = False
    is_required: bool = False
    default_value: str | None = None
    description: str | None = None
    ordering: int = 0


class FieldUpdate(BaseModel):
    """更新字段。"""
    field_name: str | None = Field(None, max_length=256)
    field_alias: str | None = None
    data_type: str | None = None
    length: int | None = None
    precision: int | None = None
    nullable: bool | None = None
    is_primary_key: bool | None = None
    is_required: bool | None = None
    default_value: str | None = None
    description: str | None = None
    ordering: int | None = None


class FieldResponse(BaseModel):
    """字段响应。"""
    id: str
    table_id: str
    field_name: str
    field_alias: str | None = None
    data_type: str
    length: int | None = None
    precision: int | None = None
    nullable: bool
    is_primary_key: bool
    is_required: bool
    default_value: str | None = None
    description: str | None = None
    ordering: int
    created_at: datetime
    updated_at: datetime | None = None
    dict_links: list["FieldDictLinkResponse"] = []
    model_config = ConfigDict(from_attributes=True)


class FieldReorder(BaseModel):
    """字段排序。"""
    field_ids: list[str] = Field(..., description="按新顺序排列的字段 ID 列表")


# ── 数据字典 ──────────────────────────────────────────────────────────────────

class DictionaryCreate(BaseModel):
    """创建字典。"""
    dict_name: str = Field(..., max_length=256, description="字典名称")
    dict_code: str | None = Field(None, max_length=64, description="字典编码")
    description: str | None = None
    scope: str = Field("standard", description="范围: standard/global")
    items: list["DictionaryItemCreate"] = []


class DictionaryUpdate(BaseModel):
    """更新字典。"""
    dict_name: str | None = Field(None, max_length=256)
    dict_code: str | None = None
    description: str | None = None
    scope: str | None = None


class DictionaryBrief(BaseModel):
    """字典列表项。"""
    id: str
    standard_id: str | None = None
    dict_name: str
    dict_code: str | None = None
    description: str | None = None
    scope: str
    is_system: bool
    item_count: int = 0
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DictionaryDetail(BaseModel):
    """字典详情（含条目）。"""
    id: str
    standard_id: str | None = None
    api_key_id: str | None = None
    dict_name: str
    dict_code: str | None = None
    description: str | None = None
    scope: str
    is_system: bool
    created_at: datetime
    updated_at: datetime | None = None
    items: list["DictionaryItemResponse"] = []
    model_config = ConfigDict(from_attributes=True)


# ── 字典条目 ──────────────────────────────────────────────────────────────────

class DictionaryItemCreate(BaseModel):
    """创建字典条目。"""
    item_code: str = Field(..., max_length=64, description="码值")
    item_name: str = Field(..., max_length=256, description="名称")
    parent_code: str | None = Field(None, max_length=64, description="父级码值")
    description: str | None = None
    ordering: int = 0


class DictionaryItemUpdate(BaseModel):
    """更新字典条目。"""
    item_code: str | None = Field(None, max_length=64)
    item_name: str | None = Field(None, max_length=256)
    parent_code: str | None = None
    description: str | None = None
    ordering: int | None = None


class DictionaryItemResponse(BaseModel):
    """字典条目响应。"""
    id: str
    dict_id: str
    item_code: str
    item_name: str
    parent_code: str | None = None
    description: str | None = None
    ordering: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── 字段-字典关联 ─────────────────────────────────────────────────────────────

class FieldDictLinkCreate(BaseModel):
    """创建字段-字典关联。"""
    dict_id: str = Field(..., description="字典 ID")
    filter_condition: str | None = Field(None, max_length=256, description="筛选条件")
    is_primary: bool = Field(False, description="是否为主要值域")


class FieldDictLinkUpdate(BaseModel):
    """更新字段-字典关联。"""
    filter_condition: str | None = None
    is_primary: bool | None = None


class FieldDictLinkResponse(BaseModel):
    """字段-字典关联响应。"""
    id: str
    field_id: str
    dict_id: str
    dict_name: str | None = None
    filter_condition: str | None = None
    is_primary: bool
    ordering: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── 版本管理 ──────────────────────────────────────────────────────────────────

class VersionResponse(BaseModel):
    """版本响应。"""
    id: str
    standard_id: str
    version_number: int
    version_label: str | None = None
    change_log: str | None = None
    is_rollback: bool = False
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VersionDetail(BaseModel):
    """版本详情（含快照）。"""
    id: str
    standard_id: str
    version_number: int
    version_label: str | None = None
    snapshot_data: dict
    diff_data: dict | None = None
    change_log: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VersionDiff(BaseModel):
    """版本对比结果。"""
    from_version: int
    to_version: int
    added: dict = {}
    modified: dict = {}
    removed: dict = {}
    summary: list[str] = Field(default_factory=list, description="变更摘要（中文，实体级）")
    stats: dict = Field(default_factory=dict, description="实体级统计 {类型: {added/removed/modified: 数量}}")
    labels: dict = Field(default_factory=dict, description="实体自然键 → 中文名映射")


class PublishRequest(BaseModel):
    """发布标准。"""
    change_log: str | None = Field(None, description="变更说明")


# ── 导入/导出 ─────────────────────────────────────────────────────────────────

class ImportResult(BaseModel):
    """导入结果。"""
    standard_id: str
    name: str
    mode: str = "create"
    updated: bool = False
    version: int | None = None
    layer_groups_count: int = 0
    tables_count: int = 0
    fields_count: int = 0
    dictionaries_count: int = 0
    warnings: list[str] = []
    message: str


class ExportRequest(BaseModel):
    """导出标准。"""
    format: str = Field("json", description="导出格式: excel/json/csv")


# ── 统计（看板汇总） ─────────────────────────────────────────────────────────

class DataStandardV2Stats(BaseModel):
    """数据标准 v2 统计（任务看板汇总概览用）。

    仅统计当前 API Key scope 可见的标准及其关联结构；
    全局字典（scope='global'）为共享维度，单独计数。
    """
    standards_total: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    layers_total: int = 0          # 层组（图层树）总数
    tables_total: int = 0          # 标准表总数
    fields_total: int = 0          # 字段总数
    global_dicts: int = 0          # 全局字典数（共享维度）
    standard_dicts: int = 0        # 标准级字典数
    dict_items_total: int = 0      # 字典项总数（全局 + 标准级）
    versions_total: int = 0        # 版本快照总数


# ── 知识图谱（A 档纯视图） ────────────────────────────────────────────────────

class GraphNode(BaseModel):
    """图谱节点 — standard / group / table / dict 四种。

    摘要契约：节点**不携带**明细数组（fields / items）—— 真实标准
    （SHGT_SURVEY）有 3288 字段 + 2031 条目，一次全给约 1.5MB；
    明细走 `/graph/nodes/{id}` 懒加载。
    """
    id: str
    kind: str = Field(description="standard | group | table | dict")
    name: str
    code: Optional[str] = None
    parent_id: Optional[str] = None
    depth: Optional[int] = None
    field_count: Optional[int] = None   # 表节点徽章
    item_count: Optional[int] = None    # 字典节点徽章
    scope: Optional[str] = None         # dict 节点：standard | global


class GraphEdge(BaseModel):
    """图谱边 — contains（层级/挂载）| binds（表↔字典，按字段数聚合）。

    `source`/`target` 而非 `from`/`to`（Python 关键字 + JS 生态更常见）。
    """
    kind: str = Field(description="contains | binds")
    source: str
    target: str
    field_count: Optional[int] = None   # binds：该表绑到该字典的字段数
    primary: Optional[bool] = None      # contains：主/次归属；binds：含主绑定


class StandardGraphResponse(BaseModel):
    """单标准子图摘要。"""
    standard_id: str
    standard_name: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


class GraphNodeDetailResponse(BaseModel):
    """节点展开明细 — 表节点给字段（含 dict_codes），字典节点给条目。"""
    node_id: str
    kind: str = Field(description="table | dict")
    name: str
    fields: Optional[list[dict]] = None
    items: Optional[list[dict]] = None


# ── 前向引用解析 ──────────────────────────────────────────────────────────────

DataStandardDetail.model_rebuild()
LayerGroupResponse.model_rebuild()
TableDetail.model_rebuild()
FieldResponse.model_rebuild()
DictionaryDetail.model_rebuild()
