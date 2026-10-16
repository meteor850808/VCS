# 数据标准存储重构方案

## 现状问题

当前 `data_standards` 模型仅将 Excel 解析为 `Standard → Sheet → Field` 三层，存在以下问题：

| 问题 | 表现 | 影响 |
|------|------|------|
| 无源表概念 | Sheet 只对应 Excel 工作表，不映射到业务表 | 无法区分"建筑物""宗地""道路"等不同图层标准 |
| 字典未独立 | 枚举值/允许值内嵌在 Field 的 JSON 字段 | 跨字段共用字典时重复存储，无法统一管理 |
| Excel 存磁盘 | 原始文件在 `data/uploads/data_standards/` 下 | 文件 I/O 慢，无法查询历史版本 |
| 无版本管理 | 更新标准直接覆盖 | 无法追溯变更历史 |

## 目标设计

```
源数据表 (SourceTable)      ← 业务图层/实体
  ├── 源数据字段 (SourceField)  ← 字段定义
  │     └── 关联字典 (FieldDictionaryLink)
  │           └── 源数据字典 (SourceDictionary)  ← 码值对照表
  └── 标准版本 (StandardVersion)  ← 变更记录
```

---

## 数据库设计

### 1. 源数据表 (source_tables)

对应业务中的实体/图层，如建筑物图层、宗地图层。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| api_key_id | VARCHAR(36) FK | 所属用户 |
| standard_id | VARCHAR(36) FK | 所属标准（可空，关联旧版 DataStandard） |
| table_name | VARCHAR(256) NOT NULL | 表名（业务名称，如 "建筑物图层"） |
| table_code | VARCHAR(64) | 表编码（如 "JZWW"、"ZDTB"） |
| description | TEXT | 表描述 |
| geometry_type | VARCHAR(32) | 几何类型（Point/Polygon/None=属性表） |
| source | VARCHAR(64) | 数据来源（如 "国标"、"行标"、"自定义"） |
| version | VARCHAR(32) | 版本号（如 "GB/T 13923-2022"） |
| is_active | BOOLEAN | 是否启用 |
| ordering | INTEGER | 排序号 |
| created_at | TIMESTAMPTZ | |

### 2. 源数据字段 (source_fields)

每个表的字段定义，独立于 Excel 工作表概念。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| table_id | VARCHAR(36) FK | 所属表 |
| field_name | VARCHAR(256) NOT NULL | 字段名（如 "YWBH"） |
| field_alias | VARCHAR(256) | 字段别名（如 "业务编号"） |
| field_type | VARCHAR(32) | 字段类型 |
| data_type | VARCHAR(32) | 数据类型（string/integer/float/date/geometry） |
| length | INTEGER | 最大长度 |
| precision | INTEGER | 小数精度 |
| nullable | BOOLEAN | 是否可空 |
| default_value | VARCHAR(256) | 默认值 |
| description | TEXT | 字段描述 |
| is_primary_key | BOOLEAN | 是否主键 |
| is_required | BOOLEAN | 是否必填 |
| ordering | INTEGER | 排序号 |
| created_at | TIMESTAMPTZ | |

### 3. 源数据字典 (source_dictionaries)

码值对照表，独立管理，可被多个字段引用。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| api_key_id | VARCHAR(36) FK | 所属用户 |
| dict_name | VARCHAR(256) NOT NULL | 字典名 |
| dict_code | VARCHAR(64) | 字典编码 |
| description | TEXT | 字典描述 |
| is_system | BOOLEAN | 是否系统内置 |
| created_at | TIMESTAMPTZ | |

### 4. 字典条目 (dictionary_items)

每个字典包含的码值条目。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| dict_id | VARCHAR(36) FK | 所属字典 |
| item_code | VARCHAR(64) NOT NULL | 码值（如 "100101"） |
| item_name | VARCHAR(256) NOT NULL | 码值名称（如 "一类居住用地"） |
| parent_code | VARCHAR(64) | 父级码值（层级结构用） |
| description | TEXT | 说明 |
| ordering | INTEGER | 排序号 |

### 5. 字段字典关联 (field_dict_links)

字段与字典的多对多关联，带筛选条件。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| field_id | VARCHAR(36) FK | 字段 |
| dict_id | VARCHAR(36) FK | 字典 |
| filter_condition | VARCHAR(256) | 筛选条件（如 "parent_code=1001"） |
| ordering | INTEGER | |

### 6. 标准版本 (standard_versions)

每次上传/更新标准的变更记录。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) PK | UUID |
| standard_id | VARCHAR(36) FK | 关联旧版 DataStandard |
| api_key_id | VARCHAR(36) FK | |
| version_label | VARCHAR(32) | 版本标签 |
| change_log | TEXT | 变更说明 |
| file_data | BYTEA | Excel 文件二进制 |
| file_name | VARCHAR(512) | 文件名 |
| created_at | TIMESTAMPTZ | |

---

## 表关系图

```
DataStandard (现有，保留兼容)
    │
    └──< StandardVersion (新建)
          │
source_tables (新建) ────< source_fields (新建)
    │                            │
    │                            └──< field_dict_links (新建)
    │                                       │
    │                                       └──< source_dictionaries (新建)
    │                                                │
    │                                                └──< dictionary_items (新建)
    │
    └── 关联到 DataStandard.id（多表可属同一标准）
```

---

## 实施步骤

### Step 1: 创建新模型
- `SourceTable` — 源数据表
- `SourceField` — 源数据字段
- `SourceDictionary` — 源数据字典
- `DictionaryItem` — 字典条目
- `FieldDictLink` — 字段字典关联
- `StandardVersion` — 标准版本

### Step 2: 更新导入流程
```
Excel 上传 → parse_standard_excel → 创建/更新 StandardVersion
  → 解析 Table 定义 → 写入 SourceTable
  → 解析 Field 定义 → 写入 SourceField
  → 解析 Dictionary 定义 → 写入 SourceDictionary + DictionaryItem
  → 建立 Field-Dict 关联 → 写入 FieldDictLink
```

### Step 3: 新增 API
- `GET /source-tables` — 源表列表
- `GET /source-tables/{id}` — 源表详情（含字段和字典）
- `POST /source-tables` — 手动创建源表
- `PUT /source-tables/{id}` — 更新源表
- `DELETE /source-tables/{id}` — 删除源表
- `GET /source-dictionaries` — 字典列表
- `POST /source-dictionaries` — 创建字典
- `POST /source-dictionaries/{id}/items` — 批量添加字典条目
- `PUT /dictionary-items/{id}` — 更新条目

### Step 4: 迁移现有数据
将当前 `data_standard_fields` 中的数据迁移到 `source_fields`。
