# VCS v2.3 数据质检模块技术方案

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          前端 (index.html)                               │
│  质检配置面板: 标准→标准表级联 / 13项拓扑规则 / 8项容差 / 文件选择        │
│  质检结果展示: 五维评分卡片 / 属性检查表 / 拓扑检查表 / 导出按钮          │
└───────────────┬─────────────────────────────────────────┬───────────────┘
                │ REST API                                │
┌───────────────▼─────────────────────────────────────────▼───────────────┐
│                        FastAPI Router                                    │
│  quality.py + data.py                                                   │
│  ├── POST /quality/check           v2 五维评分                           │
│  ├── POST /quality/check/upload    上传 + 质检（兼容旧接口）             │
│  └── POST /data/{id}/quality-check 已入库文件质检                        │
└───────────────┬─────────────────────────────────────────┬───────────────┘
                │                                         │
┌───────────────▼─────────────────────────────────────────▼───────────────┐
│                     quality_checker.py (编排器)                          │
│  run_quality_check()                                                    │
│  ┌─────────────┐    ┌───────────────┐    ┌──────────────┐              │
│  │ Phase 1     │    │ Phase 2       │    │ Phase 3      │              │
│  │ 属性检查     │───▶│ 拓扑检查       │───▶│ 五维评分      │              │
│  │ 9 项 A1-A9  │    │ 13 项 T01-T13 │    │ + 报告存储    │              │
│  └──────┬──────┘    └──────┬────────┘    └──────────────┘              │
│         │                  │                                            │
│  ┌──────▼──────┐    ┌──────▼────────┐                                  │
│  │ attribute_  │    │ topology_     │                                  │
│  │ checker.py  │    │ checker.py    │                                  │
│  │ (pandas向量化)│   │ (PostGIS+shapely)│                                │
│  └──────┬──────┘    └──────┬────────┘                                  │
└─────────┼──────────────────┼────────────────────────────────────────────┘
          │                  │
┌─────────▼──────────────────▼────────────────────────────────────────────┐
│                     数据访问层                                           │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐     │
│  │ 标准定义加载   │    │ PostGIS 特征加载  │    │ 报告存储           │     │
│  │ StandardTable │    │ point_features   │    │ QualityReport     │     │
│  │ StandardField │    │ line_features    │    │ QualityRuleResult │     │
│  │ DictLink      │    │ polygon_features │    │ QualityViolation  │     │
│  │ DictionaryItem│    │ (properties JSONB)│   │                   │     │
│  └──────────────┘    └──────────────────┘    └───────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 核心数据流

```
用户: 选择文件 + 标准 + 标准表 + 拓扑配置
  │
  ▼
┌─ Phase 0: 准备 ──────────────────────────────────────────────┐
│  load_standard_tables(db, standard_id)                        │
│  → List[StandardTableDef]  (含 fields + dict_item_codes)     │
│                                                               │
│  load_features_by_file(db, stored_file_id, api_key_id)       │
│  → DataFrame (layer_name, properties, geometry_wkt, idx)     │
└───────────────────────┬───────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 1: 属性检查 ──────────────────────────────────────────┐
│  run_attribute_checks(db, standard_id, stored_file_id, ...)  │
│                                                               │
│  A1 必要图层: standard_layers ∩ data_layers                   │
│  A2 类型正确: geometry_type 匹配                              │
│  A3 结构一致: data_fields ⊇ standard_fields                  │
│  A4 必填项:   is_required ∧ value is None                    │
│  A5 值域:     value ∉ dict_item_codes                        │
│  A6 唯一性:   primary_key duplicate detection                 │
│  A7 图层非空: COUNT(*) > 0                                    │
│  A8 小数位:   decimal_places(value) > precision               │
│  A9 长度:     len(str(value)) > length                       │
│                                                               │
│  → (List[CheckResult], {total, passed, failed, by_rule})     │
└───────────────────────┬───────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 2: 拓扑检查 ──────────────────────────────────────────┐
│  run_topology_checks(db, stored_file_id, api_key_id, ...)    │
│                                                               │
│  PostGIS SQL (高效): T01/T03/T04/T05/T06/T07/T08/T09/T12/T13│
│  Shapely (精确):     T02/T10/T11                              │
│                                                               │
│  → (List[TopologyCheckResult], {total, passed, failed})      │
└───────────────────────┬───────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 3: 评分 + 存储 ──────────────────────────────────────┐
│  compute_scores(attr_results, topo_results)                  │
│                                                               │
│  completeness = pass(A4) / total(A4)       × 0.25           │
│  accuracy     = pass(A5) / total(A5)       × 0.25           │
│  consistency  = pass(A1+A2+A3) / total     × 0.20           │
│  uniqueness   = pass(A6) / total(A6)       × 0.15           │
│  topology     = 1 - fail(T*) / total(T*)   × 0.15           │
│  overall      = Σ weighted scores                            │
│                                                               │
│  → QualityReport 写入 DB + JSON 报告文件                      │
│  → QualityCheckResponse 返回前端                              │
└───────────────────────────────────────────────────────────────┘
```

---

## 数据模型

### 标准定义内部表示

