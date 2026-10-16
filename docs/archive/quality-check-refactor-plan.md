# 数据检查模块重构方案

## 一、现状与问题

### 1.1 断裂点

| 问题 | 影响 |
|------|------|
| `StoredFile` 无 `standard_id` | 上传文件无法关联标准 |
| 质检引擎读旧版 `DataStandardField`，不读 v2.2 `StandardField` | 新建标准无法驱动质检 |
| 规则引擎 `rule_engine.py` 从未被调用 | 死代码 |
| 拓扑检查仅 6 项，缺极大极小面/微短线/自相交/多部件/环方向/线连通性 | 不满足需求 |
| 拓扑容差硬编码 | 不可配置 |
| 无层名映射机制 | 上传文件不知道对应标准中哪个表 |
| 属性检查仅做空值统计 | 无类型/值域/长度/精度/唯一性检查 |
| 五维评分模型 `scoring.py` 从未调用 | 死代码 |
| `quality_violations` 表从未写入 | 结构化违规数据缺失 |

### 1.2 可复用资产

| 资产 | 文件 | 复用方式 |
|------|------|----------|
| `StandardField` 模型 | `models/standard_field.py` | **核心数据源** |
| `StandardTable` + `StandardLayerGroup` | 同上 | 层表结构 |
| `StandardDictionary` + `DictionaryItem` | 同上 | 值域定义 |
| `StandardFieldDictLink` | 同上 | 字段↔字典关联 |
| `QualityRule` / `QualityRuleResult` / `QualityViolation` 模型 | `models/quality_rule*.py` | **直接使用** |
| `QualityReport` 模型 | `models/quality_report.py` | 扩展字段 |
| `topology_checker.py` | `services/` | 扩展到 10+ 规则 |
| `quality_exporter.py` | `services/` | 适配新数据结构 |
| `format_detection.py` | `services/` | 保持不变 |
| `feature_store.py` | `services/` | 保持不变 |

---

## 二、架构设计

### 2.1 分层质检流程

