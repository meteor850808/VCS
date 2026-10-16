# VCS v2.2 数据标准功能技术方案

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        前端 (index.html)                           │
│  三栏布局: 标准列表 | 层组树+表字段 | 字典+版本                       │
│  导入弹窗: JSON / Excel / CSV + 模板下载                            │
└───────────────┬───────────────────────────────────┬─────────────────┘
                │ REST API                          │
┌───────────────▼───────────────────────────────────▼─────────────────┐
│                    FastAPI Router (45 端点)                         │
│  data_standard_v2.py                                               │
│  ├── 标准 CRUD      (5)                                            │
│  ├── 层组管理        (6)                                            │
│  ├── 数据表管理      (5)                                            │
│  ├── 字段管理        (5)                                            │
│  ├── 字典管理       (10)                                            │
│  ├── 字段-字典关联   (4)                                            │
│  ├── 版本管理        (3)                                            │
│  └── 多格式导入      (7)                                            │
└───────────────┬───────────────────────────────────┬─────────────────┘
                │ SQLAlchemy 2.0 Async               │
┌───────────────▼───────────────────────────────────▼─────────────────┐
│                      数据库模型层                                    │
│  DataStandard ─┬─ StandardLayerGroup (嵌套, 自引用FK)               │
│                ├─ StandardTable ──── StandardField                  │
│                ├─ StandardDictionary ── DictionaryItem              │
│                ├─ StandardFieldDictLink                             │
│                └─ StandardVersion                                   │
└───────────────┬─────────────────────────────────────────────────────┘
                │ Alembic Migration
┌───────────────▼─────────────────────────────────────────────────────┐
│  PostgreSQL 15 + PostGIS 3.4                                       │
│  0001_baseline_v2_1 → 0002_data_standard_v2_2                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 数据模型设计

### 核心实体关系

```
DataStandard (1) ──┬── (N) StandardLayerGroup (嵌套, parent_id → self)
                   ├── (N) StandardTable ──── (N) StandardField
                   ├── (N) StandardDictionary ── (N) DictionaryItem
                   └── (N) StandardVersion

StandardField (N) ──┬── (N) StandardFieldDictLink ── (N) StandardDictionary
                    └── (1) QualityRule (FK: standard_field_id)
```

### 层组嵌套设计

```sql
standard_layer_groups:
  id            VARCHAR(36) PK
  standard_id   VARCHAR(36) FK → data_standards.id
  parent_id     VARCHAR(36) FK → standard_layer_groups.id  -- 自引用
  group_name    VARCHAR(256)
  group_code    VARCHAR(64)
  ordering      INTEGER
  created_at    TIMESTAMP
  updated_at    TIMESTAMP

  UNIQUE (standard_id, parent_id, group_name)
  UNIQUE (standard_id, parent_id, group_code)
```

**嵌套深度限制**: 应用层限制最大 5 层，通过 `StandardLayerGroup.get_depth()` 递归计算。

### 字典作用域

```
scope = 'standard'  → 仅绑定到特定数据标准
scope = 'global'    → 全局共享，可被多个标准的字段引用
```

### 字段-字典关联

```sql
standard_field_dict_links:
  id            VARCHAR(36) PK
  field_id      VARCHAR(36) FK → standard_fields.id
  dict_id       VARCHAR(36) FK → standard_dictionaries.id
  value_mode    VARCHAR(20)  -- 'strict' | 'suggest'
  created_at    TIMESTAMP

  UNIQUE (field_id, dict_id)
```

---

## API 设计

### 认证方式

所有端点使用 `X-API-Key` Header 认证，通过 SHA-256 hash 查库验证。

### 分页参数

列表端点统一支持:
- `page` (默认 1)
- `page_size` (默认 20, 最大 100)
- `search` (模糊搜索)

### 错误响应

```json
{
  "detail": "错误描述",
  "error_code": "NOT_FOUND"
}
```

### 标准创建示例

```json
POST /api/v1/data-standards-v2
{
  "name": "城市基础地理信息数据标准",
  "category": "industry",
  "description": "城市规划基础地理信息数据标准"
}
```

### JSON 导入示例

```json
POST /api/v1/data-standards-v2/import/json
{
  "name": "控制点标准",
  "category": "national",
  "layer_groups": [
    {
      "group_name": "基础地理要素",
      "group_code": "BG",
      "tables": [
        {
          "table_name": "控制点",
          "table_code": "CTRL_PT",
          "table_type": "spatial",
          "geometry_type": "Point",
          "fields": [
            {"field_name": "pt_id", "data_type": "string", "length": 20, "is_primary_key": true},
            {"field_name": "pt_name", "data_type": "string", "length": 50}
          ]
        }
      ],
      "children": []
    }
  ],
  "dictionaries": [
    {
      "dict_name": "控制点类型",
      "dict_code": "CTRL_TYPE",
      "scope": "standard",
      "items": [
        {"item_code": "GPS", "item_name": "GPS控制点"},
        {"item_code": "TRI", "item_name": "三角点"}
      ]
    }
  ]
}
```

### 版本发布示例

