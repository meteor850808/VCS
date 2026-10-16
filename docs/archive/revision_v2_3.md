# VCS v2.3 数据质检模块重建修订记录

## 版本信息
- **版本**: v2.3
- **日期**: 2026-07-21
- **类型**: 核心模块重建 + API 重构 + 前端升级
- **影响范围**: 质检引擎、编排器、API 端点、Schema、前端质检面板、数据库模型扩展

---

## 修订目标

1. **标准驱动质检**: 质检引擎直接读取 v2.2 数据标准定义（`StandardTable → StandardField → DictLink → DictionaryItem`），彻底替代旧版 `DataStandardField` 路径
2. **属性结构检查**: 实现 9 项检查（A1-A9），覆盖图层、类型、结构、必填、值域、唯一性、空值、小数位、长度
3. **拓扑检查扩展**: 从 6 项扩展到 13 项（T01-T13），混合 PostGIS SQL + shapely 策略
4. **五维评分模型**: 实现完整性/准确性/一致性/唯一性/拓扑五维加权评分
5. **可配置拓扑容差**: 8 项容差参数可通过 API 和前端配置
6. **标准→表级联**: 前端支持选择标准后自动加载其下标准表列表
7. **版本管理修复**: 修复 diff 500 错误、重复 version_number 查询问题

---

## 详细变更

### 1. 数据库模型扩展

#### 1.1 StoredFile 新增字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `standard_id` | FK → data_standards.id | 关联数据标准 |
| `standard_table_id` | FK → standard_tables.id | 关联标准表 |

#### 1.2 QualityReport 新增字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `standard_table_id` | FK → standard_tables.id | 关联标准表 |
| `attribute_summary` | JSON | 属性检查汇总 `{total, passed, failed, by_rule}` |
| `topology_summary` | JSON | 拓扑检查汇总 `{total, passed, failed, by_rule}` |
| `topology_config` | JSON | 拓扑检查配置快照 |
| `score_completeness` | Float | 必填项完整率评分 |
| `score_accuracy` | Float | 值域/类型正确率评分 |
| `score_consistency` | Float | 属性结构一致性评分 |
| `score_uniqueness` | Float | 主键唯一性评分 |
| `score_topology` | Float | 拓扑合规率评分 |

### 2. 新增/重写服务模块

#### 2.1 属性检查引擎（新建）
- **文件**: `app/services/quality/attribute_checker.py`
- **9 项检查规则**:

| 规则码 | 名称 | 检查内容 |
|--------|------|----------|
| 31010001 | 必要图层检查 | 数据中的图层名是否在标准定义中存在 |
| 31010002 | 要素类型正确性 | 几何类型是否与标准表定义匹配 |
| 31010003 | 属性结构正确性 | 字段集是否为标准字段集的超集 |
| 31010004 | 字段必填项 | `is_required=true` 的字段是否为空 |
| 31010005 | 属性值域控制 | 值是否在字典允许值范围内 |
| 31010006 | 字段唯一值 | 主键字段值是否唯一 |
| 31010007 | 必要图层不为空 | 对应图层是否包含要素 |
| 31010008 | 字段值小数位 | 数值精度是否超过标准定义 |
| 31010009 | 字段长度限制 | 字符串长度是否超过 `length` 限制 |

- **关键函数**:
  - `load_standard_tables(db, standard_id)` — 递归加载 v2.2 标准定义
  - `load_features_by_file(db, stored_file_id, api_key_id)` — 从 PostGIS 三表加载特征
  - `run_attribute_checks(db, standard_id, stored_file_id, api_key_id)` — 执行全部检查

#### 2.2 拓扑检查引擎（重写）
- **文件**: `app/services/topology_checker.py`
- **13 项检查规则**:

| 规则码 | 名称 | 几何类型 | 策略 |
|--------|------|----------|------|
| T01 | 空几何 | ALL | PostGIS SQL |
| T02 | 自相交 | Line/Polygon | shapely |
| T03 | 极大面 | Polygon | PostGIS SQL |
| T04 | 极小面 | Polygon | PostGIS SQL |
| T05 | 微短线 | Line | PostGIS SQL |
| T06 | 面重叠 | Polygon | PostGIS SQL + ST_Overlaps |
| T07 | 点重叠 | Point | PostGIS SQL |
| T08 | 线重叠 | Line | PostGIS SQL |
| T09 | 面裂隙 | Polygon | PostGIS SQL + ST_Touches |
| T10 | 线连通性 | Line | shapely |
| T11 | 不规则面 | Polygon | shapely 凸度分析 |
| T12 | 环方向 | Polygon | PostGIS SQL |
| T13 | 多部件几何 | ALL | PostGIS SQL |

- **可配置容差** (`TopologyConfig`):

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `min_area` | 0.0001 | 极小面阈值 |
| `max_area` | 1e9 | 极大面阈值 |
| `min_length` | 0.001 | 微短线阈值 |
| `overlap_tolerance` | 0.0001 | 重叠容差 |
| `gap_threshold` | 0.001 | 裂隙阈值 |
| `point_distance` | 0.001 | 点重叠距离 |
| `convexity_threshold` | 0.3 | 不规则面凸度 |
| `min_sides` | 3 | 最少边数 |

#### 2.3 质检编排器（重写）
- **文件**: `app/services/quality_checker.py`
- **五维评分权重**:

