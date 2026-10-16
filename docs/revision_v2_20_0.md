# v2.20.0 修订记录

**发布日期**: 2026-09-20 · **版本**: v2.20.0

## 背景

例行架构审查（2026-09-20 生成 HTML 报告）产出候选优化项 C7/C2/C3/C1/C4，
按价值排序在本批次实施。核心主题：**让单一权威源取代 5 处漂移的重复定义**。

## 变更清单

### C7 — 切片大小漂移修复（bug）

| 文件 | 改动 |
|------|------|
| `app/api/v1/data.py` | 删除内联 `CHUNK_SIZE_MB = 2`；`expected_max` 与 413 错误消息统一读 `settings.file_chunk_size_mb`（=10，v2.1 起技术方案权威值）；`MAX_CHUNKS` 注释同步（10MB×500=5GB） |
| `tests/test_data_chunk_edges.py` | oversize 契约测试 payload 从 4MB 提升到 16MB（threshold 15MB=10×1.5） |

### C2 — secret_key 死配置（研究与澄清）

- 深入核查发现 `settings.secret_key` **全仓零引用**（认证走 API Key SHA-256 哈希 +
  admin_init_key 引导，无签名密钥）——**并非安全漏洞**，原始"加校验"建议不适格。
- 因 `SettingsConfigDict` 严格模式（`extra_forbidden`，实测确认），删除字段会让
  存量 `.env` 带 `SECRET_KEY` 的环境启动失败 → **保留字段仅作兼容，标注 v2.20 弃用**。
- `.env.example` 移除 `SECRET_KEY` 行；`BASELINE.md §9/§17` 同步标注。

### C3 — 日志文件轮转（运维）

| 文件 | 改动 |
|------|------|
| `app/config.py` | 新增 `log_file`（=./data/logs/vcs.log）/ `log_max_bytes`（10MB）/ `log_backup_count`（5） |
| `app/core/logging_setup.py` | `_ensure_file_handler()` 幂等装配 `RotatingFileHandler`；写盘失败优雅降级仅控制台；根治 `server_out.log` 无限增长 |
| `tests/test_core_a_p7a.py` | +2 例：装配幂等（根 logger 恰一个 handler）、INFO 记录真实落盘 |

### C1 — 评分权重收敛（五处漂移 → 单一权威）

**关键发现**：`DimensionScores.overall` 字段本就存在且 `compute_scores` 一直返回
`overall`，前端与 HTML 报告却不用，反而各自硬编码 `0.25/0.25/0.20/0.15/0.15`；
`quality_reports.score_overall` 列在库中**早已存在但从未写入/映射**（死列）。

| 文件 | 改动 |
|------|------|
| `app/services/quality_checker.py` | `WEIGHTS` 声明为唯一权威来源（注释约定） |
| `app/schemas/quality.py` | `QualityReportDetail` 新增 `score_overall` 响应字段 |
| `app/models/quality_report.py` | 补 `score_overall` ORM 映射（无默认值，存量 NULL 语义保留） |
| `app/api/v1/data.py` | quality-check 端点：落库 + 响应返回 `score_overall` |
| `app/api/v1/quality.py` | `_overall_from_scores()`（引用 WEIGHTS）；GET 详情 / HTML 报告 / upload 两路径全部改经此函数；POST /quality/check 落库 score_overall |
| `app/frontend/app.js` | 删除 3 处硬编码权重（L1136/1397/2271），改消费后端 `score_overall`（带旧响应回退） |
| `tests/` | 契约测试：权重归一化和值域、前端无硬编码守卫、GET 详情 `0.815` 契约断言、quality-check `85.75` 落库断言；`_FAKE_QC_RESULT` 补 `overall` |

### C4 — data.py 助手抽取（评估后放弃）+ 死代码删除

- 评估：`_guess_mime` / `_is_vector_file` / `_ingest_srid` 均**仅 data.py 内部使用**
  （无跨模块调用方），搬入 file_utils 属无价值重构；`_ingest_srid` 已是
  `custom_crs.resolve_ingest_srid_async` 的本地包装。放弃抽取（ADR-0001 的
  路由拆分仍需专项 + 测试先行，不在本批次）。
- 删除生产死代码 `_extract_layer_name`（无调用方，仅测试引用）+ 其 3 个测试 +
  `test_data_p5k3.py` 留档引用。

## 测试

- 新增 +4 契约/单元测试（日志×2、权重×2），删除死代码测试 ×3。
- 全量回归：**1537 passed**（v2.19.6 的 1536 基准 +4 −3），venv Python 3.13.5，`--no-cov`。

## 兼容性说明

- **API 向后兼容**：`score_overall` 为新增字段（默认 0.0）；旧报告（score_overall
  为 NULL）读取时按 WEIGHTS 即时重算返回，前端继续可用。
- **配置向后兼容**：`SECRET_KEY` 仍被接受（弃用）；新日志字段可选、有默认值。
- **无数据库迁移**：`score_overall` 列 v2.1 起已在库中；生产库无需改动。
- **前端行为**：综合分数值不变（同一套权重），仅改来源；`score_overall` 缺失时
  回退旧 `completeness_score` 字段。

## 版本同步

`CONTEXT.md` / `BASELINE.md` / `deploy_linux.sh` / `deploy_windows.bat` /
`restart.bat` / `app/main.py` 全部升至 v2.20.0。