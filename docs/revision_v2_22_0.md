# v2.22.0 修订记录

> **版本**: v2.22.0
> **日期**: 2026-09-22
> **批次**: Lane② — data.py 内联纯函数域迁移收口 + except-swallow 治理 + Redis 测试环境闭环
> **契约守卫**: `tests/test_data_p5k2.py`（28 passed in 4.26s）· `tests/test_data_p5k3.py`（28 passed）· 全量回归（1541 passed in 44.66s）

---

## Lane② — data.py 纯函数域继续拆分（合并入 data_format_helpers.py）

### 背景
v2.21.0 已把 data.py 的格式检测纯函数域（MIME_MAP / VECTOR_EXTENSIONS / _guess_mime / _is_vector_file / _parse_date_bound）拆分至 `app/services/data_format_helpers.py`。

v2.22.0 继续拆分 data.py 内剩余的两枚**纯函数**（子任务统计域）：

- `_empty_feature_stats(stored_file_id: str) -> dict`
  - 返回子任务统一统计结构的纯函数（written / skipped_empty / skipped_no_geometry / geometry_type / error 全零初始）
- `_collect_sub_task_results(results: list) -> tuple[dict, list]`
  - 将 `asyncio.gather` 结果转为 (stats_by_file, errors) 的纯函数
  - dict 结果：按 stored_file_id 收拢，error 字段并入 errors
  - Exception 结果：并入 errors（理论不发生，子任务内部已兜底）

### 变更
- **迁移目标**：`app/services/data_format_helpers.py`
- **helpers 终态**：90 行 / 5 符号单一 def（_guess_mime/_is_vector_file/_parse_date_bound/_empty_feature_stats/_collect_sub_task_results）/ 零重复定义
- **data.py 接线块**：`from app.services.data_format_helpers import (...)` 共 7 符号（MIME_MAP, VECTOR_EXTENSIONS, _guess_mime, _is_vector_file, _parse_date_bound, _empty_feature_stats, _collect_sub_task_results）
- **内联残留**：data.py 内联 `def _empty_feature_stats` / `def _collect_sub_task_results` **零残留**

### 验收
| 项 | 结果 |
|----|------|
| 契约守卫 tests/test_data_p5k2.py | **28 passed in 4.26s** |
| 全量回归 pytest | **1541 passed in 51.55s** |
| main.py version 字段 | `2.22.0` ✅ |
| 六锚文档批次 | revision_history/CONTEXT/BASELINE/deploy_linux/deploy_windows/restart 全命中 v2.22.0 ✅ |

---

## Lane② — data.py except-swallow 治理（静默吞异常收口）

### 背景
Lane② 第二批：data.py 内 `except Exception: pass` 风格的**静默吞异常**（bare swallow）均不做任何记录，异常一旦发生即无痕消失，排查成本高。本批次在不改变行为语义（仍不抛错、不改变控制流）的前提下，为全部裸吞点补上 `logger.warning(..., exc_info)` 记录，使异常可观测。

### 变更
- **data.py 终态**：LINES:2552 / BYTES:110487 / `BARE_EXCEPT_PASS:0` / `LOGGED_WARNING:24` / `PARSE_OK:True`
- **治理方式**：每处 `except Exception:` 块在保持原 fallback 行为不变的前提下，入口追加一条 `logger.warning(f"...: {e}", exc_info)`；`pass` 体保留。早期帧曾误报 BARE_EXCEPT_PASS:0，提交前复查发现 7 处裸吞漏网（清理/探测落空路径），已全部补日志并钉死复扫帧 0。
- **可逆性**：治理前留档备份（`.bak`）保留于会话临时区，byte 级可回滚。

### 测试环境闭环（Redis）
- 全量回归早期权威帧：`34 failed, 1507 passed in 9259.53s`——红根 = 治理试验期残留脏行（`key_groups`/`quality_plans` 唯一键撞车），非代码缺陷；
- 残留治理后：`1 failed, 1540 passed in 9612.36s`——唯一红 = `tests/test_api.py::TestConversion::test_submit_conversion`，根因 **Redis 未启动**导致 convert 降级路径断言 `pending != running`（`convert.py:139` 探测 +`OVERRIDE` 同步执行）；
- **闭环动作**：本机拉起项目自带 `Redis8/redis-server.exe`（standalone:6379，`Redis8/redis.conf`）→ 单测 `1 passed in 0.78s` → 关键 4 文件 `107 passed in 2.93s` → 全量 **1541 passed in 44.66s**（Redis 在线使大批 Celery 相关用例不再走连接超时，套件从 2.7h 缩短到 45s）。

### 验收
| 项 | 结果 |
|----|------|
| 契约守卫 tests/test_data_p5k3.py | **28 passed**（多帧，含 227.40s 首帧）|
| 治理质量帧 | **BARE_EXCEPT_PASS:0 / LOGGED_WARNING:24 / PARSE_OK:True**（提交前复扫帧）|
| 全量回归 pytest（Redis 在线）| **1541 passed in 44.66s** |
| 环境红闭环 | `test_submit_conversion` → **1 passed in 0.78s**（Redis 拉起后）|