| 维度 | 权重 | 来源规则 |
|------|------|----------|
| completeness（完整性） | 25% | A4 必填项 |
| accuracy（准确性） | 25% | A5 值域控制 |
| consistency（一致性） | 20% | A1/A2/A3 层/类型/结构 |
| uniqueness（唯一性） | 15% | A6 主键唯一 |
| topology（拓扑） | 15% | T01-T13 拓扑检查 |

- **关键函数**:
  - `run_quality_check(db, standard_id, stored_file_id, ...)` — 异步编排入口
  - `compute_scores(attr_results, topo_results)` — 五维评分计算

### 3. API 端点变更

#### 3.1 新增端点
| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /data-standards-v2/{id}/tables` | GET | 标准下所有表（扁平列表，跨层组） |

#### 3.2 重写端点
| 端点 | 变更 |
|------|------|
| `POST /quality/check?file_id=` | v2 接口，返回五维评分 + 属性/拓扑检查明细 |
| `POST /quality/check/upload` | 上传文件并质检，优先使用 v2 引擎，失败回退旧引擎 |
| `POST /data/{file_id}/quality-check` | 重写使用新编排器，支持 `standard_table_id` |

#### 3.3 Schema 变更
| Schema | 变更 |
|--------|------|
| `QualityCheckRequest` | 新增请求体（standard_id, standard_table_id, geometry_type, topology_config） |
| `QualityCheckResponse` | 新增响应体（五维评分、检查明细、违规样本） |
| `DimensionScores` | 五维评分 Pydantic 模型 |
| `CheckItemResult` | 属性检查项结果 |
| `TopoCheckItemResult` | 拓扑检查项结果 |
| `CheckSummary` | 检查汇总（total/passed/failed/by_rule） |
| `QualityReportDetail` | 扩展五维评分字段 + 向后兼容旧字段 |

### 4. 前端变更

#### 4.1 质检配置面板
- 标准选择下拉加载 v2.2 标准（`/data-standards-v2`）
- 标准↔标准表级联下拉（选择标准后自动加载其下所有表）
- 13 项拓扑规则复选框（T01-T13）替代旧 6 项
- 折叠式容差参数面板（8 项可调参数）
- "对已入库文件质检" 按钮 + 文件选择

#### 4.2 质检结果展示
- 五维评分卡片（5 列网格，颜色编码百分比）
- 属性检查汇总表（规则码 / 名称 / 状态 / 失败数 / 说明）
- 拓扑检查汇总表
- 旧版报告格式向后兼容展示

#### 4.3 文件列表行质检
- 行内质检使用五维综合分（加权平均）替代旧单分
- 标准表选择传递

### 5. 版本管理 Bug 修复

#### 5.1 diff 端点 500 错误
- **根因**: `_deep_diff` 函数不支持 list 类型对比
- **修复**: 重写递归对比逻辑，支持 dict/list/None/标量

#### 5.2 重复 version_number 查询
- **根因**: `diff_versions` 和 `rollback_version` 查询未限制排序，多条记录导致歧义
- **修复**: 加 `.order_by(created_at.desc()).limit(1)` 确定唯一版本

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `app/services/quality/attribute_checker.py` | 属性检查引擎（9 项规则，向量化） |
| `docs/revision_v2_3.md` | 本文档 |
| `docs/technical_spec_v2_3.md` | 技术方案 |
| `docs/test_manual_v2_3.md` | 测试手册 |

### 重写文件
| 文件 | 变更 |
|------|------|
| `app/services/topology_checker.py` | 从 6 项扩展到 13 项，混合 SQL+shapely |
| `app/services/quality_checker.py` | 编排器重写，串联属性+拓扑+五维评分 |
| `app/schemas/quality.py` | 新增 v2 请求/响应模型 |
| `app/api/v1/quality.py` | 重写质检 API（v2 + 兼容） |
| `app/api/v1/data.py` | 重写 `/quality-check` 端点 |
| `app/frontend/index.html` | 质检配置面板 + 结果展示升级 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/models/stored_file.py` | 新增 standard_id, standard_table_id |
| `app/models/quality_report.py` | 新增五维评分 + summary 字段 |
| `app/api/v1/data_standard_v2.py` | 新增 GET `/{id}/tables` 端点 |

### 废弃文件（标记，未删除）
| 文件 | 替代 |
|------|------|
| `app/services/quality/rule_engine.py` | attribute_checker.py |
| `app/services/quality/rule_generator.py` | attribute_checker.py |
| `app/services/validation_pipeline.py` | quality_checker.py（新编排器） |
| `app/services/quality/scoring.py` | quality_checker.py（compute_scores） |

---

## 依赖变更

- **新增**: `pandas` — 属性检查向量化计算
- **已有**: `shapely` — 拓扑几何分析（T02/T10/T11）
- **已有**: `sqlalchemy` — PostGIS 空间查询

---

## 数据库迁移

新增字段通过 `ALTER TABLE` 添加（Alembic 迁移待执行）：
```sql
ALTER TABLE stored_files ADD COLUMN standard_id VARCHAR(36) REFERENCES data_standards(id) ON DELETE SET NULL;
ALTER TABLE stored_files ADD COLUMN standard_table_id VARCHAR(36) REFERENCES standard_tables(id) ON DELETE SET NULL;
ALTER TABLE quality_reports ADD COLUMN standard_table_id VARCHAR(36) REFERENCES standard_tables(id) ON DELETE SET NULL;
ALTER TABLE quality_reports ADD COLUMN attribute_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN topology_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN topology_config JSONB;
ALTER TABLE quality_reports ADD COLUMN score_completeness FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN score_accuracy FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN score_consistency FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN score_uniqueness FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN score_topology FLOAT DEFAULT 0;
```
