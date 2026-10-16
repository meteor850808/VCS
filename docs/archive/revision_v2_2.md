# VCS v2.2 数据标准功能升级修订记录

## 版本信息
- **版本**: v2.2
- **日期**: 2026-07-15
- **类型**: 功能升级 + 数据库扩展
- **影响范围**: 数据标准模块（ORM 模型、API 端点、前端页面、数据迁移）

---

## 修订目标

1. **合并双轨模型**: 将 `DataStandard` + `SourceTable` 合并为统一的标准层级体系
2. **嵌套层组**: 支持最多 5 层的树形层组结构
3. **共享字典**: 字典支持 `standard` / `global` 两种作用域，全局字典可跨标准共享
4. **多格式导入**: 支持 JSON / Excel / CSV 三种格式导入数据标准
5. **版本管理**: 支持发布版本快照和版本对比
6. **模板下载**: 提供 JSON / Excel / CSV 三种格式的导入模板
7. **压缩包上传下载**: 支持 ZIP 压缩包方式导入/导出

---

## 详细变更

### 1. 新增 ORM 模型（6 个）

| 模型 | 表名 | 说明 |
|------|------|------|
| `StandardLayerGroup` | `standard_layer_groups` | 嵌套层组（自引用 FK `parent_id`，最大 5 层） |
| `StandardTable` | `standard_tables` | 标准数据表（spatial/attribute/relationship） |
| `StandardField` | `standard_fields` | 标准字段（合并原 DataStandardField + SourceField） |
| `StandardDictionary` | `standard_dictionaries` | 标准字典（scope: standard/global） |
| `DictionaryItem` | `dictionary_items` | 字典条目（层级结构，parent_code） |
| `StandardFieldDictLink` | `standard_field_dict_links` | 字段-字典关联（值域控制） |

### 2. 修改 ORM 模型

#### 2.1 DataStandard
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `category` | 新增 | national/industry/custom |
| `status` | 新增 | draft/published/archived |
| `version` | 新增 | 整数版本号 |
| `layer_groups` | 新增关系 | → StandardLayerGroup |
| `standard_tables` | 新增关系 | → StandardTable |
| `standard_fields` | 新增关系 | → StandardField |
| `standard_dictionaries` | 新增关系 | → StandardDictionary |
| `versions` | 新增关系 | → StandardVersion |

#### 2.2 QualityRule
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `standard_field_id` | FK 更新 | 指向 `standard_fields.id` |

### 3. 新增 API 端点（45 个）

#### 3.1 数据标准 CRUD（5 端点）
- `GET /data-standards-v2` — 列表查询
- `POST /data-standards-v2` — 创建标准
- `GET /data-standards-v2/{id}` — 详情（含层组/表/字段/字典）
- `PUT /data-standards-v2/{id}` — 更新标准
- `DELETE /data-standards-v2/{id}` — 删除标准

#### 3.2 层组管理（6 端点）
- `GET /data-standards-v2/{id}/layers` — 获取层组树
- `POST /data-standards-v2/{id}/layers` — 创建层组
- `PUT /layers/{id}` — 更新层组
- `DELETE /layers/{id}` — 删除层组
- `POST /layers/{id}/move` — 移动层组
- `POST /layers/{id}/copy` — 复制层组

#### 3.3 数据表管理（5 端点）
- `GET /layers/{id}/tables` — 层组下的表
- `POST /layers/{id}/tables` — 创建表
- `GET /tables/{id}` — 表详情（含字段）
- `PUT /tables/{id}` — 更新表
- `DELETE /tables/{id}` — 删除表

#### 3.4 字段管理（5 端点）
- `GET /tables/{id}/fields` — 表字段列表
- `POST /tables/{id}/fields` — 创建字段
- `PUT /fields/{id}` — 更新字段
- `DELETE /fields/{id}` — 删除字段
- `POST /tables/{id}/fields/reorder` — 字段排序