```
用户操作: 选择文件 → 选择数据标准 → 选择标准表 → 配置容差 → 开始质检
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 0: 准备                                            │
│  ┌─────────────────┐  ┌──────────────────┐               │
│  │ 标准加载         │  │ 数据加载          │               │
│  │ StandardTable   │  │ PostGIS features │               │
│  │ → StandardField │  │ properties JSON  │               │
│  │ → DictLink      │  │ geometry WKB     │               │
│  │ → DictionaryItem│  │                  │               │
│  └────────┬────────┘  └────────┬─────────┘               │
│           │                    │                          │
│           ▼                    ▼                          │
│  ┌────────────────────────────────────┐                   │
│  │ Layer Name Mapping                 │                   │
│  │ uploaded layer_name ↔ standard table_name │            │
│  └────────────────┬───────────────────┘                   │
│                   │                                       │
│  Phase 1: 属性结构检查（向量化，pandas）                     │
│  ┌────────────────────────────────────┐                   │
│  │ A1 必要图层检查                     │                   │
│  │ A2 要素类型正确性                   │                   │
│  │ A3 属性结构正确性（字段名+类型）      │                   │
│  │ A4 字段必填项 (is_required)         │                   │
│  │ A5 属性值域控制 (DictLink→DictItem) │                   │
│  │ A6 字段唯一值 (is_primary_key)      │                   │
│  │ A7 必要图层不为空                   │                   │
│  │ A8 字段值小数位 (precision)          │                   │
│  │ A9 字段长度限制 (length)             │                   │
│  └────────────────┬───────────────────┘                   │
│                   │                                       │
│  Phase 2: 拓扑检查（shapely + PostGIS）                    │
│  ┌────────────────────────────────────┐                   │
│  │ T1  空几何                          │                   │
│  │ T2  自相交                          │                   │
│  │ T3  极大/极小面                     │                   │
│  │ T4  微短线                          │                   │
│  │ T5  面重叠（多部件检测并入）          │                   │
│  │ T6  点重叠                          │                   │
│  │ T7  线重叠                          │                   │
│  │ T8  面裂隙                          │                   │
│  │ T9  线连通性                        │                   │
│  │ T10 不规则面要素                    │                   │
│  │ T11 环方向（逆时针）                 │                   │
│  │ T12 多部件几何                      │                   │
│  └────────────────┬───────────────────┘                   │
│                   │                                       │
│  Phase 3: 报告生成                                        │
│  ┌────────────────────────────────────┐                   │
│  │ 聚合结果 → QualityReport            │                   │
│  │ 逐条违规 → QualityViolation         │                   │
│  │ 按规则汇总 → QualityRuleResult      │                   │
│  │ 五维评分 → completeness/accuracy/   │                   │
│  │   consistency/uniqueness/topology   │                   │
│  └────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

1. **标准驱动**：所有属性检查规则从 `StandardField` + `StandardFieldDictLink` + `DictionaryItem` 自动生成
2. **向量化执行**：属性检查用 pandas 向量操作，不逐行循环
3. **拓扑复用 PostGIS**：利用已入库的 PostGIS 几何做空间查询，不重读文件
4. **容差全可配**：每个拓扑规则的容差参数从前端传入
5. **结果结构化**：违规记录存 `quality_violations` 表，可追溯到 feature_index

---

## 三、数据模型变更

### 3.1 新增字段

**`stored_files` 表 — 新增关联字段：**
```sql
ALTER TABLE stored_files ADD COLUMN standard_id VARCHAR(36) REFERENCES data_standards(id);
ALTER TABLE stored_files ADD COLUMN standard_table_id VARCHAR(36) REFERENCES standard_tables(id);
```

**`quality_reports` 表 — 扩展报告结构：**
```sql
ALTER TABLE quality_reports ADD COLUMN standard_table_id VARCHAR(36);
ALTER TABLE quality_reports ADD COLUMN layer_mappings JSONB;
ALTER TABLE quality_reports ADD COLUMN topology_config JSONB;
ALTER TABLE quality_reports ADD COLUMN attribute_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN topology_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN score_completeness FLOAT;
ALTER TABLE quality_reports ADD COLUMN score_accuracy FLOAT;
ALTER TABLE quality_reports ADD COLUMN score_consistency FLOAT;
ALTER TABLE quality_reports ADD COLUMN score_uniqueness FLOAT;
ALTER TABLE quality_reports ADD COLUMN score_topology FLOAT;
```

### 3.2 ORM 模型变更

**`app/models/stored_file.py`：**
```python
standard_id: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True
)
standard_table_id: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("standard_tables.id", ondelete="SET NULL"), nullable=True
)
```

**`app/models/quality_report.py`：** 新增 `standard_table_id`, `layer_mappings`, `topology_config`, `attribute_summary`, `topology_summary`, 五维评分字段

### 3.3 复用已有模型（无需改动）

| 模型 | 表 | 用途 |
|------|-----|------|
| `QualityRule` | `quality_rules` | 存储生成/自定义的检查规则 |
| `QualityRuleResult` | `quality_rule_results` | 每条规则的执行结果（passed/failed/count） |
| `QualityViolation` | `quality_violations` | 每条违规记录（feature_index + expected + actual） |

---

## 四、属性结构检查设计

### 4.1 检查项映射

| 检查项 | 检查代码 | 数据来源 | 实现方式 |
|--------|----------|----------|----------|
| **A1 必要图层检查** | 31010001 | `StandardTable` 表名列表 vs 上传文件 layer_name 列表 | set 差集 |
| **A2 要素类型正确性** | 31010002 | `StandardTable.geometry_type` vs 上传要素 geometry_type | 映射表匹配 |
| **A3 属性结构正确性** | 31010003 | `StandardField.field_name` + `data_type` vs 上传字段 | 双向对比（缺字段+多余字段+类型不匹配） |
| **A4 字段必填项** | 31010006 | `StandardField.is_required=True` | pandas `isna()` 向量化 |
| **A5 属性值域控制** | 31010004 | `StandardFieldDictLink` → `DictionaryItem.item_code` | pandas `isin()` 向量化 |
| **A6 字段唯一值** | 31010005 | `StandardField.is_primary_key=True` | pandas `duplicated()` 向量化 |
| **A7 必要图层不为空** | 31010007 | 匹配到的 layer feature_count > 0 | SQL `COUNT(*)` |
| **A8 字段值小数位** | 31010008 | `StandardField.precision` | pandas 字符串解析小数位数 |
| **A9 字段长度限制** | 31010009 | `StandardField.length` | pandas `str.len()` |

### 4.2 属性检查引擎伪代码

```python
async def run_attribute_checks(
    db: AsyncSession,
    standard_id: str,
    stored_file_id: str,
    api_key_id: str,
) -> list[AttributeCheckResult]:
    """
    Phase 1: 属性结构检查
    从 StandardField 生成规则，对 PostGIS features 做向量化验证。
    """
    # 1. 加载标准定义
    standard_tables = await _load_standard_tables(db, standard_id)
    #   → [{table_name, geometry_type, fields: [{field_name, data_type, length, precision, nullable, is_required, is_primary_key, dicts: [item_codes]}]}]
    
    # 2. 加载已入库的 features
    features = await _load_features_by_file(db, stored_file_id, api_key_id)
    #   → [{layer_name, geometry_type, properties: {field: value}, feature_index}]
    
    # 3. 按 layer_name 分组
    layer_groups = group_by_layer(features)
    
    results = []
    
    for table_def in standard_tables:
        layer_feats = layer_groups.get(table_def["table_name"], [])
        df = pd.DataFrame([f["properties"] for f in layer_feats])
        
        # A1: 必要图层存在
        if not layer_feats:
            results.append(AttributeCheckResult(
                rule_code="31010001", status="fail",
                message=f"必要图层 '{table_def['table_name']}' 缺失"
            ))
            continue
        
        # A7: 必要图层不为空
        if len(df) == 0:
            results.append(AttributeCheckResult(
                rule_code="31010007", status="fail",
                message=f"图层 '{table_def['table_name']}' 无要素"
            ))
            continue
        
        # A2: 要素类型正确性
        actual_types = {f["geometry_type"] for f in layer_feats}
        expected_type = table_def["geometry_type"]
        wrong_types = actual_types - {expected_type, None}
        if wrong_types:
            results.append(AttributeCheckResult(
                rule_code="31010002", status="fail",
                message=f"要素类型不匹配: 期望 {expected_type}, 实际 {wrong_types}"
            ))
        
        for field_def in table_def["fields"]:
            fname = field_def["field_name"]
            
            # A3: 属性结构正确性 — 缺失字段
            if fname not in df.columns:
                results.append(AttributeCheckResult(
                    rule_code="31010003", status="fail",
                    field=fname, message=f"字段 '{fname}' 缺失"
                ))
                continue
            
            # A4: 必填项
            if field_def["is_required"]:
                null_count = df[fname].isna().sum()
                if null_count > 0:
                    results.append(AttributeCheckResult(
                        rule_code="31010006", status="fail",
                        field=fname, failed_count=null_count,
                        message=f"必填字段 '{fname}' 有 {null_count} 条空值"
                    ))
            
            # A5: 值域控制
            if field_def["dicts"]:
                allowed = set(field_def["dicts"])
                invalid_mask = ~df[fname].astype(str).isin(allowed) & df[fname].notna()
                invalid_count = invalid_mask.sum()
                if invalid_count > 0:
                    samples = df[invalid_mask][fname].head(5).tolist()
                    results.append(AttributeCheckResult(
                        rule_code="31010004", status="fail",
                        field=fname, failed_count=invalid_count,
                        expected=f"值域: {list(allowed)[:10]}",
                        actual=f"违规值示例: {samples}",
                        message=f"字段 '{fname}' 有 {invalid_count} 条值不在允许值域内"
                    ))
            
            # A6: 唯一性
            if field_def["is_primary_key"]:
                dup_mask = df[fname].duplicated(keep=False)
                dup_count = dup_mask.sum()
                if dup_count > 0:
                    results.append(AttributeCheckResult(
                        rule_code="31010005", status="fail",
                        field=fname, failed_count=dup_count,
                        message=f"主键字段 '{fname}' 有 {dup_count} 条重复值"
                    ))
            
            # A8: 小数位
            if field_def["precision"] and df[fname].notna().any():
                decimals = df[fname].dropna().astype(str).apply(
                    lambda x: len(x.split('.')[1]) if '.' in x else 0
                )
                over_precision = (decimals > field_def["precision"]).sum()
                if over_precision > 0:
                    results.append(AttributeCheckResult(
                        rule_code="31010008", status="fail",
                        field=fname, failed_count=over_precision,
                        message=f"字段 '{fname}' 有 {over_precision} 条超过 {field_def['precision']} 位小数"
                    ))
            
            # A9: 长度限制
            if field_def["length"]:
                lengths = df[fname].dropna().astype(str).str.len()
                over_length = (lengths > field_def["length"]).sum()
                if over_length > 0:
                    results.append(AttributeCheckResult(
                        rule_code="31010009", status="fail",
                        field=fname, failed_count=over_length,
                        message=f"字段 '{fname}' 有 {over_length} 条超过 {field_def['length']} 字符长度"
                    ))
    
    # A3: 多余字段检查
    standard_field_names = {f["field_name"] for t in standard_tables for f in t["fields"]}
    for layer_name, feats in layer_groups.items():
        if feats:
            actual_fields = set(feats[0]["properties"].keys())
            extra = actual_fields - standard_field_names
            if extra:
                results.append(AttributeCheckResult(
                    rule_code="31010003", status="info",
                    message=f"层 '{layer_name}' 存在多余字段: {list(extra)}"
                ))
    
    return results