```json
POST /api/v1/data-standards-v2/{id}/publish
{
  "snapshot_summary": "发布初始版本，包含 3 个层组、12 个数据表"
}
```

---

## 前端设计

### 三栏布局

```
┌──────────────────────────────────────────────────────────────────────┐
│ Toolbar: [新建标准] [导入] [模板下载]                    共 N 个标准  │
├──────────────────┬───────────────────────┬──────────────────────────┤
│ 标准列表          │ 层组树 / 数据表详情    │ 字典管理 / 版本历史       │
│                  │                       │                          │
│ ┌──────────────┐ │ ┌───────────────────┐ │ ┌──────────────────────┐ │
│ │ 标准A        │ │ │ 📁基础地理要素    │ │ │ 📖 控制点类型    共享 │ │
│ │ 标准B        │ │ │  ├─ 📋 控制点      │ │ │  GPS控制点           │ │
│ │ ...          │ │ │  └─ 📋 水准点      │ │ │  三角点              │ │
│ └──────────────┘ │ └───────────────────┘ │ └──────────────────────┘ │
│                  │                       │ ┌──────────────────────┐ │
│                  │ 🗂 数据表             │ │ 📜 版本历史           │ │
│                  │ ┌───────────────────┐ │ │ v1.0 2024-01-01      │ │
│                  │ │ 控制点            │ │ │ v1.1 2024-01-15      │ │
│                  │ │ 水准点            │ │ │ [回滚]               │ │
│                  │ └───────────────────┘ │ └──────────────────────┘ │
│                  │                       │                          │
│                  │ 📝 字段定义           │                          │
│                  │ ┌───────────────────┐ │                          │
│                  │ │ pt_id  string 20  │ │                          │
│                  │ │ pt_name string 50 │ │                          │
│                  │ └───────────────────┘ │                          │
└──────────────────┴───────────────────────┴──────────────────────────┘
```

### 导入弹窗

支持三种导入方式:
1. **JSON**: 粘贴 JSON 数据 + 填写标准名称
2. **Excel**: 上传 .xlsx 文件
3. **CSV**: 多选 .csv 文件（每个文件对应一个数据表）

---

## 数据迁移策略

### 迁移脚本

`scripts/migrate_data_standard_v2_2.py`:
1. 创建新的标准记录（从旧 `data_standards` + `source_tables` 迁移）
2. 创建层组结构
3. 迁移字段定义
4. 迁移字典数据
5. 更新外键引用

### 回滚策略

Alembic 迁移包含 `downgrade()` 函数，可回滚到 v2.1 状态。

---

## 性能考虑

1. **层组树查询**: 使用 CTE (Common Table Expression) 递归查询，避免 N+1
2. **字典缓存**: 全局字典可缓存到 Redis，修改时失效
3. **批量导入**: 使用事务批量插入，减少数据库往返
4. **版本快照**: 使用 JSONB 存储快照，避免大表 JOIN

---

## 安全考虑

1. **租户隔离**: 所有查询通过 `api_key_id` 过滤
2. **输入验证**: 所有字段在 Schema 层验证
3. **SQL 注入防护**: 使用 SQLAlchemy ORM 参数化查询
4. **文件上传限制**: 导入文件大小限制 10MB

---

## Bug 修复记录 (2026-07-17)

### Bug Fix 1: DELETE `/field-dict-links/{link_id}` 返回 500 并杀死服务器进程

**问题描述**: 删除字段-字典关联时返回 500 Internal Server Error，且会杀死 uvicorn worker 进程。

**根因**: `source_data.py`（旧 API）和 `data_standard_v2.py`（新 API）都注册了 `DELETE /field-dict-links/{link_id}` 路由。`router.py` 中 `source_data_router` 先注册，FastAPI 路由匹配先到先得，请求被旧路由拦截。旧路由使用 `db.get(SourceFieldDictLink)` 查询不存在的 `field_dict_links` 表（正确表名：`standard_field_dict_links`），导致 `UndefinedTableError`。

**修复**:
1. `router.py`: `data_standard_v2_router` 注册顺序提前到 `source_data_router` 之前
2. `data_standard_v2.py`: DELETE 端点改用原生 SQL `text("DELETE FROM standard_field_dict_links WHERE id = :lid")`，彻底绕过 ORM identity map 和 cascade 冲突

**教训**: 当多个路由模块定义同名路径时，注册顺序决定匹配结果。新路由必须在旧路由之前注册。

### Bug Fix 2: `dict_name` 嵌套序列化始终为空

**问题描述**: `FieldResponse.dict_links[].dict_name` 始终返回 `null`/空字符串。

**根因**: `FieldResponse` 使用 `from_attributes=True`，但 Pydantic 不会穿透 ORM relationship 读取嵌套属性。`StandardFieldDictLink` ORM 模型没有 `dict_name` 属性（`dict_name` 在 `StandardDictionary` 上）。

**修复**: 在 `StandardFieldDictLink` 模型上添加 `@property dict_name`，通过已 selectin 加载的 `dictionary` 关系安全获取字典名称（带 try/except 保护）。