#### 3.5 数据字典管理（10 端点）
- `GET /data-standards-v2/{id}/dictionaries` — 标准字典列表
- `POST /data-standards-v2/{id}/dictionaries` — 创建字典
- `GET /dictionaries` — 全局字典查询
- `GET /dictionaries/{id}` — 字典详情
- `PUT /dictionaries/{id}` — 更新字典
- `DELETE /dictionaries/{id}` — 删除字典
- `POST /dictionaries/{id}/items` — 添加条目
- `PUT /dictionaries/{id}/items` — 更新条目
- `DELETE /dictionaries/{id}/items/{item_id}` — 删除条目
- `POST /dictionaries/{id}/share` — 设为全局共享

#### 3.6 字段-字典关联（4 端点）
- `GET /fields/{id}/dict-links` — 字段关联列表
- `POST /fields/{id}/dict-links` — 创建关联
- `PUT /field-dict-links/{id}` — 更新关联
- `DELETE /field-dict-links/{id}` — 删除关联

#### 3.7 版本管理（3 端点）
- `POST /data-standards-v2/{id}/publish` — 发布版本
- `GET /data-standards-v2/{id}/versions` — 版本历史
- `POST /data-standards-v2/{id}/rollback/{version}` — 版本回滚

#### 3.8 多格式导入（7 端点）
- `POST /data-standards-v2/import/json` — JSON 导入
- `POST /data-standards-v2/import/excel` — Excel 导入
- `POST /data-standards-v2/import/csv` — CSV 导入
- `GET /data-standards-v2/template/json` — JSON 模板下载
- `GET /data-standards-v2/template/excel` — Excel 模板下载
- `GET /data-standards-v2/template/csv` — CSV 模板下载（ZIP）
- `POST /data-standards-v2/{id}/export` — 导出标准

### 4. 数据库迁移

- **迁移脚本**: `alembic/versions/0002_data_standard_v2_2.py`
- **数据迁移**: `scripts/migrate_data_standard_v2_2.py`
- **依赖**: `0001_baseline_v2_1`

### 5. 前端变更

- **标准管理页面升级**: 三栏布局（标准列表 + 层组树 / 表字段详情 / 字典版本）
- **多格式导入弹窗**: JSON / Excel / CSV 三种导入方式
- **模板下载**: 三种格式模板一键下载
- **新增 API 辅助函数**: `PUT()` 方法

### 6. 部署脚本变更

- **init_db.py**: 添加新模型导入，确保 `create_all()` 创建新表

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `app/models/standard_layer_group.py` | 层组模型 |
| `app/models/standard_table.py` | 数据表模型 |
| `app/models/standard_field.py` | 字段模型 |
| `app/models/standard_dictionary.py` | 字典模型 |
| `app/models/dictionary_item.py` | 字典条目模型 |
| `app/models/standard_field_dict_link.py` | 字段-字典关联模型 |
| `app/schemas/data_standard_v2.py` | Pydantic Schema |
| `app/api/v1/data_standard_v2.py` | API 端点 |
| `scripts/migrate_data_standard_v2_2.py` | 数据迁移脚本 |
| `alembic/versions/0002_data_standard_v2_2.py` | Alembic 迁移 |
| `docs/revision_v2_2.md` | 本文档 |
| `docs/technical_spec_v2_2.md` | 技术方案 |
| `docs/test_manual_v2_2.md` | 测试手册 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/models/data_standard.py` | 添加 category/status/version + 新关系 |
| `app/models/quality_rule.py` | FK 指向新 standard_fields |
| `app/models/source_data.py` | 移除旧 DictionaryItem |
| `app/models/__init__.py` | 添加新模型导入 |
| `app/api/v1/router.py` | 注册 data_standard_v2_router |
| `alembic/env.py` | 添加新模型导入 |
| `scripts/init_db.py` | 添加新模型导入 |
| `app/frontend/index.html` | 标准管理页面升级 |