```python
@dataclass
class StandardTableDef:
    table_name: str
    table_code: str | None
    geometry_type: str | None    # Point / LineString / Polygon / None
    fields: list[StandardFieldDef]

@dataclass
class StandardFieldDef:
    field_name: str
    data_type: str               # string / integer / float / date / datetime / boolean
    length: int | None
    precision: int | None
    nullable: bool
    is_required: bool
    is_primary_key: bool
    dict_item_codes: list[str]   # 值域允许值列表
```

### 检查结果

```python
@dataclass
class CheckResult:
    rule_code: str               # "31010001" ~ "31010009"
    rule_name: str
    status: str                  # pass / fail / skip
    severity: str                # error / warning / info
    field_name: str              # 涉及字段（可空）
    layer: str                   # 涉及图层（可空）
    failed_count: int
    total_checked: int
    expected: str                # 期望值描述
    actual: str                  # 实际值描述
    message: str                 # 人类可读说明
    samples: list[dict]          # 违规样本（最多 20 条）

@dataclass
class TopologyCheckResult:
    rule_code: str               # "T01" ~ "T13"
    rule_name: str
    status: str
    severity: str
    failed_count: int
    total_checked: int
    message: str
    feature_indices: list[int]   # 违规要素索引（最多 50）
    pairs: list[list[int]]       # 要素对索引（重叠/裂隙）
```

---

## 关键算法

### 层名映射

上传文件的 `layer_name`（文件名去掉扩展名）与标准表 `table_name` 进行模糊匹配：
1. 精确匹配（大小写不敏感）
2. 去掉常见前缀/后缀后匹配（如 `DLG_`, `_图层`）
3. 未匹配则跳过属性检查，仅执行拓扑检查

### 向量化属性检查

```python
# 值域检查（A5）— pandas 向量化
df = load_features_as_dataframe()
for table_def in standard_tables:
    table_df = df[df['layer_name'] == table_def.table_name]
    for field_def in table_def.fields:
        allowed = set(field_def.dict_item_codes)
        if allowed:
            violations = table_df[~table_df[field_def.field_name].isin(allowed)]
```

### PostGIS 拓扑检查示例（T06 面重叠）

```sql
SELECT p1.id, p2.id
FROM polygon_features p1
JOIN polygon_features p2
  ON p1.id < p2.id
  AND p1.stored_file_id = :fid
  AND p2.stored_file_id = :fid
WHERE ST_Overlaps(p1.geometry, p2.geometry)
  AND p1.api_key_id = :akid
  AND p2.api_key_id = :akid;
```

---

## API 详细设计

### POST /quality/check

**请求**:
```
POST /api/v1/quality/check?file_id=<stored_file_id>
Content-Type: application/json
X-API-Key: <key>

{
  "standard_id": "std_xxx",
  "standard_table_id": "tbl_xxx",
  "geometry_type": "Polygon",
  "topology_config": {
    "enabled_rules": ["empty_geometry", "polygon_overlap", "self_intersection"],
    "tolerances": {"min_area": 0.001, "overlap_tolerance": 0.0001}
  }
}
```

**响应**:
```json
{
  "report_id": "uuid",
  "stored_file_id": "file_id",
  "file_name": "测试数据.geojson",
  "standard_id": "std_xxx",
  "standard_table_id": "tbl_xxx",
  "attr_results": [
    {
      "rule_code": "31010001",
      "rule_name": "必要图层检查",
      "status": "pass",
      "severity": "error",
      "failed_count": 0,
      "total_checked": 1,
      "message": "所有图层均在标准定义中",
      "samples": []
    }
  ],
  "attr_summary": {"total": 9, "passed": 7, "failed": 2, "skipped": 0},
  "topo_results": [
    {
      "rule_code": "T01",
      "rule_name": "空几何",
      "status": "fail",
      "severity": "error",
      "failed_count": 3,
      "total_checked": 150,
      "message": "3 个要素几何为空",
      "feature_indices": [12, 45, 89]
    }
  ],
  "topo_summary": {"total": 8, "passed": 6, "failed": 2},
  "scores": {
    "completeness": 0.95,
    "accuracy": 0.88,
    "consistency": 1.0,
    "uniqueness": 1.0,
    "topology": 0.973,
    "overall": 0.954
  },
  "total_violations": 5,
  "violation_samples": [...],
  "created_at": "2026-07-21T12:00:00Z"
}
```

---

## 性能设计

### 属性检查
- **向量化**: 使用 pandas DataFrame 批量处理，避免逐行循环
- **批量加载**: 一次查询加载全部特征到 DataFrame，按 `layer_name` 分组
- **限制样本**: 每项检查最多返回 20 条违规样本

### 拓扑检查
- **PostGIS 优先**: 统计类规则（空几何/极值/重叠/裂隙）使用 SQL，利用空间索引
- **shapely 混合**: 仅复杂几何分析（自相交/连通性/凸度）使用 shapely
- **异步执行**: 全部基于 `AsyncSession`，不阻塞事件循环

### 报告存储
- **JSON 文件**: 完整报告写入文件系统，数据库仅存摘要
- **五维评分**: 数据库字段直接存储，无需重新计算

---

## 安全考虑

1. **租户隔离**: 所有查询通过 `api_key_id` 过滤
2. **输入验证**: 标准 ID、文件 ID 在 API 层校验
3. **SQL 注入防护**: 全部使用 SQLAlchemy ORM / 参数化查询
4. **文件大小限制**: 特征加载受 `max_features_per_upload` 限制
