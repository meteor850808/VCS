# v2.19.6 修订记录

**发布日期**: 2026-09-20 · **版本**: v2.19.6

## 背景

用户反馈：「一天清理 ZIP 后，ZIP 数据好像没有实际写到 PG 数据库中」。

## 根因（完整复现）

用真实服务（127.0.0.1:45679）上传 ZIP 并 3 步复现：

1. 上传 `dbg_zip_*.zip`（含 inner.geojson）→ 生成父壳记录（`is_archive=True`）+
   子文件记录（`source_archive=ZIP名`），特征**确实写入**子记录分区表
   （`point_features_p_<child_id>`，查询 total=3）；
2. 回拨父记录 `created_at` 3 天 → 调 `POST /data/cleanup-archives?days=1`；
3. **父壳 + 子记录一并被删，PostGIS 分区被 DROP**。

结论：数据并非"没写入"，而是写入后被自动清理删掉了。根因在
`cleanup_old_archives` 误用 `_delete_file_recursive`（文件删除语义：递归删子 +
DROP 全部分区），而压缩包壳只是"上传批次外壳"，其子记录才是真实数据。

## 方案（经用户确认，v2.19.6 方案 A）

**cleanup-archives 只删压缩包壳，保留解压数据**：

| 文件 | 改动 |
|------|------|
| `app/api/v1/data.py` | `cleanup_old_archives` 重写：① 将子记录 `source_archive` 置 `None`（解除关联，提升为独立文件记录）② 只删壳记录 ③ **不调用 `_delete_file_recursive`、不 DROP 任何分区**；返回 `{deleted, cutoff_days}`；日志 `[cleanup-archives]` 记录保留子文件数 |
| `tests/test_data_crud_quality.py` | +2 例（下述） |

**语义边界（显式删除不受影响）**：
- `DELETE /data/{id}` / `POST /data/batch-delete` / 同名覆盖 `_delete_existing_by_name`
  仍走 `_delete_file_recursive` —— 删父+子+分区，递归语义不变；
- `source_archive` 置空是必要设计：上传聚合（data.py:822-838、1284-1299 按
  `source_archive == filename` 统计 `feature_count`）在重传同名 ZIP 时不会扫到
  孤儿旧记录而虚高；`_extract_layer_name` 无 `source_archive` 时
  `Path("a.zip/layer.shp").stem` 仍正确返回 "layer"。

## 测试

新增 2 例（`TestBatchDeleteCleanup`）：

1. `test_cleanup_archives_preserves_child_data`：真实上传 ZIP → 断言子记录 +
   分区存在且 `feature_count==1` → 回拨父 created_at 10 天 → `cleanup(days=7)`
   → 断言壳删、子记录保留且 `source_archive is None`、**分区表仍在且 count==1**；
2. `test_delete_archive_recursive_still_recurses`：回归——显式 `DELETE /data/{id}`
   仍递归删父+子+分区（`to_regclass` 为 NULL）。

均用唯一 ZIP 名（`h5_<uuid8>.zip`）避免 vcs_test 跨运行残留。

## 测试踩坑实录（重要）

- **`db_session` 读事务持锁阻塞 DROP TABLE**：新测试若先
  `select(ApiKey)` 查 key_id（用 `_TestSession` 会话、到 fixture 结束才
  commit/rollback），会对 `api_keys` 持有 `AccessShareLock`；端点内
  DROP 分区表（含 FK 引用 `api_keys`）需对被引用表取 `AccessExclusiveLock` →
  无限等待（`pg_locks` 可见 granted=f 的排他锁请求被共享锁阻塞）。现有
  `test_delete_archive_recursive` 从不查 ApiKey，故从不触发。**修复：新测试
  不预查 ApiKey**（key_id 本就未用），单测从 3min+ 挂起 → 1.12s 通过。
- 僵尸 PG 事务（强杀 pytest 遗留 `idle in transaction`）同样会阻塞
  `drop_partition` 的 DROP TABLE（`pg_stat_activity` 可见
  `wait Lock / duration 2656s`）——用 `SELECT pg_terminate_backend(pid)` 清理。

## 验证

- `tests/test_data_crud_quality.py`：**34 passed**（含 +2 新例，3.48s）。
- 全量回归：**1536 passed / 49.45s**（venv Python 3.13.5，`--no-cov`）。
- vcs_test 库验证：cleanup 后子记录 `source_archive=None`、分区数据 count=1
  物理保留；显式删除后分区 DROP。

## 兼容性说明

- 自动清理语义变更：**只删壳、永不 DROP 分区/丢数据**；对历史已删除数据不可逆。
- 前端 `cleanup-archives?days=1` 调用（app.js:981）继续有效，现仅清理真过期壳。
- API 响应结构不变（`{deleted, cutoff_days}`），无数据库迁移、无新依赖。

## 版本同步

`CONTEXT.md` / `BASELINE.md` / `deploy_linux.sh` / `deploy_windows.bat` /
`restart.bat` / `app/main.py` 全部升至 v2.19.6。