```

### 4.3 标准数据加载函数

```python
async def _load_standard_tables(db, standard_id) -> list[dict]:
    """
    从 v2.2 StandardField 加载完整标准定义。
    返回: [{table_name, geometry_type, fields: [{field_name, data_type, length, precision, 
            is_required, is_primary_key, nullable, dict_item_codes: [...]}]}]
    """
    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields)
            .selectinload(StandardField.dict_links)
            .selectinload(StandardFieldDictLink.dictionary)
            .selectinload(StandardDictionary.items),
    )
    result = await db.execute(q)
    std = result.scalar_one()
    
    tables = []
    def collect_layers(layer):
        for t in layer.tables:
            fields = []
            for f in t.fields:
                item_codes = []
                for link in f.dict_links:
                    if link.dictionary:
                        item_codes.extend(i.item_code for i in link.dictionary.items)
                fields.append({
                    "field_name": f.field_name,
                    "data_type": f.data_type,
                    "length": f.length,
                    "precision": f.precision,
                    "is_required": f.is_required,
                    "is_primary_key": f.is_primary_key,
                    "nullable": f.nullable,
                    "dict_item_codes": item_codes,
                })
            tables.append({
                "table_name": t.table_name,
                "table_code": t.table_code,
                "geometry_type": t.geometry_type,
                "fields": fields,
            })
        for child in layer.children:
            collect_layers(child)
    
    for lg in std.layer_groups:
        collect_layers(lg)
    
    return tables
