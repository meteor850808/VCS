# VCS v2.5 分组权限 + 超级管理员 + 硬删除修订记录

## 版本信息
- **版本**: v2.5
- **日期**: 2026-07-29
- **类型**: 权限体系升级 + 管理功能完善
- **影响范围**: 认证注入、路由查询、模型/Schema、前端管理 UI、迁移脚本

---

## 修订目标

1. **Group 分组隔离**: API Key 可归入分组，同组 Key 共享数据访问权限——实现团队级数据协作
2. **超级管理员鉴权**: `ADMIN_INIT_KEY` 可作为 `X-API-Key` 查询所有数据，方便运维
3. **API Key 硬删除**: 在现有「吊销（软删除）」基础上增加「永久删除」功能，彻底清理已废弃的 Key
4. **Group 管理前端**: 分组列表 + 成员卡片 + 创建/编辑/删除分组 + Key 分组指派
5. **管理员前端 Bug 修复**: 修复 `fmt`/`esc` 函数重复声明导致 admin 页 JS 崩溃的问题

---

## 详细变更

### 1. Group 分组权限模型

#### 1.1 新增模型 `KeyGroup`
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID PK | 分组唯一标识 |
| `name` | VARCHAR(128) UNIQUE | 分组名称 |
| `description` | TEXT nullable | 分组描述 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

**文件**: `app/models/key_group.py`

#### 1.2 ApiKey 模型扩展
| 字段 | 类型 | 说明 |
|------|------|------|
| `group_id` | FK → key_groups.id, ondelete=SET NULL | 所属分组 |
| `group` | relationship → KeyGroup | ORM 关联 |

**文件**: `app/models/api_key.py`

#### 1.3 迁移 `0004_add_key_group.py`
```sql
CREATE TABLE key_groups (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE api_keys ADD COLUMN group_id VARCHAR(36)
    REFERENCES key_groups(id) ON DELETE SET NULL;
CREATE INDEX ix_api_keys_group_id ON api_keys(group_id);
```

### 2. AuthContext 认证上下文

#### 2.1 新增 `AuthContext` 类
- **`accessible_key_ids`**: 当前 Key 可访问数据所属的 api_key_id 列表
  - 无 group: `[self.id]`（仅自己，兼容旧行为）
  - 有 group: 同组所有活跃 Key 的 ID 列表
- **`scope_clause(model)`**: 生成 SQL 过滤条件 `model.api_key_id.in_(accessible_key_ids)`
- **`owns(record)`**: 权属检查（`record.api_key_id in accessible_key_ids`）
- **`__getattr__` 委托**: 向后兼容 `auth.id` / `auth.name` 等属性直接访问
- **`create_super_admin(db)`**: 工厂方法——查询全部活跃 Key ID，创建超级管理员上下文

**文件**: `app/core/auth_context.py`

#### 2.2 `authenticated()` 依赖更新
- 返回类型从 `ApiKey` 改为 `AuthContext`
- 新增前置兜底：若 `X-API-Key` 值 === `settings.admin_init_key`，返回超级管理员 AuthContext
- 超级管理员 `accessible_key_ids` = 所有活跃 Key 的 ID → 可查看所有数据

**文件**: `app/core/deps.py`

### 3. 查询模式迁移

#### 3.1 批量替换（68 处，覆盖 9 个路由文件）
| 文件 | 替换内容 |
|------|----------|
| `app/api/v1/data.py` | `api_key.id` → `api_key.scope_clause()` + `api_key.owns()` |
| `app/api/v1/quality.py` | 同上 |
| `app/api/v1/quality_rules.py` | 同上 |
| `app/api/v1/spatial.py` | 同上 |
| `app/api/v1/data_standards.py` | 同上 |
| `app/api/v1/data_standard_v2.py` | 同上 |
| `app/api/v1/convert.py` | 同上 |
| `app/api/v1/batch.py` | 同上 |
| `app/api/v1/source_data.py` | 同上 |

**迁移脚本**: `scripts/migrate_auth_patterns.py`, `scripts/fix_auth_varname.py`

#### 3.2 替换模式
| 旧模式 | 新模式 |
|--------|--------|
| `select(X).where(X.api_key_id == api_key.id)` | `select(X).where(api_key.scope_clause(X))` |
| `sf and sf.api_key_id != api_key.id` | `not sf or not api_key.owns(sf)` |
| `not std or std.api_key_id != api_key.id` | `not std or not api_key.owns(std)` |

### 4. API Endpoint 扩展

