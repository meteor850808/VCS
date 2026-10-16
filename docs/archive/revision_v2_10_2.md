# v2.10.2 修订记录

> 日期: 2026-08-21

## 变更摘要

连接池优化 + 子任务并发控制 + 数据管理上传流程修复

## 具体变更

### 1. 连接池配置优化（`app/config.py`, `app/db/session.py`）

| 参数 | 旧值 | 新值 | 说明 |
|------|------|------|------|
| `db_pool_size` | 10 | 5 | 异步引擎常驻连接数 |
| `db_max_overflow` | 20 | 15 | 异步引擎溢出连接数（总上限 20） |
| `db_pool_timeout` | — | 30 | 新增：连接等待超时（秒） |
| `max_concurrent_db_tasks` | — | 5 | 新增：子任务并发上限（Semaphore） |
| 同步引擎 pool_size | 10 | 3 | GDB 读取有 `gdb_read_lock` 串行化，不需要大池 |
| 同步引擎 max_overflow | 20 | 5 | 同上 |

**设计决策**: 异步引擎 5+15=20 连接上限，同步引擎 3+5=8 上限，两池合计最多 28 连接，远低于 PostgreSQL `max_connections=100`，留足余量给 pgAdmin / psql 等工具。

### 2. 子任务并发控制（`app/api/v1/data.py`）

- `_load_extracted_features_async` — 添加 `async with db_task_semaphore` 包裹 DB 写入
- `_load_shp_group_features_async` — 添加 `async with db_task_semaphore` 包裹 DB 写入
- `_load_all_features_to_spatial_async` — 添加 `async with db_task_semaphore` 包裹 DB 写入
- `_load_gdb_layer_async` — 未添加（使用 sync pool + `gdb_read_lock` 已串行化）

**根因**: `asyncio.gather` 并发启动 N 个子任务，每个创建独立 session。无限制并发时，N>20 即耗尽连接池 → 后续请求（含主请求自身）排队超时。

### 3. 环境变量模板更新（`.env.example`）

新增 `DB_POOL_TIMEOUT=30`、`MAX_CONCURRENT_DB_TASKS=5`。

## 影响范围

| 模块 | 影响 |
|------|------|
| `app/config.py` | 新增 `db_pool_timeout`、`max_concurrent_db_tasks` 字段 |
| `app/db/session.py` | 新增 `db_task_semaphore`；同步引擎使用 `min()` 限制池大小 |
| `app/api/v1/data.py` | 3 个 async 子任务函数添加 Semaphore |
| `.env.example` | 新增 2 个配置项 |

## 验证

- [x] GeoJSON 上传 → `feature_count=1`，`point_features` 分区 1 条
- [x] OBJ zip 上传 → 子记录创建 + 3D 要素写入
- [x] 同名文件替换 → 旧记录（含子记录）删除，新记录创建
- [x] 清理端点 → `cleanup-archives?days=1` 正常返回
- [x] 连接池无耗尽日志