```

---

## 五、拓扑检查设计

### 5.1 检查项定义

| 编号 | 规则名 | 检查代码 | 适用几何 | 容差参数 | 实现方式 |
|------|--------|----------|----------|----------|----------|
| T1 | **空几何** | 41010001 | ALL | — | `geom IS NULL OR ST_IsEmpty(geom)` |
| T2 | **自相交** | 41010002 | Line/Polygon | — | `NOT ST_IsValid(geom)` + `ST_IsValidReason` |
| T3 | **极大面** | 41010003 | Polygon | `max_area` (用户设) | `ST_Area(geom) > max_area` |
| T3 | **极小面** | 41010004 | Polygon | `min_area` (用户设) | `ST_Area(geom) < min_area` |
| T4 | **微短线** | 41010005 | Line | `min_length` (用户设) | `ST_Length(geom) < min_length` |
| T5 | **面重叠** | 41010006 | Polygon | `tolerance` (用户设) | `ST_Overlaps(a, b)` + `ST_Area(ST_Intersection) > tolerance` |
| T6 | **点重叠** | 41010007 | Point | `distance` (用户设) | `ST_Distance(a, b) < distance` |
| T7 | **线重叠** | 41010008 | Line | `tolerance` (用户设) | `ST_Overlaps(a, b)` + `ST_Length(ST_Intersection) > tolerance` |
| T8 | **面裂隙** | 41010009 | Polygon | `gap_threshold` (用户设) | `ST_Gaps` via unary_union + envelope diff |
| T9 | **线连通性** | 41010010 | Line | — | 端点匹配: `ST_StartPoint/ST_EndPoint` 交集检测 |
| T10 | **不规则面** | 41010011 | Polygon | `min_sides`, `convexity_threshold` | 面数 < min_sides 或 凸度 < threshold |
| T11 | **环方向** | 41010012 | Polygon | — | `ST_IsValid(geom)` 外环逆时针检测 |
| T12 | **多部件** | 41010013 | ALL | — | `GeometryType(geom) LIKE 'Multi%'` |

### 5.2 拓扑检查配置 Schema（前端传入）

```json
{
  "tolerances": {
    "min_area": 0.0001,
    "max_area": 1000000.0,
    "min_length": 0.001,
    "overlap_tolerance": 0.0001,
    "gap_threshold": 0.001,
    "point_distance": 0.0001,
    "convexity_threshold": 0.3,
    "min_sides": 3
  },
  "enabled_rules": [
    "empty_geometry", "self_intersection", "extreme_area",
    "short_line", "polygon_overlap", "point_overlap",
    "line_overlap", "polygon_gap", "line_connectivity",
    "irregular_polygon", "ring_direction", "multipart_geometry"
  ]
}
```

### 5.3 拓扑检查实现策略

**核心原则：优先用 PostGIS SQL 查询，避免全量加载到 Python**

```python
async def run_topology_checks(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str,
    table_geometry_type: str,  # 从 StandardTable.geometry_type 获取
    tolerances: dict,
    enabled_rules: list[str],
) -> list[TopologyCheckResult]:
    """
    Phase 2: 拓扑检查
    基于 PostGIS 空间函数做高效查询。
    """
    results = []
    
    # T1: 空几何 — SQL 查询
    if "empty_geometry" in enabled_rules:
        q = select(func.count()).select_from(
            _get_feature_model(table_geometry_type)
        ).where(
            and_(
                _model.stored_file_id == stored_file_id,
                or_(_model.geometry.is_(None), func.ST_IsEmpty(_model.geometry))
            )
        )
        empty_count = (await db.execute(q)).scalar()
        if empty_count > 0:
            results.append(TopologyCheckResult(
                rule="empty_geometry", failed_count=empty_count,
                message=f"发现 {empty_count} 条空几何"
            ))
    
    # T2: 自相交 — SQL 查询
    if "self_intersection" in enabled_rules:
        q = select(func.count()).select_from(model).where(
            and_(
                model.stored_file_id == stored_file_id,
                model.geometry.isnot(None),
                not_(func.ST_IsValid(model.geometry))
            )
        )
        invalid_count = (await db.execute(q)).scalar()
        if invalid_count > 0:
            results.append(TopologyCheckResult(
                rule="self_intersection", failed_count=invalid_count,
                message=f"发现 {invalid_count} 条自相交/无效几何"
            ))
    
    # T3: 极大/极小面
    if "extreme_area" in enabled_rules:
        min_a = tolerances.get("min_area", 0.0001)
        max_a = tolerances.get("max_area", 1e9)
        q = select(model.feature_index, func.ST_Area(model.geometry).label("area")).where(
            and_(
                model.stored_file_id == stored_file_id,
                func.ST_Area(model.geometry) < min_a
            )
        )
        small = (await db.execute(q)).all()
        q2 = select(model.feature_index, func.ST_Area(model.geometry).label("area")).where(
            and_(
                model.stored_file_id == stored_file_id,
                func.ST_Area(model.geometry) > max_a
            )
        )
        large = (await db.execute(q2)).all()
        if small:
            results.append(TopologyCheckResult(
                rule="extreme_area", subtype="small",
                failed_count=len(small),
                feature_indices=[r.feature_index for r in small[:50]],
                message=f"发现 {len(small)} 条极小面 (< {min_a})"
            ))
        if large:
            results.append(TopologyCheckResult(
                rule="extreme_area", subtype="large",
                failed_count=len(large),
                feature_indices=[r.feature_index for r in large[:50]],
                message=f"发现 {len(large)} 条极大面 (> {max_a})"
            ))
    
    # T5: 面重叠 — 使用 PostGIS ST_Overlaps + ST_Intersects
    if "polygon_overlap" in enabled_rules and table_geometry_type in ("Polygon", "MultiPolygon"):
        tol = tolerances.get("overlap_tolerance", 0.0001)
        q = text("""
            SELECT a.feature_index as idx_a, b.feature_index as idx_b,
                   ST_Area(ST_Intersection(a.geometry, b.geometry)) as overlap_area
            FROM polygon_features a, polygon_features b
            WHERE a.stored_file_id = :sid AND b.stored_file_id = :sid
              AND a.id < b.id
              AND ST_Overlaps(a.geometry, b.geometry)
              AND ST_Area(ST_Intersection(a.geometry, b.geometry)) > :tol
            LIMIT 500
        """)
        overlaps = (await db.execute(q, {"sid": stored_file_id, "tol": tol})).all()
        if overlaps:
            results.append(TopologyCheckResult(
                rule="polygon_overlap", failed_count=len(overlaps),
                pairs=[(r.idx_a, r.idx_b) for r in overlaps[:100]],
                message=f"发现 {len(overlaps)} 对面重叠 (面积 > {tol})"
            ))
    
    # ... T4-T12 类似模式
    
    return results