#### 4.1 Group CRUD（Admin 接口）
| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/admin/groups` | 创建分组（名称唯一性校验） |
| `GET` | `/admin/groups` | 列出所有分组（含成员数统计） |
| `GET` | `/admin/groups/{id}` | 分组详情（含成员列表） |
| `PUT` | `/admin/groups/{id}` | 更新分组 |
| `DELETE` | `/admin/groups/{id}` | 删除分组（组内 Key 自动置空 group_id） |

**文件**: `app/api/v1/admin.py`

#### 4.2 Key-Group 指派
| 方法 | 路径 | 说明 |
|------|------|------|
| `PUT` | `/admin/apikeys/{key_id}/group` | 设置 Key 所属分组（body.group_id=null 则移出） |

#### 4.3 API Key 硬删除
| 方法 | 路径 | 说明 |
|------|------|------|
| `DELETE` | `/admin/apikeys/{key_id}/hard` | 从数据库彻底移除（不可恢复） |

### 5. Schema 扩展

| Schema | 新增字段 | 说明 |
|--------|----------|------|
| `ApiKeyCreateRequest` | `group_id: str \| None` | 创建时可选指定分组 |
| `ApiKeyBrief` | `group_id, group_name` | 列表展示分组信息 |
| `KeyGroupBrief` | `id, name, description, member_count, created_at` | 分组列表响应 |
| `KeyGroupDetail` | `id, name, description, created_at, api_keys: list[ApiKeyBrief]` | 分组详情含成员 |
| `KeyGroupCreateRequest` | `name, description` | 创建分组请求 |
| `KeyGroupUpdateRequest` | `name \| None, description \| None` | 更新分组请求 |
| `SetKeyGroupRequest` | `group_id \| None` | 设置 Key 分组请求 |

**文件**: `app/schemas/admin.py`

### 6. 前端管理 UI 升级

#### 6.1 双 Tab 布局
- **API 密钥** 页签：Key 创建/列表/吊销/硬删除/分组指派
- **分组管理** 页签：Group CRUD + 成员卡片展示

#### 6.2 分组管理功能
- 分组列表：卡片式布局，显示名称、描述、成员数、创建时间
- 成员展示：分组内 Key 以标签形式排列（活跃/吊销状态彩色标识）
- 编辑分组：弹窗编辑名称/描述
- 删除分组：确认后删除，组内 Key 自动移出分组

#### 6.3 Key 列表增强
- 新增「分组」列，显示分组标签
- 创建 Key 时可选分组（下拉选择）
- 修改分组按钮（👥 图标），弹窗选择新分组
- 已吊销 Key 显示 ❌删除 按钮（两次确认防误操作）
- 颜色区分：活跃=绿底、吊销=红框

#### 6.4 Bug 修复
- **`esc` 函数重复声明**: `const esc`（line 1211）与 `function esc`（line 3508）冲突 → 删除 `function esc`，保留 `const esc`
- **`fmt` 重命名**: 原 `function fmt` 随删除，改名为 `admFmt` 避免与全局函数冲突
- 修复后 admin 页面 JS 零错误加载

### 7. 辅助修复

#### 7.1 `geojson_to_wkt()` Z 维度处理
- **问题**: `shapely.wkt.dumps()` 输出带 Z 维度的 WKT，PostGIS `ST_GeomFromText` 无法解析
- **修复**: 新增 `_force_2d()` 用 `shapely.ops.transform` 剥离 Z 坐标
- **文件**: `app/services/spatial_loader.py`

#### 7.2 前端 `restart.bat` 修复（两个文件）
- 移除 `chcp 65001`（UTF-8 编码切换导致 bat 字符损坏）
- 恢复端口 45679（曾被改为 45680）
- 简化 kill 逻辑，移除 `findstr` 尾部空格

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `app/models/key_group.py` | KeyGroup ORM 模型 |
| `app/core/auth_context.py` | AuthContext 认证上下文 |
| `alembic/versions/0004_add_key_group.py` | 添加 key_groups 表 + group_id 列 |
| `docs/revision_v2_5.md` | 本文档 |
| `scripts/migrate_auth_patterns.py` | 查询模式批量迁移脚本 |
| `scripts/fix_auth_varname.py` | auth→api_key 变量名修复脚本 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/models/api_key.py` | 新增 `group_id` FK + `group` relationship |
| `app/core/deps.py` | `authenticated()` 返回 AuthContext；admin init key 超级管理员兜底 |
| `app/api/v1/admin.py` | 新增 Group CRUD + Key-Group 指派 + 硬删除端点 |
| `app/schemas/admin.py` | 新增 KeyGroup 相关 Schema + ApiKey 扩展字段 |
| `app/frontend/index.html` | 分组管理双 Tab + 删除按钮 + esc/fmt Bug 修复 |
| `app/services/spatial_loader.py` | `_force_2d()` 剥离 Z 维度 |
| `app/api/v1/data.py` | 使用 `scope_clause` / `owns` 迁移查询模式 |
| `app/api/v1/quality.py` | 同上 |
| `app/api/v1/quality_rules.py` | 同上 |
| `app/api/v1/spatial.py` | 同上 |
| `app/api/v1/data_standards.py` | 同上 |
| `app/api/v1/data_standard_v2.py` | 同上 |
| `app/api/v1/convert.py` | 同上 |
| `app/api/v1/batch.py` | 同上 |
| `app/api/v1/source_data.py` | 同上 |
| `restart.bat` | 修复编码 + 端口 + kill 逻辑 |
| `.env` | `ADMIN_INIT_KEY` 更新为 `VCS-admin-2026-q6k4H9nZxR3` |

---

## 数据库迁移

### 0004_add_key_group.py
```sql
CREATE TABLE key_groups (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE api_keys
    ADD COLUMN group_id VARCHAR(36)
    REFERENCES key_groups(id) ON DELETE SET NULL;

CREATE INDEX ix_api_keys_group_id ON api_keys(group_id);
```

---

## E2E 验证

### Group 数据共享
```
1. key-owner (group G1) 上传文件 → 成功
2. key-sharer (group G1) 查看文件列表 → 看到同一文件 ✅
3. default (无 group) 查看文件列表 → 看到不同文件 ✅ （隔离）
```

### 超级管理员
```
X-API-Key: VCS-admin-2026-q6k4H9nZxR3
GET /api/v1/data → 返回全部 21 条文件记录 ✅
```

### 硬删除
```
1. 创建 key → 列表可见 ✅
2. 吊销 → is_active=false，列表仍可见 ✅
3. 永久删除 → 列表完全消失 ✅
4. 尝试再次访问 → 404 ✅
```
