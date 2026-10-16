# VCS v2.9.1 修订记录 — scope_clause 全链路健康检查与 Bug 修复

## 版本信息
- **版本**: v2.9.1（在 v2.9.0 基础上）
- **日期**: 2026-08-17
- **类型**: 项目全链路连通性检查 + Bug 修复
- **影响范围**: `app/core/auth_context.py`
- **关联文档**: `docs/test_manual_v2_9.md`（新增回归用例 TC-A2）

---

## 修订背景

### 1. 项目全链路连通性检查

对整个 VCS 项目基础设施进行了一次完整的连通性验证，确保所有服务正常运行。

### 2. Bug 发现

在连通性检查中发现 `GET /api/v1/source-dictionaries` 返回 **500 Internal Server Error**。

根因分析：`AuthContext.scope_clause()` 对超级管理员返回 Python `True`，而 `source_data.py:193` 使用 `|` 运算符将其与 SQLAlchemy 表达式组合：
```python
auth.scope_clause(SourceDictionary) | (SourceDictionary.is_system == True)
```
Python `True` 不支持 `|` 运算符与 SQLAlchemy 表达式组合，抛出 `TypeError`。

---

## 详细变更

### 1. Bug 修复（`app/core/auth_context.py`）

#### 1.1 scope_clause 返回值修正
- **修改**: `scope_clause()` 超级管理员分支从 `return True`（Python bool）改为 `return true()`（SQLAlchemy `ClauseElement`）
- **新增导入**: `from sqlalchemy import true`
- **文档注释同步更新**

#### 1.2 修复原理
- `sqlalchemy.true()` 是 `ClauseElement`，支持所有 SQLAlchemy 表达式运算符（`|`、`&`、`.where()` 等）
- 与 Python `True` 在 `.where()` 中生成的 SQL 完全相同（`WHERE TRUE`）
- 修复了 `|` 运算符场景下的 `TypeError`

### 2. 影响评估

调用点分析（9 个文件，45 处调用）：

| 模式 | 数量 | 变更影响 |
|------|------|----------|
| 独立 `.where()` | 13 处 | ✅ 无影响（SQL 完全相同） |
| 逗号分隔 AND | 27 处 | ✅ 无影响（SQL 完全相同） |
| `\|` 运算符 | 1 处 | 🔧 **修复目标** |
| `owns()` Python bool | ~20 处 | ✅ 无影响 |

---

## 验收结果

### 连通性检查结果

| 组件 | 状态 | 详情 |
|------|------|------|
| PostgreSQL 16 + PostGIS | ✅ | 端口 5432，35 张表，168 个外键约束 |
| Redis 8.10 | ✅ | 端口 6379 |
| Celery Worker | ✅ | 已连接 Redis broker |
| FastAPI 服务 | ✅ | 端口 8000，91 路由 / 126 方法端点 |
| Health 端点 | ✅ | `{"status":"ok","postgres":"ok","redis":"ok"}` |
| Alembic 迁移 | ✅ | 最新 head：`0007_layer_group_table_links` |

### Bug 修复验证

| 测试 | 结果 |
|------|------|
| `scope_clause()` 返回类型 `True_`（SQLAlchemy true） | ✅ |
| `true() \| (SourceDictionary.is_system == True)` 组合 | ✅ |
| 真实数据库执行 `WHERE true() OR is_system = true` | ✅ |
| `GET /api/v1/source-dictionaries` HTTP 200 | ✅ |

### 回归测试（手动）

| 端点 | 状态 |
|------|------|
| `GET /health` | ✅ 200 |
| `GET /` (API 索引) | ✅ 200 |
| `GET /capabilities` | ✅ 200 |
| `GET /data` (上传文件列表) | ✅ 200 |
| `GET /data/stats` | ✅ 200 |
| `GET /data-standards` | ✅ 200 |
| `GET /data-standards-v2` | ✅ 200 |
| `GET /quality` (质检报告) | ✅ 200 |
| `GET /quality-rules` | ✅ 200 |
| `GET /batch` (批处理任务) | ✅ 200 |
| `GET /jobs` (转换作业) | ✅ 200 |
| `GET /source-tables` (数据源表) | ✅ 200 |
| `GET /dictionaries` (全局数据字典) | ✅ 200 |
| `GET /source-dictionaries` (数据字典) | ✅ 200（修复后） |

---

## 遗留说明

- **API Key 哈希算法**: 使用 SHA-256（非 bcrypt），安全评估待后续优化
- **测试数据状态**: 库中存量 5 个 stored_files、9 个 api_keys、109 个 quality_reports