```

### 5.4 拓扑检查结果 Schema

```python
@dataclass
class TopologyCheckResult:
    rule: str                    # 规则标识
    subtype: str | None = None   # 子类型 (small/large for extreme_area)
    passed: bool = False
    failed_count: int = 0
    feature_indices: list[int] = field(default_factory=list)  # 违规要素索引（最多500）
    pairs: list[tuple[int, int]] = field(default_factory=list)  # 重叠/间隙配对
    message: str = ""
    detail: dict = field(default_factory=dict)
```

---

## 六、API 设计

### 6.1 新增/变更端点

| 方法 | 路径 | 变更类型 | 说明 |
|------|------|----------|------|
| **POST** | `/quality/check` | **重构** | 新增 `standard_table_id` 参数，移除旧 `standard_fields` |
| **POST** | `/data/{file_id}/quality-check` | **重构** | 同上 |
| **GET** | `/data-standards-v2/{id}/check-preview` | **新增** | 预览标准检查项（生成规则但不执行） |
| **GET** | `/quality/{id}` | **扩展** | 返回 attribute_summary + topology_summary |
| **POST** | `/data/{file_id}/bind-standard` | **新增** | 绑定文件到标准 |

### 6.2 质检请求 Schema

```python
class QualityCheckRequest(BaseModel):
    data_standard_id: str
    standard_table_id: str  # 新增：明确指定检查哪个标准表
    topology_config: TopologyConfig = Field(default_factory=TopologyConfig)

