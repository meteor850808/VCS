# 环境问题记录：数据库 Schema 与代码版本差异

## 状态
- **日期**: 2026-07-31
- **类型**: 已知环境差异（开发库）+ 已修复代码缺陷
- **影响**: 本地 PostgreSQL 开发库 `vcs_db`

---

## 1. Alembic 版本落后于代码

### 现象
- `alembic_version` 停留在 **`0004_add_key_group`**
- 代码已是 v2.2+（含 `standard_layer_groups` / `standard_tables` / `standard_fields` / `standard_dictionaries` 等 v2 模型与路由）
- 数据库实际已存在 v2 相关表（`standard_*`），说明历史上存在**手动 DDL**，但 alembic 版本号未跟进

### 已暴露的实际故障
`POST /api/v1/data-standards-v2` 返回 500：
- `data_standards` 表存在 `sheet_count` 列（`NOT NULL`，无默认值），而代码模型 `DataStandard` **不包含**该列
- ORM INSERT 不含 `sheet_count` → PostgreSQL 报 `NOT NULL violation` → 500

### 已采取的临时修复
```sql
ALTER TABLE data_standards ALTER COLUMN sheet_count SET DEFAULT 0;
```
（记录于 `scripts/` 之外，直接对开发库执行）

### 建议行动（后续处理）
1. 在干净/备份环境执行 `alembic upgrade head` 前，先 `pg_dump` 备份
2. 跑迁移后用 `alembic check` 校验模型与库一致
3. 评估历史手动 DDL 与迁移脚本的冲突（重点：`data_standards.sheet_count` 的来源，若迁移脚本会重建该列，需与代码模型对齐）
4. CI 中增加 `alembic check` 步骤，防止再次漂移

---

## 2. AuthContext 超级管理员数据可见性缺陷（代码 Bug，已修复）

### 现象
- 使用 `ADMIN_INIT_KEY`（`settings.admin_init_key`）鉴权时，`GET /api/v1/data-standards-v2` 返回空列表
- 使用超级管理员创建数据标准后，列表/详情接口看不到该标准；重名创建时 500（唯一约束）

### 根因
- `AuthContext.create_super_admin` 使用**虚拟 ApiKey id** `00000000-0000-0000-0000-000000000001` 标识管理员操作
- 而 `scope_clause()` / `owns()` 统一用 `api_key_id IN (accessible_key_ids)` 过滤，`accessible_key_ids` 只包含**真实存在的活跃 Key id**
- 结果：超级管理员创建的数据（`api_key_id = 虚拟 id`）被自身查询过滤掉

### 修复
`app/core/auth_context.py`：
- `AuthContext.__init__` 新增 `is_super_admin` 标记
- `scope_clause()`：超级管理员返回 `True`（不过滤，可访问所有数据）
- `owns()`：超级管理员返回 `True`
- `create_super_admin()`：传入 `is_super_admin=True`

### 影响面
- 所有依赖 `scope_clause()` / `owns()` 的路由（data / quality / quality_rules / data_standard_v2 / source_data / spatial / convert / batch）统一受益
- 超级管理员现在能看到：全部 Key 的数据 + 自身虚拟 ID 创建的数据 + `api_key_id IS NULL` 的记录（符合"管理员可查所有"语义）

---

## 3. 相关验证记录（2026-07-31 QC 页面 E2E）

- 修复后：`GET /api/v1/data-standards-v2?page_size=50` 返回 15 条（含此前 E2E 创建的标准）→ 全部可见
- E2E 浏览器验证（Playwright / Chrome）全流程通过后，已清理 E2E 测试数据：
  - 删除 4 条 `ul3.json` 质检报告（`207d6245` / `ebbc3a05` / `038dc5a8` / `3cc8df17`，级联清理 rule_results 与 violations）
  - 删除 E2E 测试标准 `c646a993-a76f-4490-b612-ca8c2bfe3dcf`
- 当前数据库：46 个文件 / 14 条标准 / 72 条报告（历史数据保留）