class TopologyConfig(BaseModel):
    enabled_rules: list[str] = [
        "empty_geometry", "self_intersection", "extreme_area",
        "polygon_overlap", "polygon_gap"
    ]
    tolerances: TopologyTolerances = Field(default_factory=TopologyTolerances)

class TopologyTolerances(BaseModel):
    min_area: float = 0.0001
    max_area: float = 1e9
    min_length: float = 0.001
    overlap_tolerance: float = 0.0001
    gap_threshold: float = 0.001
    point_distance: float = 0.0001
    convexity_threshold: float = 0.3
    min_sides: int = 3
```

### 6.3 质检响应 Schema

```python
class QualityCheckResponse(BaseModel):
    report_id: str
    score: QualityScore
    
    # 属性检查结果
    attribute_checks: list[AttributeCheckResult]
    attribute_summary: AttributeSummary  # {total_rules, passed, failed, by_severity}
    
    # 拓扑检查结果
    topology_checks: list[TopologyCheckResult]
    topology_summary: TopologySummary  # {total_rules, passed, failed, by_severity}
    
    # 总体
    total_violations: int
    violation_samples: list[ViolationSample]  # 前 100 条违规样本

@dataclass
class QualityScore:
    completeness: float   # 必填项完整率
    accuracy: float       # 值域/类型正确率
    consistency: float    # 结构一致性
    uniqueness: float     # 主键唯一性
    topology: float       # 拓扑合规率
    overall: float        # 加权总分 = 0.25*completeness + 0.25*accuracy + 0.20*consistency + 0.15*uniqueness + 0.15*topology
```

---

## 七、前端设计

### 7.1 质检配置面板重构

```
┌─────────────────────────────────────┐
│ ⚙ 质检配置                          │
│                                     │
│ 关联数据标准:  [下拉选择标准]          │
│ 检查目标表:    [下拉选择标准表] ←联动  │
│                                     │
│ ── 属性检查项 ──                     │
│ ☑ 必要图层检查                       │
│ ☑ 要素类型正确性                     │
│ ☑ 属性结构正确性                     │
│ ☑ 字段必填项                         │
│ ☑ 属性值域控制                       │
│ ☑ 字段唯一值                         │
│ ☑ 必要图层不为空                     │
│ ☑ 字段值小数位                       │
│ ☑ 字段长度限制                       │
│                                     │
│ ── 拓扑检查项 ──                     │
│ ☑ 空几何                            │
│ ☑ 自相交                            │
│ ☑ 极大/极小面  最小:___ 最大:___     │
│ ☑ 微短线        最小长度:___         │
│ ☑ 面重叠        容差:___            │
│ ☑ 点重叠        距离:___            │
│ ☑ 线重叠        容差:___            │
│ ☑ 面裂隙        阈值:___            │
│ ☑ 线连通性                          │
│ ☑ 不规则面      最少边数:___        │
│ ☑ 环方向                            │
│ ☑ 多部件几何                        │
│                                     │
│ [开始质检]                          │
└─────────────────────────────────────┘
```

### 7.2 标准↔表联动逻辑

```javascript
// 选择标准后，加载该标准的所有层组→表
async function onStandardChange(standardId) {
  const tableSel = document.getElementById('qcTableSel');
  tableSel.innerHTML = '<option value="">请选择标准表...</option>';
  
  if (!standardId) return;
  
  const layers = await GET(`/api/v1/data-standards-v2/${standardId}/layers`);
  // 递归收集所有 StandardTable
  const tables = collectAllTables(layers);
  tables.forEach(t => {
    tableSel.appendChild(new Option(`${t.table_name} (${t.geometry_type})`, t.id));
  });
}

function collectAllTables(layers) {
  let tables = [];
  layers.forEach(lg => {
    (lg.tables || []).forEach(t => tables.push(t));
    if (lg.children) tables = tables.concat(collectAllTables(lg.children));
  });
  return tables;
}
```

### 7.3 质检结果展示重构

```
┌──────────────────────────────────────┐
│ 📊 质检报告 — 道路中心线.gpkg         │
│ 标准: 国土调查数据库标准 v2.1          │
│                                      │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ │
│  │ 92.3 │ │ 98.1 │ │ 85.7 │ │ 100  │ │
│  │完整率 │ │准确率 │ │一致率 │ │拓扑  │ │
│  └──────┘ └──────┘ └──────┘ └──────┘ │
│                                      │
│ ── 属性检查 (9项, 6通过, 3未通过) ──   │
│ ✅ 必要图层检查                       │
│ ✅ 要素类型正确性                     │
│ ✅ 属性结构正确性                     │
│ ❌ 字段必填项 (23条空值)              │
│   └ road_name: 15条, road_width: 8条 │
│ ❌ 属性值域控制 (5条违规)             │
│   └ road_type: "X" 不在值域内        │
│ ✅ 字段唯一值                         │
│ ✅ 必要图层不为空                     │
│ ✅ 字段值小数位                       │
│ ✅ 字段长度限制                       │
│                                      │
│ ── 拓扑检查 (8项, 7通过, 1未通过) ──   │
│ ✅ 空几何                            │
│ ✅ 自相交                            │
│ ✅ 极大/极小面                       │
│ ✅ 微短线                            │
│ ❌ 面重叠 (3对)                       │
│ ✅ 面裂隙                            │
│ ✅ 环方向                            │
│ ✅ 多部件几何                        │
│                                      │
│ [导出 HTML] [导出 Excel] [导出 GeoJSON]│
└──────────────────────────────────────┘
```

---

## 八、实施计划

### Phase 1: 数据模型层 (DB Migration)
1. `StoredFile` 加 `standard_id` + `standard_table_id` 字段
2. `QualityReport` 加五维评分 + summary 字段
3. Alembic 迁移脚本

### Phase 2: 标准数据加载器
4. 实现 `_load_standard_tables()` — 从 v2.2 标准加载完整定义（含字典值域）
5. 实现 layer_name ↔ table_name 映射逻辑

### Phase 3: 属性检查引擎
6. 新建 `services/quality/attribute_checker.py` — 9 项检查
7. 向量化实现（pandas）
8. 结果写入 `quality_violations` 表

### Phase 4: 拓扑检查引擎
9. 重写 `services/topology_checker.py` — 扩展到 12 项检查
10. PostGIS SQL 优先策略
11. 可配置容差
12. 结果写入 `quality_violations` 表

### Phase 5: 质检编排器
13. 重写 `services/quality_checker.py` — 串联 Phase 0→1→2→3
14. 五维评分计算
15. 报告生成（复用 reporter.py + exporter.py）

### Phase 6: API 层
16. 重构 `/quality/check` 和 `/data/{id}/quality-check`
17. 新增 `/data/{id}/bind-standard`
18. 扩展报告详情响应

### Phase 7: 前端
19. 质检配置面板重构（标准→表联动、检查项配置、容差输入）
20. 质检结果展示重构（分类展示、按字段分组）
21. 报告详情展开优化

---

## 九、文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **修改** | `models/stored_file.py` | 加 standard_id, standard_table_id |
| **修改** | `models/quality_report.py` | 加五维评分 + summary 字段 |
| **新建** | `services/quality/attribute_checker.py` | 属性检查引擎（9 项） |
| **重写** | `services/topology_checker.py` | 拓扑检查扩展到 12 项 |
| **重写** | `services/quality_checker.py` | 编排器：Phase 0→1→2→3 |
| **修改** | `services/quality/models.py` | 扩展 QualityResult 数据结构 |
| **修改** | `services/quality/reporter.py` | 适配新结果结构 |
| **修改** | `services/quality_exporter.py` | 适配 QualityViolation |
| **修改** | `api/v1/quality.py` | 重构质检端点 |
| **修改** | `api/v1/data.py` | 重构 stored-file 质检端点 |
| **修改** | `schemas/quality.py` | 新增请求/响应 Schema |
| **修改** | `app/frontend/index.html` | 质检配置 + 结果展示重构 |
| **废弃** | `services/quality/rule_generator.py` | 由 attribute_checker 取代 |
| **废弃** | `services/quality/rule_engine.py` | 由 attribute_checker 取代 |
| **废弃** | `services/validation_pipeline.py` | 由新的编排器取代 |
| **废弃** | `core/db_utils.py` 的 `load_standard_fields` | 由 `_load_standard_tables` 取代 |

---

## 十、兼容性考虑

1. **旧报告兼容**：`QualityReport` 新增字段全部有默认值，旧数据不受影响
2. **旧 API 兼容**：`/quality/check` 的 `standard_fields` 参数标记为 deprecated，保留但不再使用
3. **渐进迁移**：旧的 `rule_engine.py` / `rule_generator.py` 标记为 deprecated，不删除
4. **前端向后兼容**：未选择标准时退化为仅拓扑检查（与现有行为一致）
