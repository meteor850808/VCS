# VCS 空间质检 — 版本修订记录汇总

> **维护规则**: 每次发版追加新版本段落，不修改旧段落。  
> **最后更新**: 2026-10-13  
> **当前版本**: v2.24.0

---

## v2.24.0 批 · Docker 生产加固 (2026-10-13)

**变更**: 把既有 Docker 化升级为内网 Linux 单机可交付的生产部署，详见 `docs/revision_v2_24_0.md`。
- **Nginx 反代**：新增 `docker/nginx/*`（自签 TLS，80→443；反代 `/api/ /ws /health /docs /redoc /openapi.json`，托管前端静态 `/app/`）；`docker-compose.prod.yml` 覆盖层默认不外发 api/pg/redis 端口
- **Docker secrets**：`POSTGRES_PASSWORD`/`ADMIN_INIT_KEY`/`FLOWER_BASIC_AUTH` 走 `secrets/*.txt`；`docker/entrypoint.sh` 从 `/run/secrets/*` 装载并按需覆盖 env
- **单一真源连接串**：`DATABASE_URL`/`DATABASE_URL_SYNC` 由 entrypoint 从 `POSTGRES_*` 派生，compose 不再硬编码
- **Linux 能力补齐**：镜像源码构建 LibreDWG（`dwg2dxf`）+ mdbtools/unixodbc；新增 `app/services/_odbc.py` 平台感知驱动（Windows Access / POSIX MDBTools）；DWG 转换与 MDB **读取** Linux 可用，MDB **导出**加平台守卫（非 Windows 明确报错）
- **既有缺陷修复**：D1 卷路径/env 不一致、D3 beat 命令缺 `.celery_app`、D2 Flower `$FLOWER_BASIC_AUTH` 未展开、D4 镜像缺 DWG/MDB 能力、D6 `version:` 废弃、D7 基础镜像浮动 tag
- **发布脚本**：`Makefile` + `docker/scripts/gen_secrets.sh`/`gen_certs.sh`；`.gitattributes` 钉脚本 LF
- **测试**：新增 `tests/test_odbc_platform.py`/`tests/test_export_mdb_platform.py`；`test_dwg_converter.py`/`test_export_service_branches.py`/`test_data_download_export.py` 平台无关化（Windows-only 用例加 skipif），Windows 行为零回归

---

## v2.23.0 批 · Windows 离线一键部署子系统 (2026-10-09)

**变更**: 新增 `deploy/` Windows 离线一键部署子系统，详见 `docs/revision_v2_23_0.md`。
- **联网机构建**：`deploy/build_bundle.ps1` 生成离线资源包（安装包 + Redis/LibreDWG 二进制 + 全量 wheel + 种子，逐文件 SHA-256 落 `offline/manifest.json`），127 文件 / 752.6MB
- **目标机安装**：`deploy/deploy_offline.bat` 十二阶段编排（00_preflight / 10 Python / 20 PG / 21 PostGIS / 22 ACE [非关键] / 25 VC++ [非关键] / 30 Redis / 40 venv / 50 建库迁移 / 60 凭证+种子 / 90 起服 / 99 验证），UAC 自提升、`--from N` 幂等续跑、`--dry-run`、失败分级 + `deploy/logs/` 日志
- **凭证**：`ADMIN_INIT_KEY`/`API_KEY` 安装末尾打印并存 `deploy/.install_state.json`
- **旧入口退役**：`deploy_windows.bat` 薄封装转调 `deploy\deploy_offline.bat`
- 版本矩阵：Python 3.13.3 / PostgreSQL 15.15 / PostGIS 3.6.1 / Redis 8.10.0 / LibreDWG / VC++2019 / ACE2016

---

## v2.22.3 批 · CSV 无损归档往返 + 字段-字典绑定模板往返 (2026-09-29)

**变更**: 闭环 v2.22.2 留档的两条技术债，详见 `docs/revision_v2_22_3.md`。
- **绑定随模板往返（A 方案）**：「字段定义」Sheet 新增第 10 列「字典编码」，多值分隔 `,，;；`/空白，保序去重，首个编码落 `is_primary=True`；JSON 字段对象同构 `dict_codes`；导入侧 `pending_links` 待字典建完再统一建链；未知编码跳过并告警；**旧 9 列模板兼容**
- **CSV 升级为 A2-1 无损归档**：`export?format=csv` / `template/csv` / `import/csv` 三端统一为 **ZIP 归档** —— `_manifest.json`（仅 format/version/encoding/generator/roles/diagnostics，**不含结构数据**）+ 6 个规范化角色 CSV（标准信息/层组/数据表/字段/字典/字典项），全部 UTF-8 with BOM。**层组编码/表编码/字典编码为连接键，行顺序即 ordering**，因此不依赖服务端 id，跨环境往返结构不变
- **CSV 也能带字典与绑定往返**（本版核心变化）：字典拆两个文件避免同名字段冲突；**空字典也能表达**（Excel 侧 `if d.items` 会丢弃）；字段表比 Excel 多一列「默认值」，避免丢 `StandardField.default_value`
- **保留 legacy 路径**：ZIP 内无 `_manifest.json` → 走「每文件一表、字段取首行」旧行为，外部作者零破坏
- **降级与容错**：`tables`/`fields` 缺席 400，其余角色缺席按空处理；未知层组/父级/表/字典引用、重复编码、额外未声明文件、单元格容错均告警而非失败；角色文件非 UTF-8 → 400（静默按空 = 删数据）
- **`标准信息.csv` 真正被消费**：`DataStandard.description/category` 往返恢复；表单显式值优先，**名称永不覆盖**（它是 update/upsert 的目标解析键），冲突只告警
- **修掉一个静默 bug**：`标准信息.csv` 原导出成「首格是表头文字 + 3 个数据值」的 4 列形态（无表头行），导入时整列错位一列 —— 只有真的消费该角色才会暴露
- **`update/upsert` 保持全删重建**（`_delete_standard_children`）—— 既有的「模板为权威状态」语义
- **守卫**：`tests/test_imp_exp_dict_roundtrip.py` **75 例** + `tests/test_csv_upload_edge_cases.py` **2 例**（TDD 全程，RED → GREEN 逐个闭环）
- 全量回归 **1694 passed**，覆盖率 **100.00%** 保持（11418 stmts / 0 miss）；前端契约 30 passed，Playwright **31/31**，`node --check` OK
- **未做/已知债**：知识图谱（仍为调研结论，建议纯视图不引入图数据库）；CSV 创建路径的宽泛 `IntegrityError` 仍误报为「标准名称已存在」；Excel 侧空字典导出仍会丢弃；`update/upsert` 三格式统一返回 201（既有契约）

---

## v2.22.2 批 · 数据标准导入/导出/模板单框三段重构 (2026-09-29)

**变更**: UI 重构 + mode 暴露 + 链路缺陷定位，详见 `docs/revision_v2_22_2.md`。
- **UI 重构**：`index.html` + `app.js` 把「3 主 Tab + 3 子 Tab + 2 dropzone」共 **6 个切换按钮**收敛为「**唯一格式选择器 + ①导入 ②导出 ③模板 三段同框**」；新增 `V2_IE_FMT`（`label`/`accept`/`multiple`/`tpl` 三源同构），删除 6 个旧函数，**后端零改动**
- **mode 暴露**：后端 `VALID_IMPORT_MODES`（`import_export.py:44`）此前**前端从不下发**，恒 `create`；现 UI 暴露 `create`/`upsert`/`update`
- **CSV 导出禁用**：`export_standard` 只有 json/excel 分支，csv 必 400 → UI 显式 `disabled` + tooltip（原按钮点了必然报错）
- **字典绑定链路查实缺陷（未修，留档）**：Excel 5 Sheet 确含「字典定义」可导入，但**绑定零覆盖**（模板无字典列、`_create_v2_children` 从不建 `StandardFieldDictLink`、无批量绑定端点）；而绑定是质检 `VALUE_IN_SET` 规则 + A5 值域的真实数据源 → 导入不绑定则值域质检整链失效；`update/upsert` 导入更会删除已绑定
- **知识图谱**：全仓 0 命中，GitHub 无现成产品；分档建议 A 纯视图（`GET /{id}/graph` + Cytoscape.js，**不引入图数据库**）起步
- **三层守卫**：`tests/test_frontend_imp_exp_ui.py`（新建 30 例，含 `node --check` 语法关卡）+ `scripts/smoke_imp_exp_ui.py`（新建 28 例 Playwright 真实 DOM 冒烟）；**4 次变异测试自证用例有效**
- **过程中修掉 3 处静默失效**：`app.js` 残留裸 `}` 无法解析、`var(--t3)` 不存在、`.v2-ie-sec:first-of-type` 被 fmt bar 抢先匹配
- 全量回归 **1617 passed**（1587 基线 + 30 新增，0 回归），覆盖率 **100.00%** 保持

---

## v2.22.1 — 架构优化批次：孤儿模块清除 + pytest 基建现代化 (2026-09-28)

**类型**: 死代码清除 + 测试基建。详见 `docs/revision_v2_22_1.md`。
- **孤儿模块删除**: `app/services/quality/scoring.py`（五维评分模型）生产零引用——真实评分路径为 `quality_checker._calculate_score_legacy`（单维通用）+ `reporter._score_color`；`tests/test_scoring.py`（16 例）一并移除；
- **pytest 基建现代化**: `pytest.ini` 开启 `asyncio_mode=auto`，全仓删除 **629 处** `@pytest.mark.asyncio` 装饰器（git diff 复核删除 629 / 新增 0，无其他装饰器误删）；
- **漏跑复核**: 早期帧 "45 例漏跑 async 测试" 为 AST 统计脚本误报——装饰器挂在类级而非函数级，`git grep` 复核 HEAD 基线实为 **0 缺漏**；全量收集数账目自洽（1541 − 16 = 1525）；
- **conftest 审慎放弃**: "client 不依赖 test_db" 经审计为**有意的分层设计**（非巧合兜底），14 处 autouse 包装去重收益 <1 行/文件，不做无谓回归风险；
- 全量回归 **1525 passed in 44.66s**（Redis 在线）。

---

## v2.22.0 — Lane② data.py 收口批次：纯函数迁移 + except-swallow 治理 + Redis 测试环境闭环 (2026-09-22)

**类型**: 架构收口 + 可观测性治理 + 测试环境闭环。详见 `docs/revision_v2_22_0.md`。
- **p5k2 纯函数域迁移**: `_empty_feature_stats` / `_collect_sub_task_results` 并入 `app/services/data_format_helpers.py`（helpers 终态 90 行 / 5 符号 / data.py 内联零残留）；
- **except-swallow 治理**: data.py 全部 `except Exception: pass` 静默吞异常点收口为 `logger.warning(..., exc_info)`（LINES:2552 / BARE_EXCEPT_PASS:0 / LOGGED_WARNING:24 / PARSE_OK:True），行为语义不变、异常可观测；提交前复扫钉死 0 裸吞（早期帧误报 0，实 7 处漏网已补日志）；
- **Redis 测试环境闭环**: 拉起项目自带 `Redis8/redis-server.exe`（standalone:6379）→ 修复 `test_submit_conversion` Redis 降级路径环境红（`pending != running`）；
- 全量回归 **1541 passed in 44.66s**（残留脏行治理 + Redis 在线后从 34 红 / 2.7h 收敛到 8 分 45 秒级全绿）。

---

## v2.21.0 — 报告→预览定位联动批次（方案A 评估） (2026-09-21)

**类型**: 新功能（报告违规样本 → 预览页定位联动）。详见 `docs/revision_v2_21_0.md`。
- `app/frontend/app.js` 新增 `locateReportSample(reportId, sampleIndex)` 与 `locatePvByFeatureIndex(pvId, featureIdx)` 两条定位链路（StoredPreview 坐标/要素索引契约）；
- `TestReportLocateContract` / `TestReportDocLocateContract` 契约守卫入库（报告 HTML 段 ↔ 预览定位契约）；
- 版本锚 bump：CONTEXT / BASELINE / deploy_linux / deploy_windows / restart / main.py 全升至 v2.21.0。

---

## v2.20.0 — 架构收敛批次 (2026-09-20)

**类型**: 修复 + 运维 + 收敛（C7 切片大小漂移 / C2 secret_key 死配置弃用 / C3 日志轮转 / C1 评分权重单一权威 / C4 评估与死代码删除）。详见 `docs/revision_v2_20_0.md`。
- 切片上限统一 `settings.file_chunk_size_mb`（10MB×1.5 余量，413 契约更新 16MB）；
- 日志 `RotatingFileHandler` 轮转（`log_file`/`log_max_bytes`/`log_backup_count`）；
- `quality_checker.WEIGHTS` 唯一权威；`score_overall` 补映射/落库/返回，前端删除 3 处硬编码权重；
- 删除生产死代码 `_extract_layer_name`；
- 全量 **1537 passed**；无数据库迁移、API 向后兼容。

---

## v2.18.0 — CAD 检查功能：ezdxf 解析 + 上传缓存 + 前端面板 (2026-09-11)

**类型**: 新功能（CAD 全链路：解析服务 ×2 + 缓存 + 4 个新 API 端点 + 前端 CAD Tab；数据库迁移 0013）

### ① 后端 CAD 解析（`app/services/cad_parser.py` + `cad_preview.py`）
- `CADParser`：ezdxf 读取 DXF/DWG（DWG 先经 `dwg_converter` 转 DXF）——图层树（LAYER 表 + 实体计数统计）、实体明细（handle/type/layer/端点长度/文本/块名）、文本内容（TextEntry）、元数据（DWG/DXF 版本映射 AC1032→R2018、单位、块/标注/填充存在性）。
- `aci_to_rgb()` ACI→RGB 颜色映射；`_SKIP_LAYERS={"0","Defpoints"}` 过滤冗余层；实体缓存上限 `MAX_CACHED_ENTITIES=2000`（超限只统计不存明细）。
- `CADPreviewGenerator`：手写 SVG（bbox 自动缩放 + 图层配色）、PNG/缩略图自绘 Pillow（`_render_pillow` 直接绘制 LINE/LWPOLYLINE/CIRCLE/ARC/TEXT/MTEXT/ELLIPSE——**ezdxf 1.4.4 无官方 PillowBackend，故自绘；不引入 matplotlib 依赖**）；空/无效图 fallback 占位。

### ② 上传时缓存（`app/services/cad_cache.py` + `app/api/v1/data.py`）
- **关键架构决策**: 项目【优化#5】上传后清空 `file_data`（下载返回 410），路由层无法查询时重新解析 ⇒ 改为**上传提交时一次性解析**：`cache_cad_snapshot()` 线程池内解析 → 元数据/图层/实体摘要写入 `stored_files.cad_metadata`(JSONB) + 预生成 `data/previews/{file_id}.svg/.png` 写入 `cad_preview_path`；失败仅日志不阻断上传（DWG 缺 LibreDWG 时同样跳过）。

### ③ 新增 API（`app/api/v1/cad/` 4 端点，注册于 router.py）
| 端点 | 说明 |
|------|------|
| `GET /api/v1/cad/{file_id}/info` | 元数据；未缓存 421 |
| `GET /api/v1/cad/{file_id}/layers` | 图层清单（名称/颜色/线型/可见性/实体数） |
| `GET /api/v1/cad/{file_id}/entities` | 实体分页（layer/entity_type/offset/limit） |
| `GET /api/v1/cad/{file_id}/preview?format=svg\|png` | 磁盘缓存预览图 |

### ④ 数据库迁移 `0013_cad_enhancement`
- `stored_files` +2 列: `cad_metadata`(JSONB, NULLABLE)、`cad_preview_path`(String(1024), NULLABLE)
- `stored_file_layers` +5 列: `color_index`(Integer)、`color_rgb`(String(16))、`linetype`(String(64))、`is_visible`(Boolean, DEFAULT True)、`is_frozen`(Boolean, DEFAULT False)
- 幂等（IF NOT EXISTS）设计；`down_revision="0012_field_dict_links_fk"`。

### ⑤ 前端 CAD Tab（`app/frontend/app.js` + `index.html`）
- 文件详情抽屉新增「🏗️ CAD」Tab（仅 dxf/dwg 显示）：元数据概览 + 预览图（fetch blob 注入 img 绕过鉴权头限制）+ 图层列表（点击筛选实体）+ 实体分页表（类型图标/图层/长度/文本）；实体点击 tooltip 显示句柄与坐标。
- 分页 50 条/页，图层过滤联动重新加载。

### ⑥ 测试
- `tests/test_cad_parser.py`（15 例）、`tests/test_cad_preview.py`（7 例）、`tests/test_cad_routes.py`（14 例，session 级 fixture 规避上传 5/min 限流；含 400/404/421 路径与缓存落库断言）。
- 夹具 `tests/fixtures/cad/{simple,complex}.dxf` + 生成脚本。
- 全量回归 **1491 passed**，无失败。

*版本号同步*：CONTEXT.md / BASELINE.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.18.0。

---

## v2.17.2 — 数据预览属性表增强：分页设置 + 点击定位 (2026-09-11)

**类型**: 前端功能（`app/frontend/app.js` + `index.html`，后端零改动、无数据库变更）

### ① 属性表滚动 + 分页设置
- 要素拉取上限 **5000 → 10000**（服务端允许上限）；文件要素超限时属性栏提示「⚠ 文件共 N 个要素，仅加载前 10,000 个」，`pv-feat` 卡片显示 `N+`。
- 属性表**全字段渲染**（原实现仅显示前 10 个字段、前 50 行），容器 `overflow-x:auto` 横向滚动 + 单元格 ellipsis（hover title 显示全文）。
- 客户端分页栏复用 `.paging` 样式：`← 上一页 | 第 X 页 / 共 Y 页 | 下一页 →` + 每页行数选择器（50/100/200）；`pvAC` 显示「显示第 a-b 行 / 共 N 行」；空结果时清空表格并隐藏分页（避免残留）。

### ② 点击记录定位地图
- 属性行点击 → 按要素对象引用精确匹配 → 点 `flyTo`（最低缩放 16）/ 线·面 `flyToBounds` → 要素橙色高亮闪烁 1.6s 后还原 + popup 弹出。
- 行高亮 `.pv-sel-row`：点另一行切换选中、再点同一行取消；不重渲染表格（保持容器滚动位置）。

### 验证
- E2E 新入 `tests/e2e/preview_attr.py`（Playwright，登记于 `tests/e2e/README.md`）：130 要素合成文件驱动 UI 断言 10 项 + 真实 10504 要素 DWG 截断用例 → **ALL OK**，console 错误 0。
- `node --check app.js` 通过。

*版本号同步*：CONTEXT.md / BASELINE.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.17.2。

---

## v2.17.0 — 测试覆盖收官：全代码库 100% 覆盖（1455 passed / 100.00%）(2026-09-10)

**类型**: 工程化（P7 收官轮）：P7a core 骨架层 → P7b data_standard_v2 全覆盖 → P7c 最后 3 个 api 服务工厂清零

### 里程碑
- **全仓库 `TOTAL 100.00%`**：10120 stmts / 0 miss，114 个代码文件全部 100%（其余全覆盖文件按 complete coverage 跳过）
- 全量回归 **1455 passed**，连跑 2 次稳定

### ① P7a core 骨架层全覆盖（60 例）
- `tests/test_core_tail_p7a.py`：`core/`（temp_cleanup / db_utils / config / logging / file_utils / rate_limit / security_middleware / auth_context / deps）+ `db/session.py` + `main.py` + 4 个模型文件 100%。
- 修复 2 处：`core/cache.py` 模块重载父属性漂移；redis 缺失时注解降级崩溃。
- 1356 passed / 95.39%。

### ② P7b data_standard_v2 + quality_rules 全覆盖（103 例）
- `tests/test_data_standard_v2_*_p7b.py` 系列：data_standard_v2 子模块 11 文件 + `quality_rules.py` 全 100%。
- 3 处顺序稳健修复：全局字典断言 any() / stats 全局计数放宽 / source 字典列表过滤 is_system。
- 1432 passed / 99.08%。

### ③ P7c 最后 3 个 api 服务工厂清零（23 例）
- **convert.py 64%→100%**：Redis 降级双路径（坏 URL / 任务异常）、取消运行、硬删除全分支含 legacy 兼容、`_rm_tree` 四态。
- **data.py 98%→100%**（20 miss 清零）：`_extract_layer_name` 双分支、`_populate_table_schema` 坏 xlsx 兜底、SHP 伴生 .shp continue。
- **quality.py 99%→100%**：S4 放行分支（feature_count=0 但 point 分表实际有要素 → 放行并以实际要素质检）。
- **通用不可达模式落定 + 8 处 pragma**（留档）：
  - multipart 无 filename 的 part 由 starlette 解析为表单字段 → FastAPI 422 先行 → handler 内 `if not file.filename` 分支不可达（convert.py 87/342、data.py 331、quality.py 245）
  - `security_middleware` content-length 413 预检先于 handler → handler 413 分支不可达（convert.py 102-104/340-341）
  - data.py 416：`_detect_and_populate` 的 detected 恒非 None（非矢量→"table"）→ else 分支不可达
  - data.py 800-804：GDB 组必有 sub_task（无 layers 则 tmp 为 None）→ 清理循环恒空
  - data.py 1704：standard_id 空串违反 FK，commit 必失败，无法作为成功路径到达
  - data.py 2170-2171 / quality.py 840：防御性兜底（ignore_errors 已吞 / Form 空列表无法解析）

### ④ 前端 CDN 自托管（项目宪章「不依赖 CDN」落地）
- Leaflet 1.9.4 dist 落地 `app/frontend/vendor/leaflet/`（leaflet.css 14.8KB + leaflet.js 147.5KB + images/ 5 张），`index.html` 两处 `https://unpkg.com/leaflet@1.9.4/...` 改为本地 `/app/vendor/leaflet/...`。
- Playwright 验证（系统 Python312）：`window.L` v1.9.4 加载成功、**0 console error / 0 failed request**（零 CDN 请求）。静态资源经 `/app` 挂载直读磁盘，无需重启服务即时生效。
- 项目宪章「前端工具不依赖 CDN，全部内联，深色主题优先」→ 全部第三方前端依赖已自托管（主脚本 v2.16 外置 app.js、Leaflet v2.17 vendor/）。

### 验证
- `pytest` 全量回归 ×2：**1455 passed / 100.00%**
- OpenAPI `version: 2.17.0`（app/main.py）
- 无数据库变更、无新增 Python 依赖；后端代码仅 pragma 留档注释，生产行为零变化；前端 CDN → 本地 vendor 同版本替换

*版本号同步*：CONTEXT.md / BASELINE.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.17.0。

---

## v2.17.1 — Alembic 迁移链空库验证修复 + Dict FK 半迁移闭环 (2026-09-10)

**类型**: 数据库运维修复（全新库首次 `alembic upgrade head` 跑通）

### 背景
迁移链从未在空库上完整执行过（生产库经多次手动修补迁移到 0011），本轮以临时库 `vcs_mig_verify` 从 0 到 head 全链验证，暴露 4 处链缺陷并修复：

1. **0002_data_standard_v2_2** — 两处缺陷：
   - `data_standards.updated_at` 重复 `ADD COLUMN`（0001 建表时已建 → DuplicateColumnError），已移除；
   - `dictionary_items` 重名建表（0001 已建，FK 指 `source_dictionaries`）→ 改为**不重建表**：实查 `pg_constraint` 换 FK 指 `standard_dictionaries` + 补 `uq_di_dict_code` 唯一约束（downgrade 对称回退）。
2. **0003c_fix_quality_reports_cols** — `quality_reports.updated_at` 重复 ADD（0001 已建），已移除（downgrade 不回退该列）。
3. **0003f_add_spatial_updated_at** — 三张空间分区表 updated_at 重复 ADD（0001 已建），改为 no-op（保留函数维持迁移链语义）。
4. **0012_field_dict_links_fk（新增）** — `field_dict_links.dict_id` FK 由 `source_dictionaries.id` 改指 `standard_dictionaries.id`（模型早已切换、vcs_test 由 create_all 重建已生效，生产库缺此迁移）。revision id 首次定义为 34 字符超出 `alembic_version.version_num` VARCHAR(32) → 缩短为 `0012_field_dict_links_fk`。迁移体幂等：实查 `pg_constraint` 得约束名/引用表，已指向正确表或约束缺失则跳过。

### 验证（temp 库 vcs_mig_verify，用完已 DROP）
- 全新库 `upgrade head` 全程无告警至 **`alembic_version = 0012_field_dict_links_fk`**
- `dictionary_items` / `field_dict_links` 的 dict_id FK 均指向 `standard_dictionaries(id) ON DELETE CASCADE`
- `data_standards.updated_at`、`point_features.updated_at` 均单列（无重复）
- `downgrade 0011` → 0012 回退正确（FK 还原 `source_dictionaries`）；再 `upgrade head` 幂等重入通过

### 生产库升级
```bash
alembic upgrade head   # 将应用 0002(换 FK+唯一约束) → 0003c/0003f(no-op) → 0012(FK 改指)
```
无应用层代码变更；迁移文件改动不影响运行时行为。

*版本号同步*：CONTEXT.md / BASELINE.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.17.1。

---

## v2.16.0 — 安全加固 + 覆盖率基线 + 前端脚本外置 + 全链路冒烟 (2026-09-02)

**类型**: 工程化 / 安全加固（三项提升落地）

### ① 安全：移除引导密钥弱默认值（P0）
- `app/config.py`：`admin_init_key` 去掉默认值 `"admin-init-change-me"`，改强校验
  （`field_validator`）：缺失或命中已知弱口令集合时**启动拒绝**（fail-fast）。
- 修正 CI 环境变量名：`API_KEY_INIT`（错）→ `ADMIN_INIT_KEY`（正确）；`.env.example` /
  `BASELINE.md` / `README.md` 移除弱默认值并注明必填 + 生成示例。
- `tests/conftest.py` `admin_init_header` 依赖真实 `settings.admin_init_key`，本机/CI 均
  从 `.env` / 环境变量取强密钥，无需改动。

### ② 测试与 CI：建立覆盖率基线 + 全链路冒烟（P1）
- `pytest-cov`、`bandit` 纳入 `requirements.txt`；`pytest.ini` 增加 `addopts`
  `--cov=app --cov-fail-under=25`，本地与 CI 门槛一致。
- 实测基线：**147 passed，总覆盖率 41%**。弱覆盖重灾区登记进 `BASELINE.md §17`：
  `api/v1/data.py` 13%、`api/v1/data_standard_v2.py` 14%（仅 stats 有测试）、`ws.py` 14%、
  `spatial_query.py` 0%、`quality/rule_engine.py` 0%。
- 新增 `scripts/smoke_convert.py` 全链路冒烟：健康检查 → `/api/v1/jobs` → `/api/v1/convert`
  → 轮询终态 → 硬删除清理；`--admin-key` / `VCS_SMOKE_ADMIN_KEY` 传参，退出码 0/1。
  **本机实测 EXIT=0**（job 进入 failed 终态 = 链路打通；sandbox 缺 GDAL 属既有环境问题）。
- CI 新增 `smoke` job：`docker compose up --build --wait` 起 api+worker+postgres+redis 后跑
  冒烟脚本。（本地无 Docker，CI harness 未在本机执行，已按脚本逻辑与实测连通性对齐。）

### ③ 前端可维护性：脚本外置 + E2E 入库（P2）
- `index.html` 内联主脚本（原 1-index 1593–5844）整体抽出为 `app/frontend/app.js`
  （228KB）；`index.html` 由 **5846 → 1594 行**。`<script src="app.js" defer>` 挂载，
  静态服务验证 app.js 200，Playwright E2E **ALL OK**。
- E2E 从临时目录入库为 `tests/e2e/dashboard_kanban.py`（参数化 `--base/--admin-key` /
  `VCS_E2E_*`，自动建 key → 造 3 任务 → UI 断言 → 清理）；附 `tests/e2e/README.md`。

### ④ 路由拆分：经评估转向「先补测试后拆分」（P2c，见 ADR-0001）
- 评估 `data.py`(2357 行) / `data_standard_v2.py`(3645 行) 的拆分可行性，发现路由级覆盖仅
  13%/14%（其余 50+ 路由零测试），且存在路径注册顺序依赖 + 测试直接导入私有辅助函数。
- 决策：无安全网不盲拆（ADR-0001）；本轮交付 ADR + 技术债追踪（BASELINE §17），等路由级
  测试提上来后按小节分批拆分。前端外置与 E2E 已达成当前可验证的可维护性提升。

### 验证
- `pytest` 147 passed（含覆盖率门槛 25%）
- `scripts/smoke_convert.py` EXIT=0（链路打通）
- `tests/e2e/dashboard_kanban.py` **ALL OK**（默认落地/四维统计/列头全选/无 console 错误）
- 服务器重启后 OpenAPI `version: 2.16.0`，`/app/app.js` 200

*版本号同步*：BASELINE.md / CONTEXT.md / deploy_linux.sh / deploy_windows.bat / restart.bat /
app/main.py 均已升至 v2.16.0。
---

## v2.15.0 — 任务看板汇总概览增强（数据/图层/标准四维统计）+ Kanban 列头批量全选 (2026-09-02)

**类型**: 功能迭代（前端看板 + 后端新增标准聚合统计端点）

### 背景
用户反馈「任务看板有点空」（原概览仅任务统计）且「管理页面没有批量勾选删除的功能」。
排查确认批量删除功能在 v2.14.0 已上线（浏览器缓存旧页导致误判），本轮做两件事：
① 概览页加入 **数据 / 图层 / 标准** 统计维度并设为默认落地页；② Kanban 增加**列头全选**增强批量删除的可发现性。

### 后端改动
- **`app/api/v1/data_standard_v2.py`**：新增 **`GET /api/v1/data-standards-v2/stats`**（注册在 `/data-standards-v2/{standard_id}` 之前，避免被 str 路由吞掉）：
  - 基于 `api_key.scope_clause()` 的 `std_ids` 子查询做 scope 隔离；返回 `DataStandardV2Stats`。
  - `standards_total` / `by_category`（national/industry/custom）/ `by_status`（draft/published/archived）/ `layers_total` / `tables_total` / `fields_total` / `global_dicts`（scope='global' 且不绑定标准）/ `standard_dicts` / `dict_items_total`（全局 ∪ 标准级字典条目）/ `versions_total`（StandardVersion 延迟导入自 `app.models.source_data`）。
- **`app/schemas/data_standard_v2.py`**：新增 `DataStandardV2Stats` 响应模型。

### 前端改动（`app/frontend/index.html`）
1. **默认落地页改为「任务看板 → 汇总概览」**：Bootstrap 由 `goPage('upload')` 改为 `goPage('dashboard')`，导航默认高亮同步。
2. **汇总概览四维统计**：
   - 📥 任务统计（4 卡 + 状态环形图 + 输出格式柱状图，沿用 v2.14 图表）；
   - 📂 数据统计（存储文件数/总大小/要素总数/已关联标准 4 卡 + 数据格式分布 + 图层要素分布（点/线/面）柱状图，数据源 `GET /data/stats`）；
   - 📋 标准统计（标准数/层组数/表数/字段数 4 卡 + 标准分类分布 + 标准状态分布柱状图，数据源 `GET /data-standards-v2/stats`；状态面板 footer 常驻显示字典（全局/标准级）/字典项/版本快照计数）。
3. `loadDash` 改为 `Promise.allSettled` 并行拉取三类数据（各自独立容错，单面板失败不影响整体）；新增 `fmtSize` / `toBarsHtml` / `renderDataStats` / `renderStdStats` 辅助函数；新增 `.dash-sec-title` 分节标题样式。
4. **Kanban 列头批量全选**：每列标题栏左侧新增复选框（`.kb-selcol`），勾选 = 选中该列当前筛选结果、取消 = 清除；`kbSyncColSel` 维护 全选/半选(indeterminate)/未选 三态；复用底部批量条（批量取消/批量删除/取消选择）。

### 新增测试（tests/test_api.py）
`TestStandardV2Stats`（2 例）：API 驱动建标准+层组+表+字段+版本+标准级/全局字典+字典项后断言各计数；第二例验证**跨 Key scope 隔离**（其他 Key 的标准不计入）。全量 **37 passed**。

### 端到端验证（Playwright，真实后端）
默认落地 = 任务看板概览 ✓；任务 3 单（3 卡/环形图/格式柱状图）✓；数据/图层/标准三类统计卡与空态面板渲染 ✓；标准状态面板字典 footer ✓；Kanban 3 卡 + 5 列头全选 ✓；列头全选 → 3 卡全选 + 批量条计数 3 ✓；取消一张 → 列头半选 ✓；取消选择 → 批量条隐藏 ✓；无控制台错误 ✓。验证用密钥与任务已清理（未终态任务亦在终态后补删）。

### 调试修复
- `loadDash` 新统计请求路径漏写 `/api/v1` 前缀（`S.base` 为 `http://127.0.0.1:45679`）→ 404 导致面板空白；修正为完整路径并并行容错。
- 后台转换任务在本地 sandbox 环境因 ogr2ogr 依赖问题终态为 failed（既有问题，非本版引入）；E2E 因此按实际卡片所在列动态定位列头全选，验证客户端交互不受影响。

*版本号同步*：BASELINE.md / CONTEXT.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.15.0。

### P1 · `data_standard_v2.py` 拆包完成（2026-09-04）

**类型**: 架构重构（单文件拆分，无功能变更）

- **`app/api/v1/data_standard_v2.py`（3645 行 / 54 路由）拆为 `app/api/v1/data_standard_v2/` 包**，10 子模块：
  | 子模块 | 路由 | 职责 |
  |--------|------|------|
  | `_common.py` | — | 共享工具/常量/reload/validate_context_* |
  | `standards.py` | 6 | 标准 CRUD + stats |
  | `layers.py` | 7 | 层组 CRUD + move/copy |
  | `tables.py` | 8 | 数据表 CRUD + attach/detach |
  | `fields.py` | 5 | 字段 CRUD + reorder |
  | `dictionaries.py` | 10 | 字典 CRUD + 条目 + share |
  | `field_links.py` | 4 | 字段-字典关联 CRUD |
  | `versions.py` | 5 | 版本对比/回滚/publish + snapshot |
  | `import_export.py` | 8 | 导入/导出/模板 |
  | `__init__.py` | — | 路由聚合，注册顺序与原文件一致 |

- **关键约束**: 静态路径 (`/stats` `/import` `/export` `/template`) 先于 `/{standard_id}`，与原单文件一致。`router.py` 无需改动。
- **修复拆包 bug**: `import_export.py` 补 `selectinload` / `Path` 导入（原单文件顶层 import 隐式提供）。
- **回归测试**: 238 passed / 4 失败（均为 pyodbc 预存缺失）/ **46.88%** 覆盖率。
- `data_standard_v2.py.bak` 已删除。

### P2 · ws.py 覆盖提升 + data.py upload 测试 (2026-09-04)

**类型**: 测试覆盖（P2a ws.py + P2b data.py upload）

#### P2a · `ws.py` 覆盖 80% → 96%
- 新增 8 条测试覆盖：auth exception（97-100）、Redis unavailable loop iteration（118-119）、batch_job_id channel（126）、no-channels error（129-134）、heartbeat body（148-153）、message forwarding exception（162-163）、WebSocketDisconnect（167-168）、generic exception（169-170）。
- 修复：`test_ws_flow.py` 添加 `from fastapi import status` 和 `from app.config import settings` 导入。

#### P2b · `data.py` upload 路由级测试
- 新增 `tests/test_data_upload.py`（9 例）：CSV 上传创建记录 + description、GeoJSON mock loader、空 ZIP / 含 CSV 的 ZIP、空文件报错、去重返回已有记录、auto_create_standard 自动建标准、link_to_existing_layer_group 关联图层组。
- 覆盖率：`data.py` **18% → 25%**（upload 块 291-847 已部分覆盖）。
- 关键修复：mock `extract_features_from_bytes` + `write_features_batch`（fiona 边界层）；`db_session.commit()` 解决跨 session 事务隔离问题。

#### 回归结果
255 passed / 4 失败（pyodbc 预存）/ 总覆盖率 **49%**。

---

### P3 · real-fiona 上传沙箱 + ws.py 100% + 静默吞异常审计 (2026-09-04)

**类型**: 测试覆盖（P2c data.py upload 沙箱 + ws.py 异常分支）+ 代码健壮性（except 审计）

#### P2c · `data.py` upload 真实 fiona 沙箱测试
- 新增 `tests/test_data_upload_p2c.py`（18 例，全部通过）：真实 GeoJSON 上传建空间要素、standard_id 关联、geometry mismatch 400、standard table 自动匹配、upload check hash 命中/未命中、chunk upload/merge、overwrite-by-name 删旧、ZIP 含 GeoJSON 解析子系统、ZIP 仅 CSV 不触发空间子任务、空文件名 422、auto_create_standard geometry override、reindex no-file-data 410 / 404 / with-file-data 触发加载。
- 覆盖率：`data.py` **25% → 34%**。
- 关键洞察/修复：
  - `detect_format` for GeoJSON 返回 `data_type='unknown'`（非 `'geometry'`）——正确系统行为
  - merge 端点返回 **200**（无 `status_code=201`）
  - `db_session.get()` 返回 ORM 缓存陈旧对象 → 用 `select()` 查询验证删除
  - `_load_all_features_to_spatial_async` 内部创建独立子会话，与 `_TestSession` patch 存在事件循环竞争 → mock 该函数验证 reindex 主流程

#### P2c · `ws.py` 100% 异常分支
- 新增 `test_redis_unavailable_loop_exception`（触发 118-119 except）与 `test_heartbeat_exception_breaks`（触发 152-153 except）→ `ws.py` 96% → **100%**（全套件内无逻辑遗漏；独立运行时结构行 23-28/45-53 需真实 Redis 触发）

#### P3 · 静默吞异常审计
- 审计 `app/` 全部 **70 处** `except Exception` → **11 处静默吞异常（无日志）全部修复**。
- 高优先（数据正确性）：
  - `crs_transform.py`：CRS 变换失败静默返回原坐标 → 数据可能错位
  - `feature_store.py` CRS transform/geometry repair：批量操作丢要素无痕迹
  - `transform_engine.py`：批量坐标变换丢要素无痕迹 + proj4 查询失败
- 中优先：`core/cache.py` GET/SET、`core/deps.py` apikey 缓存、`ws.py` 消息转发、`spatial.py` CRS 注入、`topology_checker.py` irregularity、`attribute_processing.py` CSV 读取
- 补 logger 到 5 文件：deps/attribute_processing/crs_transform/feature_store/transform_engine
- 其余 59 处为 SAFE（已重抛/已日志）或 ACCEPTABLE（优雅降级）——保持原样

#### 回归结果
273 passed / 4 失败（pyodbc 预存）/ 总覆盖率 **51%**。

---

### P4 · 纯 Python 0% 文件全清（obj_parser/scoring/geometry_repair/progress_tracker）(2026-09-07)

**类型**: 测试覆盖（消灭剩余无 GDAL 依赖的 0%/低覆盖服务文件）

- 新增 `tests/test_obj_parser.py`（12 例）：**`obj_parser.py` 0% → 100%**
  - OBJ 顶点/面/组解析、`v`/`v//vn`/`v/vt` 三种面索引格式、无 `o` 时默认对象命名、畸形行（缺 z/非数字/vn/vt 非法/空 f/o/usemtl）跳过、字节输入 + 控制字符、文件不存在返回空、`obj_to_geojson_features` 质心 Point 输出、tempfile 清理
  - 洞察：空 `o` 语句仍会创建默认命名对象（`object_N`），不忽略
- 新增 `tests/test_scoring.py`（16 例）：**`quality/scoring.py` 0% → 100%**
  - `_clamp` 边界、加权总分、时限 30 天封顶、自定义权重、负比率钳制、`score_color` 0.85/0.6 阈值、`round()` 浮点四舍五入语义（0.87655 → 0.8765）
- 新增 `tests/test_geometry_repair.py`（22 例）：**`geometry_repair.py` 18% → 100%**
  - AUTO_FIX/ISOLATE/DROP/RAISE 四策略、自相交 bowtie 自动修复、无几何要素保留/丢弃、shapely 缺失回退、**buffer(0) 与 make_valid 双异常回退**（monkeypatch `_BoomGeom` 确定性触发）、**make_valid 成功路径**（buffer 产出无效时回退）、外环 CCW/内环 CW 右手定则修正、MultiPolygon 带洞、要素计数比对
  - 实证：shapely 2.1.2 中 `buffer(0)` 对 NaN 坐标不抛异常、`make_valid` 抛 GEOSException；`_ring_is_clockwise` 用 Σ(Δx)(y2+y1) 面积公式，>0 判顺时针
- 新增 `tests/test_progress_tracker.py`（12 例）：**`progress_tracker.py` 19% → 94%**
  - Redis import 失败（monkeypatch `__import__`）→ 返回 None + 警告日志；`publish_progress` 单/双通道（job+batch 双发布、payload 一致）；批量更新；Redis 连接异常日志；`subscribe_job_progress` 无 Redis 时 yield error 帧并正常结束（第二次迭代 StopAsyncIteration）；消息帧转发 + `finally` 清理（unsubscribe/close）；GeneratorExit 提前关闭清理；listen() 异常向上传播但仍清理

#### 回归结果
**335 passed** / 4 失败（pyodbc 预存）/ 总覆盖率 **54%**（+3%）。纯 Python 服务文件 0% 清零：
`obj_parser` 100%、`scoring` 100%、`geometry_repair` 100%、`progress_tracker` 94%（仅剩真实 Redis 连接 19-22 行）。

---

### P5 · topology_checker 100% + T2 自相交死代码 bug 修复（2026-09-07）

**类型**: 测试覆盖 + Bug 修复（拓扑检查核心规则全清 + 修复静默失效的 T2 规则）

- 新增 `tests/test_topology_checker.py`（55 例）：**`topology_checker.py` 13% → 100%**（340 语句全清）
  - 米制量算策略 `resolve_metric_strategy`：native（原生米制投影直算）/ planar（无 CRS/越界兜底）/ utm（地理系按 bbox 动态选带：中心经度 → zone = int((lon+180)//6)+1，南半球加 32000）；实测样本 SRID 4326→32649、3857-ft→32721、南半球 32750、别区 328501 越界钳制 32660/32601；`get_srid_is_geographic` monkeypatch（monkeypatch 目标是 `app.services.custom_crs` 模块函数，代码内按调用点惰性导入，故 patch 生效）
  - `_make_result`/`_summarize`：`by_severity` 仅统计 fail 状态结果；`detail=` kwargs 直接进入 `TopologyCheckResult.detail`（对象嵌套语义）
  - 全 13 条规则：T1 空几何（3 模型合计）、T2 自相交、T3 极大面、T4 极小面、T5 微短线、T6 面重叠、T7 点重叠、T8 线重叠、T9 面裂隙、T10 线连通性（悬挂/通过/要素不足三态）、T11 不规则面（凹面凸度 <0.3 + 边数 <3 + 空几何 continue + 畸形 WKT 跳过）、T12 环方向、T13 多部件几何
  - 容错：6 种单规则 SQL 异常（max_area/min_area/polygon_overlap/point_overlap/line_overlap/polygon_gap）→ error 结果 + 事务回滚、不拖垮整次检查
  - 图层圈定双路径：原生 SQL `= ANY(:layers)`（T6/T7/T8/T9/T12）+ ORM `.where(model.layer_name.in_(...))`（T1/T2/T3/T4/T5/T10/T11/T13）；`config=None` 建默认配置；无效 geometry_type 优雅空结果
  - 测试技巧：`FakeTopologyDB` 按 SQL 子串分发 handler（顺序即优先级）；SQLAlchemy ORM 查询 `literal_binds` 编译成 SQL 字符串再匹配；为防止子串误匹配需注意 handler 顺序——`ST_Intersection` 必须在 `ST_Length` 之前（T8 的 `ST_Length(ST_Intersection(...))`）、`ST_UNION` 在 `ST_IsEmpty` 之前（T9 的 `NOT ST_IsEmpty(gap_geom)`）、`CASE WHEN ST_Area` 在 `ST_IsEmpty` 之前（T12）
- **Bug 修复（T2 自相交死代码）**：`run_topology_checks` 自相交循环守卫条件
  `if gtype not in active_rules and gtype not in [r.rule_code for r in results]: continue` **恒为 True**——
  `gtype` 是 `"line"/"polygon"`（数据模型名），`active_rules` 是规则名（`"self_intersection"` 等），
  `results` 的 `rule_code` 是 `"T01"..."T13"`，二者永不相交 → 循环体从不执行，T2 查询从未发出，
  自相交检测规则从 v2.x 起一直是死代码。TDD 红用例（`test_t2_self_intersection_both_types`）先行证明
  （期望 2 条结果实测 0 条）→ 改为 `if gtype not in check_models: continue` 后修复。
- 平台经验：pytest-asyncio 1.4.0 为 Mode.STRICT，异步测试必须显式 `@pytest.mark.asyncio`；
  凹面凸度样例需实测（初始 L 形凹面凸度 0.345 反超阈值 0.3 未命中，改用 CROSS_WKT 凸度 0.28 实算命中）

#### 回归结果
**355 passed**（未计入 pyodbc 依赖的 4 例 MDB 测试，预存失败与本次无关）/ 总覆盖率 **57%**（+3%）。
随 P5 完成后可覆盖清单：`topology_checker` 100%（340 语句）。

### P5b · 服务/workers 全覆盖：script_generation/quality_exporter/batch_tasks/tasks/beat/celery（2026-09-07）

**类型**: 测试覆盖 + Bug 修复（作业编排/导出/定时任务链路全清）

- 新增 `tests/test_script_generation.py`（45 例）：**`script_generation.py` 20% → 99%**（134 语句，239/265 为结构性不可达防御分支留档：Python `BoolOp` 仅 And/Or，`_safe_to_js` 对 Call/Attribute/Subscript 必先抛 ValueError 故 `ast.walk` 二次确认无法触发）
  - **修复潜在 bug**：`generate_fiona_script` 带 `select_fields` 时抛 `NameError: json is not defined`——模块顶层未 `import json`（`json.dumps` 只在函数内使用，生产路径只要选字段导出即崩溃）
  - `_safe_where_to_js` 边界：`type="A"`→`(props.get("type") == 'A')`、`name in [...]`→ IN 元组、三元链/属性访问/下标/`is`/`not`/二元算术/UNICODE→None、AND/OR 连接带首尾空格
  - `sandbox_execute`：临时文件用 `patch("tempfile.gettempdir")` 验证清理（`Path` 是函数内导入，patch 模块属性无效）
- 新增 `tests/test_quality_exporter.py`（23 例）：**`quality_exporter.py` 8% → 100%**
  - `_fetch_violation_geometry`：空索引短路/三表轮询/首胜保留/非法 JSON→None/`json.loads(123)` 走 TypeError 分支（注意：bytes 无效编码抛 `UnicodeDecodeError` 不在 `(JSONDecodeError, TypeError)` 捕获集）
  - GeoJSON 导出：拓扑索引逐个 Feature + 无索引汇总、属性 samples 去重、旧版 issues、几何获取异常→日志降级（geometry=None）、非 dict/非 int 跳过；Excel：表头/三类行/font/首 20 索引豁免
- 新增 `tests/test_batch_tasks.py`（7 例）：**`batch_tasks.py` 15% → 100%**
  - `orchestrate_batch`：全路径 SUCCESS（success_count/finished_at/两次 publish running+success）/PARTIAL/全败 FAILED（"所有子任务均失败"）/批量不存在早退/apply_async 或 get 抛异常→FAILED+仅 running 发布
  - 关键坑：`create_engine`/`sessionmaker`/`publish_batch_update` 均为**函数内导入**（模块级无此属性）→ 必须 patch `sqlalchemy.create_engine`/`sqlalchemy.orm.sessionmaker`/`app.services.progress_tracker.publish_batch_update`
- 新增 `tests/test_tasks_worker.py`（27 例）：**`tasks.py` 25% → 100%**
  - `run_conversion` 5 步流水线：格式识别（低置信度警告）/CRS/属性（dbf 缺失跳过、异常跳过）/GeoJSON 几何修复（count 不匹配警告、异常跳过）/脚本生成（fiona vs ogr2ogr）+ 沙箱预执行（失败警告）+ 完整执行 + 成功终态（6→5 步 publish：GeoJSON 无 dbf 时属性步不发）
  - 错误语义：`_get_job` 在 try 之外→ValueError 直抛不标记 FAILED；try 内异常→FAILED + retry（`self.request.retries < max_retries` 时调 `self.retry(exc)`，通过 `patch.object(T.run_conversion, "retry")` stub；`push_request(retries=99)` 控制 max_retries 分支→直接 raise exc）
  - 辅助函数：`_run_full_conversion` 两分支（ogr2ogr subprocess/Fiona 脚本临时文件 finally 清理）、`_find_attr_file`、`_load_geojson_features`（FC/单 Feature/其他/坏 JSON）、`_get_job`、模块级引擎配置断言
- 新增 `tests/test_beat_schedule.py`（11 例）：**`celery_app.py` 74% → 100%**、**`beat_schedule.py` 32% → 95%**（43-44 内层 except 仅 stat 竞态可达留档）
  - Redis 协议补丁：kombu `_connparams` 与 `RedisBackend.__init__` 用桩函数作为"原始实现"验证注入 `protocol=2` 行为；两个 except 分支用"删模块属性 + `sys.modules[name]=None`"触发 `ImportError`（`__import__` patch 因 from-import 走属性短路而无效）
  - **修复真实 bug**：Python 3.12 `Path.is_dir()` 只吞 `ENOENT/ENOTDIR/EBADF`，EACCES（权限受限目录）会重新抛出——`cleanup_temp_files` 原代码仅包住第二次 `stat()` 与 `rmtree`，未包 `is_dir()`，遇权限目录整个定时任务崩溃 → 外层加 `try/except OSError: continue`
- 平台经验：`unittest.mock` 的 Mock **不是描述符**，`patch(cls.attr, side_effect=fn)` 不会绑定实例（self 缺失）；需 `patch.object(cls, "attr", new=fn)` 传入普通函数才能走描述符绑定
- 全量回归：**434 passed**（4 例 pyodbc 预存失败除外）/ 总覆盖率 57%（10176 语句，missed 4380）

### P5c · quality.py 路由级 99% + 覆盖率测量修正（greenlet 并发追踪）（2026-09-07）

**类型**: 测试覆盖（路由层安全网）+ 测量基础设施修复

- **📐 测量修正（重要）**: 新增 `.coveragerc` 启用 `[run] concurrency = greenlet,thread`
  - 根因：SQLAlchemy async（asyncpg）在 `greenlet_spawn` 中切换执行，默认 coverage **不追踪 greenlet 切换后的执行体**，导致 async 路由 handler 的实际代码行完全不统计
  - 证据：同一 handler 测试，quality.py 实测 18% → 28%（启用后）；full 套件 data.py 34%→59%（真值）
  - 影响：全量覆盖从旧口径 57%（missed 4380）→ 新口径 **68.24%**（missed 3232），两者不可直接比较；pytest.ini 的 `--cov-fail-under=25` 门槛远低于新值，CI 仍安全
- 新增 `tests/test_quality_routes.py`（27 例）：**`quality.py` 27% → 99%**（269 语句）
  - `POST /quality/check`：有要素文件 200（含 QualityReport 落库 + report.html/json 落地路径断言）、越权/不存在 404、feature_count=0 且分表无实际要素 → 400 拒检（防空报告满分）、他人文件 404、topology_config 自定义 enabled_rules/tolerances 解析
  - `POST /quality/check/upload`：新 orchestrator（data_standard_id → `run_quality_check` 打桩）200 + topology_config JSON 解析；orchestrator 抛异常 → 回退旧接口 → 标准不可达（load_standard_fields 权限校验返 None）→ 404；未指定标准 → 真实 `check_quality` 回退 200；坏 topology JSON → 400
  - `GET /quality/{id}`：违规样本派生（int→feature_index / 其它→sample 语义）、严重度分布兜底、违规总数、pass/skip 状态行跳过、无违规空结果、404
  - `GET /quality/{id}/html`：即时生成含五维 SVG 仪表、全零分触发红色阈值 + sev-ok、pass 行跳过样本循环、404
  - `GET /quality/{id}/json`：附件头下载、无 json_report_path → 404
  - `GET /quality`：倒序 + 分页 + issues_count/五维评分
  - `GET /quality/{id}/export`：GeoJSON（打桩 `export_violations_geojson`，Content-Disposition 头）、Excel（桩函数写真实 xlsx）、404
  - `DELETE` 单条 / `batch-delete` / `delete-all`（scope 隔离断言）
  - 留档 3 行：108-109 S4 放行（feature_count=0 但分表有要素 → 需建 LIST 物理分区造数，成本过高）、245 上传空文件名（FastAPI `File(...)` 422 门禁先拦截，不可达）、840 batch-delete 空列表（`Form(...)` 必填，不可达）
- 平台经验：
  - **测试 Key 必须 `await db_session.commit()`**：`_create_key` 仅 `flush()` 时，`authenticated` 依赖（独立会话）查不到未提交数据 → 请求 401（首版 4 个用例踩坑）
  - upload 新 orchestrator 路径会写临时 StoredFile：其 `standard_id` 有 FK → 必须造真实 DataStandard 行，不能传假 id
  - 详情/HTML 端点会对**所有** dict 行做 `CheckItemResult(**r)` 反序列化（含 pass 行）→ 造数需满足 schema 全必填字段（rule_code/rule_name/status/severity），否则 500
  - `isinstance(s, int)` 布尔注意：`True` 也是 int（样本语义仅影响展示，不影响判定逻辑）
- 全量回归：**530 passed**（4 例 pyodbc 预存失败除外：test_mdb_processor 1 + integration 3）/ 总覆盖率 **68.24%**（10176 语句，missed 3232，新测量口径）

### P5d · batch.py 路由级 100%（2026-09-07）

**类型**: 测试覆盖（批量转换路由全清）

- 新增 `tests/test_batch_routes.py`（17 例）：**`batch.py` 30% → 100%**（106 语句全清）
  - `POST /batch/convert`：Redis 可用 → `run_conversion.apply_async(task_id=jid)` 并行提交（202 + BatchJob/ConversionJob 落库 + 2 次提交 + task_id 与子任务 id 一一对应）；Redis 不可用 → 降级进程内 `apply(args=[jid], task_id=jid)`（`max_retries` 先置 0 后恢复原值，message 含「已进程内执行」）；降级中 `apply` 抛异常 → 内部 except 捕获仍 202；超过 `settings.max_batch_size` → 413
  - `GET /batch/{id}`：详情（含 success_count/duration_seconds 字段）、404、越权 404（scope 隔离）
  - `GET /batch`：created_at 倒序（需显式设置不同 created_at，同事务同毫秒排序不稳定）、分页 page/page_size
  - `DELETE /batch/{id}`：RUNNING 且带 celery_task_id 子任务 → `celery_app.control.revoke(task_id, terminate=True)` + batch/子任务全量 CANCELLED；非 RUNNING 或无 task_id → 不 revoke；404
  - `GET /batch/{id}/jobs`：子任务列表（asc 排序）、空列表
  - `_redis_available`：ping=True→True、ping 抛异常→False、`import redis` 失败→False（桩 `redis.from_url` / `builtins.__import__`）
- 平台经验：
  - 批次提交写真实磁盘：`patch.object(settings, "upload_dir", str(tmp_path))` 隔离；`settings.output_dir` 只拼字符串不写盘，无需处理
  - **`Task.apply(args=..., task_id=...)` 的 `args` 是关键字参数**：mock 记录在 `call_args.kwargs["args"]`（首个位置参数为空），不是 `.args[0]`
  - **先 `flush()` 生成 batch.id 再挂子任务**：`_make_child(batch_id=batch.id)` 在 flush 前 batch.id 为 None → 子任务 `batch_job_id` 存 NULL → 列表/取消查询全部落空（3 个用例同源踩坑）
  - **expire_on_commit=False 下 identity map 缓存旧状态**：路由用独立会话把 status 改 CANCELLED 后，测试会话 `get()` 命中缓存返回旧对象 → 须 `await db_session.refresh(batch/child)` 再断言（探针确认 revoke 本身正常、路由响应 204）
  - `revoke` 打桩：`patch("app.workers.celery_app.celery_app", new=MagicMock())`（函数内 `from … import celery_app` 每次从模块属性取，patch 生效；若直接 patch 目标方法可能打不到）
- 全量回归：**547 passed**（+17，4 例 pyodbc 预存失败除外）/ 总覆盖率 **68.99%**（10176 语句，missed 3156）

---

### P5e · spatial.py 路由级 100%（2026-09-07）

**类型**: 测试覆盖（空间转换/目录/下载/修复路由全清）

- 新增 `tests/test_spatial_routes.py`（30 例）：**`spatial.py` 29% → 100%**（188 语句全清，含防御兜底可达性证明）
  - `GET /spatial/crs/catalog`：8 大分组（地理坐标系/自定义坐标系/常用/3°&6°投影带/UTM）由真实 `spatial_ref_sys` 驱动，zone 命名格式断言（4513→Zone 25/4491→Zone 13/2401→Zone 25/2349→Zone 25/2327→Zone 13/32651→Zone 51N）；自定义坐标系 resolve 异常 → 回退静态 srid 分组；**"其他"兜底可达性**：临时 UPDATE 32653 srtext 为异常值触发兜底，finally 恢复原值（若进程中断会残留脏数据，属测试环境可接受）
  - `POST /spatial/{file_id}/transform`：成功 → TransformResult 落库 + output_data 注入 crs member（EPSG URN）；自定义 SRID ≥910000 → `urn:ogc:def:crs:VCS-CUSTOM::{srid}`；源 CRS 解析失败 → warnings 降级仍 200；无要素 → 400「该文件无可转换的几何要素」+ failed 明细；404 / 越权 404 / 虚拟管理密钥 `00000000-…-0001`（startswith "00000000"）→ api_key_id 置 NULL
  - `_inject_crs_member` 单元：空 payload / 已有 std crs 直通 / 坏 URN 矫正为 VCS-CUSTOM / 坏 JSON 返回原值
  - 列表：total 统计 + items 不含 output_data 大字段、page/page_size 分页、scope 隔离
  - 下载：geojson → `Content-Disposition` 附件名 `{filename}_EPSG:4326_to_EPSG:4490.geojson`；shp/gpkg 桩为**同步函数**（验证 `_run_sync` executor 内调用）且 target_wkt 从 spatial_ref_sys(4490 srtext) 实取；自定义 SRID 库无行 → wkt None 且**跳过 pyproj 兜底**（动态挑不在库的 ≥910000 srid，避免与 catalog 测试真实注册冲突）；未知 srid 99999 → pyproj 抛异常 wkt None；坏 format 400；404/410
  - 删除：204 后用 `select(func.count())` 直读库断言（绕开 identity map 缓存）；404 / 越权 404
  - `POST /spatial/{file_id}/repair`：`repair_features_by_file` 落库 + feature_count 回写 / 404
- **修复静默失效（第 3 处，同 P3/P5b 模式）**：`_categorize` 的 WGS UTM 分支——
  ```python
  r = raw.lower()
  m = re.search(r"zone (\d+)([NS])", r)  # r 已小写，[NS] 永不可匹配 → 死代码
  ```
  全部 UTM 条目（32643-32653）静默落入「其他」组，`WGS 84 UTM` 分组恒空、前端下拉缺失。修正为 `[ns]` + 显示 `ns.upper()`。**TDD 红用例（断言 32651 ∈ WGS 84 UTM 组）先行暴露**，修改 1 行后 8 组全绿。这是 P5 系列第 3 个「无测试覆盖→逻辑静默失效」实证，直接支持 ADR-0001 拆分前先补测试的决策。
- 平台经验：
  - **嵌套 helper 不可直接 import**：`_categorize` 定义在 `crs_catalog` 内部 → 不能 `from app.api.v1.spatial import _categorize`（ImportError），改由真实库查询驱动外部行为覆盖全分支
  - **catalog 未打桩时 resolve_transform_crs 会真实把自定义 SRID 注册进 spatial_ref_sys** → 后续测试若断言「库无该行」须先查库动态选 srid（硬编码 918984 首次跑挂）
  - **`_run_sync` 把同步函数丢进 executor 调用** → 桩必须用同步 `MagicMock(return_value=bytes)`；AsyncMock 的返回 coroutine 直接泄漏到 `Response(content=…)` → `AttributeError: 'coroutine' object has no attribute 'encode'`
  - **identity map 缓存已删对象**：路由（独立会话）删除后测试会话 `get()` 仍命中缓存 → 用 `select(func.count())` 从库直读；`expire_all()+get()` 会触发过期刷新报 `MissingGreenlet`
  - 函数内 `from x import y` 打桩统一 patch 源模块属性（transform_engine / export_service）
- 全量回归：**577 passed**（+30，4 例 pyodbc 预存失败除外）/ 总覆盖率 **70.92%**

---

## v2.14.0 — 任务看板重做（汇总概览 + Kanban 任务管理）+ 批量硬删除 (2026-09-02)

**类型**: 功能迭代（前端任务看板大改 + 后端新增硬删除端点）

### 背景
任务看板此前仅有简单列表，缺少按状态分组的视觉化管理、可视化统计，且无法真正删除任务
（`DELETE /api/v1/jobs/{job_id}` 仅软取消，置为 CANCELLED，磁盘文件与记录永久留存）。

### 后端改动
- **`app/api/v1/convert.py`**：新增 `_rm_tree()`（递归删除磁盘目录）与 `_hard_delete_job()` 辅助函数；
  新增 **`POST /api/v1/jobs/delete`**（`JobBatchDeleteRequest` / `JobBatchDeleteResponse`）批量硬删除：
  - 仅允许**终态**（success / failed / cancelled）；pending / running 拒绝（理由「运行中的任务不能直接删除，请先取消」），全部被拒返回 400，部分成功返回 200 + 逐条明细。
  - 删除磁盘目录 `uploads/{job_id}`、`outputs/{job_id}` 及其质检报告文件，并删除关联 `QualityReport` 记录（子表 `quality_rule_results` / `quality_violations` 由 DB 外键 CASCADE 清理）。
  - 沿用既有 API Key scope 校验（`api_key.owns()` / `api_key.scope_clause()`）。
- **`app/schemas/conversion.py`**：新增 `JobBatchDeleteRequest`（`job_ids: list[str]`）、`JobDeleteResult`、`JobBatchDeleteResponse`。

### 前端改动（`app/frontend/index.html`）
任务看板页重构为**双 Tab**：
1. **汇总概览（overview）**：统计卡（总数/进行中/成功/失败）+ 原生 SVG **环形图**（状态分布，点击聚焦）+ **柱状图**（输出格式分布，点击按格式过滤）；无任务时显示空态。
2. **任务管理（manage）**：5 列 **Kanban 看板**（pending/running/success/failed/cancelled），卡片含状态色条、格式徽章、进度条、时间、复选框；工具栏支持**关键词搜索 + 8 个格式筛选 chip（全部/GEOJSON/SHP/GPKG/KML/DXF/GML/其他）**；底部**批量操作条**（选中计数 + 批量取消 / 批量删除）；单卡操作取消（pending/running）/ 删除（终态），均带确认弹窗；点击卡片打开详情弹窗。
3. 新增 `DASH` 状态对象（jobs / statusFilter / fmtFilter / sel / chartFocus），`loadDash` 每 6s 轮询，管理页隐藏时不渲染看板；`kbMatch` 统一应用搜索+格式+状态过滤。

### 新增测试（tests/test_api.py）
`test_batch_delete_terminal_jobs`：运行中任务删除被拒、终态任务批量删除成功、列表不再出现；TestConversion + TestAuth 回归 **10 例全过**。

### 端到端验证（Playwright，真实后端）
看板 5 列 / 5 卡片渲染、搜索过滤、格式过滤、列聚焦暗化、批量条显隐、详情弹窗、单项删除（5→4）、批量删除（4→2）均通过；修复汇总图表首次不渲染 bug（`stroke-dasharray` 模板字符串误调 `.toFixed` 抛 TypeError，被 `loadDash` 的 try/catch 吞掉）。

*版本号同步*：BASELINE.md / CONTEXT.md / deploy_linux.sh / deploy_windows.bat / restart.bat / app/main.py 均已升至 v2.14.0。
---

## v2.13.0-bugfix 《转换结果支持按需导出 GeoJSON / SHP(zip) / GeoPackage》(2026-09-01)

**背景**: 概要为「转换/转回自定义坐标系结果投影识别不对」的终版方案。

### 问题
GeoJSON(RFC 7946) 的 `crs` 成员无法原生表达自定义投影坐标系（如 shanghaicity=SRID 918984）。
把 shanghaicity 转换为 GeoJSON 下载后在 QGIS/ArcGIS 无法按自定义投影打开，投影识别不对。

### 方案
下载接口支持**三种格式**：GeoJSON / Shapefile(zip 打包) / GeoPackage。其中 Shapefile 自带
`.prj`(WKT)、GeoPackage 内嵌 CRS 定义，均能**完整保留自定义投影**，通用 GIS 工具直接按正确
投影打开——这是自定义坐标系（shanghaicity）的正解。

### 改动（app/services/export_service.py 新增）
基于 **fiona(GDAL/OGR)** 实时导出 `transform_results.output_data`（GeoJSON FeatureCollection）：
- `export_geojson`: 原样返回（调用方已负责 CRS 声明/纠正）。
- `export_shapefile_zip`: 写 `.shp/.shx/.dbf/.prj/.cpg` 并打包 `.zip`；`.prj` 用从 `spatial_ref_sys` 取的 `srtext`(WKT)，保留自定义投影与纠正后的假北；`.dbf` 用 UTF-8 编码，中文不丢。
- `export_gpkg`: 写单文件 `.gpkg`，内嵌 CRS 定义。
- fiona 为同步库，导出在事件循环外线程池执行，不阻塞 FastAPI。

### 改动（app/api/v1/spatial.py）
- `download_transform_result` 增加 `format` query 参数（geojson/shp/gpkg，默认 geojson）。
- 自定义 SRID(>=910000) 从 `spatial_ref_sys` 取 `srtext` 作为 WKT 传给导出函数。
- 按格式返回正确 `media_type`（geo+json / application/zip / geopackage+sqlite3）与扩展名。

### 改动（app/frontend/index.html）
转换结果列表「下载」旁增加格式下拉（GeoJSON / SHP(ZIP) / GPKG），按需导出。

### 新增测试（tests/test_export_service.py，7 用例）
SHP zip 含带自定义 WKT 的 .prj 且可被 fiona 反读、dbf 中文属性保留、GPKG 合法 SQLite 且带自定义 CRS、
空/非法输出报错、文件名清洗。全量相关测试 **41 例全过**。

### 端到端验证（真实「转回去→shanghaicity」记录，target_srid=918984）
- SHP zip 生成 5 文件；`.prj` 含 `ShanghaiCity` 且含纠正后 `false_northing=-3457147.81`；
  fiona 反读 crs_wkt 正确还原自定义投影，6 要素完整。
- GPKG 为合法 SQLite，内嵌自定义 CRS。
---

## v2.13.0-bugfix — 转换结果 GeoJSON 声明目标 CRS（避免误判 WGS84） (2026-09-01)

**类型**: Bug 修复（转换结果被误识别为 EPSG:4326 / WGS84）

### 背景
用户实测：将上海城建坐标（shanghaicity）转换到 CGCS2000（4490/4519）后，下载的 GeoJSON 被识别为 惈CRS: EPSG:4326（WGS84）惉。据此排查确认结果是“缺少 CRS 声明”而非坐标输出错误（转换自身已通过逐点空间验证、2058 点误差 0 米）。

### 根因
GeoJSON 规范 RFC 7946 规定：FeatureCollection 若省略 惈crs惉 成员，坐标系统一按 **WGS84 (EPSG:4326)** 解释。转换结果写入时构建的 FeatureCollection 仅含 惈type/features惉，未附目标 CRS（如 惈urn:ogc:def:crs:EPSG::4490惉）。下游工具（QGIS/ArcGIS/在线地图）读取时因缺少声明而默认 WGS84/4326。

### 修复（惈app/api/v1/spatial.py惉）
- 新增纯函数 惈_inject_crs_member(payload, target_srid)惉：向转换结果 FeatureCollection 注入 惈crs惉 成员（惈urn:ogc:def:crs:EPSG::<target_srid>惉）；已含或解析失败时原样返回。
- **创建锚点**：惈transform_spatial_features惉 写入 output_data 时包含 CRS 声明。
- **下载兼容**：惈download_transform_result惉 下载时调用 惈_inject_crs_member惉，对历史（保存时未含 crs）结果也自动补上，无需数据迁移。
- 新增单元测试 惈tests/test_transform_result_crs.py惉（惈_inject_crs_member惉，4 用例）。


### 追加修复（自定义 SRID 无效 EPSG URN）
用户实测“转换回去”（EPSG:4490→Custom:shanghaicity, dst_srid=918984）时，下载的抛影系不对：因为内置的 crs 为 惈urn:ogc:def:crs:EPSG::918984惉，而 918984 是自定义 SRID（≥910000），**不在 EPSG 官方库中**，下游 GIS 工具无法解析该无效 EPSG URN。
修复：_inject_crs_member 区分 SRID 类型，并对早期已写入的无效 “urn:ogc:def:crs:EPSG::<custom>” URN 下载时自动纠正为 VCS-CUSTOM 命名空间（不再网过已有 crs 成员）：标准 EPSG（< 910000）用 惈urn:ogc:def:crs:EPSG::<srid>惉；自定义（≥ 910000）用诚实的 惈urn:ogc:def:crs:VCS-CUSTOM::<srid>惉（不再假装成 EPSG 编码）。新增测试 test_custom_srid_uses_vcs_namespace_not_bogus_epsg。

两项校验结果：
- 转换回去相往返精度： shanghaicity→4490→shanghaicity 逐点vs源原始数据，2058 点最大角点误差 **0.0000 米**，0 个要素不匹配。
- 报不降：无。
### 验证
- 对存在转换结果 惈4bb67d68惉 （shanghaicity→​4490）：写入时无 crs、target_srid=4490，下载时注入 惈urn:ogc:def:crs:EPSG::4490惉 正确。
- 新转换在创建时即附 CRS 声明。
- 回归：相关测试合计 34 用例全过。
---

## v2.13.0-bugfix — ZIP 解压 SHP 的 .prj 坐标系识别修复 (2026-08-31)

**类型**: Bug 修复（SRID 回退 4326 问题）

### 背景
用户上传的 shanghaicity（上海城市坐标系，投影米制）SHP 压缩包，自动识别源 CRS 却是 `EPSG:4326`（WGS84 经纬度）。手动选择/已知该数据为 shanghaicity 城建坐标，两者坐标域严重不匹配。

### 根因
不是 CRS 解析算法问题，而是**上传链路没把 `.prj` 喂给 CRS 检测**：

1. `data.py` 的 ZIP 解压循环对每个子文件调用 `_detect_and_populate(sf, content, filename)`，**只把 `.shp` 单文件写入临时文件**，配套 `.prj`（伴生文件）未一并写入。
2. `format_detection._extract_crs` 对 SHP 用 `p.with_suffix('.prj')` 查找 `.prj` —— 临时目录里只有 `.shp`，找不到 `.prj` → 返回 `None`。
3. `sf.detected_crs = None` → `_ingest_srid` → `resolve_ingest_srid_async(None)` 回退到默认 SRID **4326** → 前端显示 `源 CRS: EPSG:4326`。

而真正的 CRS 解析（`resolve_ingest_srid_async`）对 shanghaicity WKT 是**正确**的——会确定性注册自定义 SRID（91xxxxx），绝不会回退 4326。

### 修复
1. **`data.py::_detect_and_populate` 增加 `companion_contents` 参数**：对 SHP 伴生文件（`.dbf/.shx/.prj/.cpg` 等），用同 stem 同目录的临时目录写入，使 `_extract_crs` 能读到 `.prj` 投影 WKT。两个 ZIP 解压 SHP 调用点（`upload_file` 与 `merge_chunks`）均传入 `shp_group.companion_contents`。
2. **前端 `crsLabel` 支持自定义 SRID 显示**：`detected_crs_srid >= 910000`（`custom_crs.CUSTOM_SRID_BASE`）或 `crs_is_custom` 时，不再硬编码显示 `EPSG:913xxx`，而是从 WKT/别名映射为可读中文标签（如 `上海城市坐标系(Beijing54/Krasovsky)`）。
3. **前端 `crsResolvable` 自动回填**：自定义 SRID 时源坐标系输入框不再回填无法解析的 `EPSG:913xxx`，而是回填后端可解析的 `Custom:shanghaicity` 别名或 WKT 原文。
4. **新增回归测试** `tests/test_crs_detection_zip.py`（4 用例）：`.prj` 读取、无 `.prj` 时 None、companion 传递后检测到 `.prj`、无 companion 保持原行为。

### 验证
- `tests/test_crs_detection_zip.py` 4 用例全过；与 `test_transform_engine`/`test_feature_store_transform` 合计 25 用例全过。
- 用真实 Fiona SHP（shanghaicity WKT）做端到端：带 companion → `detected_crs=PROJCS["ShanghaiCity",...]`；不带 → None（复现旧 bug）。
- Playwright 浏览器加载 `/app/` 无 JS 错误，`crsLabel`/`crsResolvable` 等在浏览器全局可用且行为正确。

---

## v2.13.0-bugfix — 坐标转换「假东/假北互换」PRJ 定义纠正 (2026-08-31)

**类型**: Bug 修复（自定义投影坐标系变换结果漂移）

### 背景
用户上传含上海城建坐标（shanghaicity）的样例数据 惈投影转换样例.zip惉 并期望据此优化坐标转换功能。该样例 惈.prj惉 在 惈data.py惉 修复后已能正确识别（惈detected_crs=PROJCS["shanghaicity",...]惉、惈srid=918073惉、惈crs_is_custom=True惉），但用该 惈.prj惉 原文作为源坐标系变换时结果漂移到太平洋（惈lon≈150.9°惉），而用内置 惈Custom:shanghaicity惉 却正确落在上海。经比对确认内置定义与 惈.prj惉 原文的差异：假东/假北互换。

### 根因
真实上海城建 惈.prj惉 声明 惈False_Easting=-3457147.81, False_Northing=0惉（数学上非标准，等价于 惈x_0=-3457147.81, y_0=0惉）；正确定义应为 惈x_0=0, y_0=-3457147.81惉。惈custom_crs._ensure_registered*惉 入库时经 惈_apply_false_origin_fix惉 → 惈_fix_swapped_false_origin惉 已将此纠正并写入 惈spatial_ref_sys惉 的 惈proj4text惉（惈+x_0=0 +y_0=-3457147.81惉）。但 惈transform_engine.resolve_transform_crs惉 对自定义 SRID 的 惈pyproj_crs = 原始文本（WKT/proj4 原文）惉，pyproj 按未纠正的 惈False_Easting=-3457147.81惉 解析，导致变换漂移。

### 修复（惈app/services/transform_engine.py惉）
- 新增 惈_pyproj_crs_for(db, res, fallback)惉：当 惈res.is_custom惉 时从 惈spatial_ref_sys惉 按 SRID 查回注册（纠正后）的 惈proj4text惉 作为 pyproj 权威定义，确保 pyproj 与 PostGIS 惈ST_Transform惉 用完全一致的（纠正后）定义。
- 惈_resolve_generic惉/惈_resolve_custom惉 的 惈is_custom惉 分支改用 惈_pyproj_crs_for惉 取纠正后定义（原文不可用→回退）。
- 新增回归测试 惈tests/test_transform_engine.py::test_false_origin_swap_must_use_corrected_proj4惉。

### 验证
- **修复前**：样例 惈.prj惉 原文变换 6 要素→惈lon≈150.9°惉（漂移太平洋，错）；**修复后**：6 要素全部成功，落在上海 惈(121.281, 30.825)惉，与内置定义差异仅 惈Δlon=0.0002°惉（中央经线 121.2751921 vs 121.275 的微系统差，可接受）。
- 样例 ZIP 的 惈.prj惉 识别（惈srid=918073惉/惈custom=True惉）端到端验证正确。
- 回归：惈test_transform_engine惉/惈test_crs_detection_zip惉/惈test_feature_store_transform惉 合 26 用例全过（惈test_mdb_integration惉 因 pyodbc 缺失为既有环境问题，与本次无关）。
---

## v2.13.0 — 统一坐标变换引擎 + 手动输入坐标系修复 (2026-08-28)

**类型**: 功能重构 + Bug 修复

### 变更内容

1. **新增统一坐标变换引擎**（`app/services/transform_engine.py`）：
   - `ResolvedCRS`：统一解析结果（`srid` 落库/审计 + `pyproj_crs` 交 pyproj 变换），根因是 pyproj 无法识别自定义 SRID 注册号（`EPSG:918984` 报错），故自定义投影坐标系用 **WKT/proj4 原文** 直接可解析。
   - `resolve_transform_crs()`：统一 CRS 解析，复用 `custom_crs.resolve_ingest_srid_async` 权威链路，兼容 `Custom:code`、纯数字（`4326`）、`EPSG:xxxx`、中文别名（`国家2000`）、WKT、proj4、URN；无法解析回退 4326 并标记 `source=fallback-*`。
   - `transform_geometry()`：变换前后坐标域校验（地理源超出 ±180/±90 → 告警「疑为投影误标」；目标地理越界 → 告警），保留 Z 维，深拷贝不污染输入；`always_xy=True` 规避轴序颠倒。
   - `TransformStats`（total/transformed/failed/skipped/warnings）与 `transform_feature_dict/list`、`transform_db_features`（跨三表读取+变换+统计）。
2. **`spatial.py` `transform_spatial_features` 接入统一引擎**：源 CRS 缺省读 `sf.detected_crs`，解析失败（用户显式指定但无法解析）给出告警而非静默；响应新增 `skipped`/`failed`/`warnings` 字段。
3. **修复「手动输入坐标系框无效」根因**：旧 `spatial.py` 用 `crs_transform.resolve_crs` 直接解析，无法处理 `Custom:` 前缀与自定义投影坐标系（如上海地方坐标系），静默回退 WGS84 → 手动输入不生效。现统一走 `resolve_transform_crs`，手动输入可真正参与数学变换。
4. **前端 `index.html`**：`chkAlias()` 手动输入 `shanghaicity`/`shanghai_local`/`Custom:...` 时提示「自定义坐标系识别，将以注册 SRID 参与变换」；`doTx()` 结果展示 `skipped`/`failed` 统计与校验 `warnings`。
5. **修复「坐标转换结果全为 `[Infinity, Infinity]`」关键 bug**（用户实测）：
   - **根因**：数据为 CAD/DXF 投影米制大坐标（如规划范围 `3.4e7`），被当作 WGS84(4326) 地理源变换到 Web Mercator(3857) 时，pyproj 对域外坐标返回 `inf` 且**不抛异常**；旧 `_transform_xy` 无 inf/NaN 检测，把 `[inf, inf]` 当成功写入 → 产出全部 `[Infinity, Infinity]` 的损坏 GeoJSON。
   - **修复**：`transform_geometry` 变换后新增 `_coords_finite()` 校验，结果含非有限值即返回 `None`（计入 `failed`），并给出「源/目标坐标系域不匹配，请核对源坐标系」告警；绝不产出含 inf 的损坏几何。`transform_db_features`/`transform_feature_list` 将此类要素计为 `failed`（而非 `transformed`）。
   - **顺带修复**：`_transform_coord_tree` 对 GeometryCollection 因空 `coordinates` 提前返回、子几何从不被变换的 bug（现先递归子几何）。
   - `spatial.py` 全部要素失败时 400 响应附带可诊断原因（含 warnings 与失败计数提示）。
6. **写回路径收敛**（`feature_store.py`）：无调用者的 `transform_features_by_file` 复用统一引擎（`resolve_transform_crs` + `transform_geometry`），移除重复的裸 pyproj `_transform_coords`/`_transform_xy` 死代码；坐标域失败计入 `failed` 且不写回损坏几何。
7. **新增单元测试** `tests/test_transform_engine.py`（17 用例）+ `tests/test_feature_store_transform.py`（4 用例，共 21 用例全过）：覆盖自定义 CRS WKT 解析/闭环、地理投影判定、Z 维保留、域校验告警、inf/NaN 拒绝（含 GeometryCollection）、批量失败统计、写回契约。

---

## v2.12.1 — MDB 几何解码与 CRS 修正 + 上传回填修复 (2026-08-28)

**类型**: Bug 修复 + 功能增强

### 变更内容

1. **PGeo SHAPE BLOB 解码器**（新增 `app/services/esri_shapebin.py`）:
   - 解析 ESRI Personal Geodatabase 的 `SHAPE` 列 BLOB（type 头 + bbox + parts/points + 可选曲线尾），
     替代错误的 WKB 路径解析
   - 类型 (raw & 0xFF) / flag (raw & 0xFFFFFF00) 校验：拒绝 WKB 伪装体（`0x00000101`）以触发 WKB 兜底
   - 多边形环方向 shoelace 判定（`_ring_is_cw`，CW=外环 / CCW=空洞）：2 环 → Polygon 带洞
     而非 MultiPolygon；无 CW 环时首环作外环，多 CW 环 → MultiPolygon
2. **MDB CRS 提取**（`mdb_processor.py`）:
   - 新增 `_load_crs_map()`：读 `GDB_GeomColumns`（TableName→SRID）× `GDB_SpatialRefs`（SRID→SRTEXT）
   - `crs_srid` / `crs_wkt` 随表数据、`detected_crs`（ESRI WKT）随 stored_file 入库
   - `data.py` MDB 分支接入 `_ingest_srid(sf, db)`：ESRI WKT（无 AUTHORITY）→ 自定义 SRID
     （910000–998999, `AUTH_NAME='VCS-CUSTOM'`，如 967453）→ GK 米制坐标不再误存为 WGS84
3. **`custom_crs.py::_fix_swapped_false_origin` 修复**:
   - 旧启发式把合法的分带 GK false_easting（41,500,000 = 带号×1e6 + 500k）误判为交换的原点，
     注册出 `x_0=0/y_0=41500000`，导致 `ST_Transform` 报 "Point outside of projection domain"
   - 修复：`x0>0 且 (x0-500000)%1000000==0`（分带 GK 形式）时不做交换；上海地方系
     （`x_0=-3,457,147.81`）不受影响
4. **MDB 上传父记录元数据回填**（`data.py` MDB 分支）:
   - 收集 `stats_by_file`，回填子记录 `feature_count`，聚合父记录
     `feature_count` / `layer_count` / `detected_crs` / `geometry_type`
   - 子记录 `file_data` 清空（保留 GeoJSON 字节即足够，原始 MDB 不落地）
   - `db.expire_all()` 后 `await db.refresh(archive_record)`，修复 MissingGreenlet 崩溃
5. **测试**: 新增 `tests/test_esri_shapebin.py`（12 tests，含真实坐标/环方向/WKB 拒绝）；
   集成测试增至 28 项（含 SRID 断言）；全套 67 passed（不含 test_api.py 预存环境问题）

### 新增文件
- `app/services/esri_shapebin.py` — PGeo SHAPE BLOB 解码器
- `tests/test_esri_shapebin.py` — 解码器单元测试 (12 tests)
- `tests/fixtures/mdb/sample_migration.mdb` — 真实 MDB 样本（GDB_GeomColumns/SpatialRefs 齐备）
- `tests/_e2e_upload_check.py` — 端到端上传验证脚本（vcs_test，幂等清理；断言 SRID≥910000 且质心落上海）

### 版本追踪更新
- `BASELINE.md` — v2.12.1
- `CONTEXT.md` — v2.12.1
- `deploy_linux.sh` — v2.12.1
- `deploy_windows.bat` — v2.12.1
- `restart.bat` — v2.12.1
- `app/main.py` — version="2.12.1"

---

## v2.12.0 — MDB (ESRI Personal Geodatabase) 支持 (2026-08-27)

**类型**: 新功能

### 变更内容

1. **MDB 格式识别**: `format_detection.py` 新增 MDB 识别，通过 Magic Bytes (`Standard Jet DB`) + 扩展名 (`.mdb`) 检测，分类为空间格式（`data_type='spatial'`）
2. **MDB 处理器**: 新建 `mdb_processor.py`，使用 pyodbc + MS Access Driver (ODBC) 读取 `.mdb` 文件中的空间表
   - 支持 WKB 和 WKT 两种几何存储格式
   - 自动检测几何类型（Point/LineString/Polygon）
   - `extract_mdb_features()` — 提取 MDB 中所有空间表的 GeoJSON features
   - `extract_mdb_to_stored_files()` — 将 MDB 表转换为 StoredFile 对象并写入数据库
3. **上传端点集成**: `data.py` upload 端点在检测到 MDB 格式时调用 `mdb_processor` 提取并入库
4. **测试覆盖**: 11 项单元测试 + 集成测试框架

### 新增文件
- `app/services/mdb_processor.py` — MDB 处理器
- `tests/test_mdb_processor.py` — MDB 处理器单元测试 (11 tests)
- `tests/test_format_detection_mdb.py` — MDB 格式检测测试
- `tests/test_upload_mdb.py` — MDB 上传端点测试
- `tests/integration/test_mdb_integration.py` — MDB 集成测试框架

### 版本追踪更新
- `BASELINE.md` — v2.12.0
- `CONTEXT.md` — v2.12.0
- `deploy_linux.sh` — v2.12.0
- `deploy_windows.bat` — v2.12.0
- `restart.bat` — v2.12.0
- `app/main.py` — version="2.12.0"

---

## v2.11.0 — 坐标转换结果管理 + Bug 修复 (2026-08-26)

**类型**: 功能重构 + Bug 修复

### 变更内容

1. **坐标转换安全重构**: 转换端点不再原地修改要素，改为生成 GeoJSON 副本存入新表 `transform_results`，支持查询、下载、删除
2. **Bug 修复 — MissingGreenlet**: `topology_checker.py` 中 `db.rollback()` 导致 ORM 对象过期，quality.py/data.py 3 个端点提前缓存 `api_key_id`
3. **Bug 修复 — 格式转换静默挂起**: 添加 Celery worker 同步回退机制，`restart.bat` 新增 Step 7 启动 Celery worker
4. **CRS 目录加载修复**: 前端 CRS 下拉改用 `_t()` 直接 fetch 绕过 `ah()` 认证检查（CRS 目录为公开端点）

### 新增数据库表
- `transform_results` — 坐标转换结果存储（`alembic/versions/0011_transform_results.py`）

### 新增 API 端点 (3)
- `GET /api/v1/spatial/transform-results` — 转换结果列表
- `GET /api/v1/spatial/transform-results/{result_id}/download` — 下载 GeoJSON
- `DELETE /api/v1/spatial/transform-results/{result_id}` — 删除结果

### 受影响文件
- `app/api/v1/spatial.py` — 重写 transform 端点 + 新增 3 个端点
- `app/api/v1/quality.py` — 修复 MissingGreenlet（2 处）
- `app/api/v1/data.py` — 修复 MissingGreenlet（1 处）
- `app/api/v1/batch.py` — 添加 Redis 不可用同步回退
- `app/models/transform_result.py` — 新增 ORM 模型
- `app/schemas/transform_result.py` — 新增 Pydantic schema
- `app/models/__init__.py` — 注册新模型
- `alembic/versions/0011_transform_results.py` — 新增迁移
- `app/frontend/index.html` — 前端转换结果表格 + 下载/删除
- `restart.bat` — 新增 Step 7 Celery worker + v2.11.0 标题

---

## v2.10.6 — 坐标转换功能修复与增强 (2026-08-25)

**类型**: Bug 修复 + 功能增强

### 问题现象
- 坐标转换页面下拉列表为空，无法选择任何已上传的文件
- 首页最近文件列表、数据预览页文件下拉同样为空

### 根因
- `/api/v1/data` 返回 `PaginatedFiles` 对象 `{items: [...], total: N, ...}`
- `loadTxFiles` / `loadPvFiles` / `loadRecent` 直接对该对象调用 `.filter()` / `.map()`
- 对象无这些方法，异常被 `try/catch` 静默吞掉，导致列表始终为空
- 数据管理页 `loadFiles` 已正确处理：`const raw=resp.items||resp`

### 修复内容

**前端 (index.html)**
1. `loadTxFiles`：修正响应解析 `(resp?.items||resp||[])`，page_size 提升至 200，下拉选项显示 CRS 标签
2. `loadPvFiles`：同样的响应格式修复
3. `loadRecent`：同样的响应格式修复
4. 坐标转换页选择文件时自动填充源 CRS（从 `detected_crs` 读取）
5. 预览区新增 CRS 信息显示（源 CRS + 坐标值）
6. 转换完成后自动刷新文件列表并重新选中当前文件

**后端 (spatial.py / feature_store.py)**
1. `transform_features_by_file`：移除内部 `commit()`（由调用方统一管理事务），移除 `api_key_id` 过滤（文件级权限已在 API 端校验）
2. `repair_features_by_file`：同样的事务管理和过滤修复
3. 坐标转换端点：转换后同步更新 `detected_crs_srid` / `crs_is_custom`，重跑图层统计聚合

---

## v2.10.5 — 自定义坐标系 SRID 注册 + 米制量算策略重构 (2026-08-24)

**类型**: Bug 修复（质检 500 根治） + 架构增强

### 问题现象
- FW.gdb.zip 上海地方坐标系文件（纯面要素）执行质检时，
  面裂隙/极大面/极小面（T09/T03/T04）必然失败，
  PostGIS 抛 `transform: Invalid coordinate (2049)`；
  其余依赖米制变换的规则（T05/T06/T07/T08）在同类型数据下同样受威胁。

### 根因链（两层缺陷叠加）
1. **入库误标 SRID**：自定义投影坐标系 `PROJCS["shanghaicity"...]`（无 EPSG 编码）
   被 `crs_transform.crs_to_srid()` 回退标记为 4326，投影米值被当作经纬度存储；
2. **量算硬编码目标投影**：v2.10.4 将量算统一 `ST_Transform(geom, 4549)`，
   对误标数据必然触发 PROJ 坐标域异常。
3. **附带发现**：源文件 WKT 存在假东/假北互换导出缺陷
   （False_Easting=-3457147.81 / False_Northing=0，实测数据按 x_0=0/y_0=-3457147.81 才落回上海），
   仅影响预览落点不影响量算，注册时自动检测并纠正。

### 方案A 实施（全部落地）
- **A1 新增 `app/services/custom_crs.py`**：
  - 无 EPSG 编码但 pyproj 可解析的 CRS → 确定性分配自定义 SRID `910000~998999`
    （SHA-256 哈希跨进程稳定，冲突换盐重试），幂等注册到 `spatial_ref_sys`
    （auth_name=`VCS-CUSTOM`）；
  - 假东/假北互换检测纠正：判据 `|x_0| > 999km 且 y_0 = 0`（合法 TM/GK 假东 ≤835km）；
    因 PostGIS+PROJ6 **优先读 srtext**，WKT 与 proj4 双修正；已注册旧行自愈更新；
    源文件原始声明完整保留于 `stored_files.detected_crs` 不影响溯源；
  - 坐标域防线：`validate_loaded_extent()` 地理坐标系下坐标超 ±180/±90 告警。
- **A2 迁移 `0009_stored_file_crs_srid.py`**：`stored_files` 增加
  `detected_crs_srid Integer NULL` / `crs_is_custom Boolean DEFAULT false`。
- **A3 入库全路径接线 `app/api/v1/data.py`**：上传/合并/重索引等 8 处调用点
  统一走 `_ingest_srid()` 解析并落元数据；异步加载器完成后执行坐标域校验，
  告警写入处理统计 `crs_warning`；GDB 分组先在主会话解析 SRID 再派发线程子任务（规避竞态）。
- **A4 存量修复工具 `scripts/repair_feature_srid.py`**：对比源文件声明 CRS 与要素实际 SRID，
  重标误标数据 + 补全元数据 + 注册自定义坐标系；默认 dry-run，`--apply` 执行，`--file-id` 过滤。
- **A5 米制量算策略重构 `app/services/topology_checker.py`**：
  硬编码 4549 替换为 `MetricStrategy` 运行期自适应 ——
  - 数据原生 SRID 为米制投影 → **native** 原生量算（零变换）；
  - 地理坐标系 → 按数据质心动选 UTM 带 EPSG:326xx(北)/327xx(南) — **utm**
    （选带查询用 `ST_PointOnSurface` 取样点；不可用 ST_Extent——其 box2d 丢失 SRID 致 transform 0 报错）；
  - 无法判定 → 兜底 4549 — **legacy**；
  - T03/T04/T06/T07/T08/T09 全部经策略生成表达式/SQL 片段；
  - T05 微短线顺带修复单位错配（原地理坐标下用"度数长度"对比米制阈值）。

### 验证结论（实测）
- 问题文件 `FW.gdb.zip/FW_CILP`(103 面) 重标 4326→918073 后：11 条规则 0 异常，
  原 500 的 T03/T04/T09 均 PASS；量算 SQL 为原生 `ST_Area(geometry)`（native 模式）；
- 4490 地理坐标测试数据自动选带 UTM 51N(EPSG:32651)，11 规则 0 异常（utm 模式）；
- 自定义 SRID 918073 变换落点由 (150.99E, 0.18N) 纠正为 (121.39E, 31.44N) 上海 ✓；
- 两个同源文件解析到同一确定性 SRID 918073（哈希复用验证 ✓）；修复脚本幂等（复跑全 ok）。

### 影响面
`custom_crs.py`(新增)、`topology_checker.py`、`api/v1/data.py`、`models/stored_file.py`、
`alembic/versions/0009_stored_file_crs_srid.py`(新增)、`scripts/repair_feature_srid.py`(新增)。
升级后需对存量数据执行一次 `python scripts/repair_feature_srid.py --apply`。

### 附：DWG 图形文件识别修复
- **现象**: 上传 `.dwg` 文件后列表分类显示"unknown/未知"，未被识别为图形文件；
- **根因**: 魔数检测正常（`AC10xx` → format_name='DWG', confidence 0.95），
  但 `format_detection.GEOMETRY_FORMATS` 分类集合遗漏 `"DWG"`，
  `detect_data_type('DWG')` 落入 unknown 分支（对照 DXF 在集合内 → geometry）；
- **限制说明**: GDAL/Fiona 无 DWG 驱动（DXF/DGN 有），DWG 仅能"识别归类"，
  要素解析需先经 ODA File Converter 转 DXF，当前栈不做解析；
- **修复**: `GEOMETRY_FORMATS` 加入 `"DWG"`；`data.py` 空间展开跳过黑名单补 `.dwg`
  （避免上传时必然失败的 Fiona 解析尝试与告警噪音）；
- **验证**: 17 种格式分类回归全对（仅 DWG 由 unknown→geometry，其余不变）、
  `_detect_and_populate` 上传路径 E2E 断言 data_type='geometry'、
  `tests/test_format_normalization.py` 8/8 通过。

### 附 2：DGN 图形文件全支持（识别 + 要素解析）
- **现象**: 与 DWG 同类缺陷——`.dgn` 分类落 unknown；且压缩包展开门控
  `VECTOR_EXTENSIONS` 不含 `.dgn`，即使识别了也不会提取要素；
- **能力实证**: GDAL 内置 DGN 驱动（V7）实测可完整读写——构造真实 DGN 样本，
  经 `detect_format` → `extract_features_from_file` 全链路提取出 Polygon 要素 ✓；
- **限制说明**: V8 格式需 ODA 驱动（本环境缺失），此类文件解析失败时按既有
  防线优雅降级（Fiona 异常捕获 → 空要素统计，不中断上传）；
- **修复**: `GEOMETRY_FORMATS` 加入 `"DGN"`；`VECTOR_EXTENSIONS` 加入 `'.dgn'`
  （压缩包子文件空间展开放行）；`spatial_loader._guess_format` 已有 `.dgn→DGN` 映射无需改动；
- **验证**: 分类矩阵 17/17、矢量门控 `.dgn=True/.dwg=False`、上传路径双格式
  E2E 断言、真实 DGN 样本要素提取 2/2、单测 8/8 通过。

### 附 3：未知格式兜底路由修复（CC.dwg UnicodeDecodeError）
- **现象**: 上传 `CC.dwg` 报 `'utf-8' codec can't decode byte 0xc0 in position 13`
  （同类：再个高线范围.dwg position 40），前端以 ⚠ 警告呈现；
- **根因**: `spatial_loader._guess_format()` 对未知扩展名兜底返回 `'GeoJSON'`
  （`.get(ext, 'GeoJSON')`）→ 二进制 DWG 被送入 `_extract_from_geojson()`
  做严格 UTF-8 `json.load` 必然崩溃；且 `_extract_from_geojson` 是唯一无
  try/except 防护的提取器，异常穿透到上传响应成为逐文件警告；
- **修复（双层）**:
  1. `_guess_format` 兜底 `'GeoJSON'` → `None`——未知扩展名走 Fiona 驱动嗅探
     （异常安全），消除整类"任意二进制当 GeoJSON 解析"缺陷；
  2. `_extract_from_geojson` 补 try/except 优雅降级为空列表（损坏/非 UTF-8
     的 .json 同样不再中断上传）；
- **验证**: CC.dwg 字节复现原错误→修复后优雅降级 0 要素；损坏 GBK .json 降级 ✓；
  合法 GeoJSON 提取回归 ✓；DXF fiona 通道回归 ✓；分类矩阵与单测 8/8 ✓；
- **部署注意**: uvicorn 无 `--reload`，代码修复后必须重启服务才生效
  （本次事故中服务自前一日运行未重启，旧代码持续触发已修复路径）。

### 附 4：DWG 要素解析全支持（LibreDWG 集成）
- **背景**: 上述修复后 DWG 可正确归类为图形文件，但 GDAL 无 DWG 驱动，
  `feature_count=0` 无法识别要素记录——用户上传实测后反馈此缺口；
- **方案**: 集成 GNU LibreDWG v0.14（win64 二进制，GPL 开源）至
  `tools/libredwg/`（dwg2dxf.exe + libredwg-0/libiconv-2/libpcre2 运行时 DLL），
  DWG→DXF 转换后复用既有 Fiona DXF 解析链路；
- **实现**:
  - 新增 `app/services/dwg_converter.py`：子进程调用 dwg2dxf（120s 超时、
    独立临时目录、非致命告警降级 debug 日志、产物用后即清）；
  - `spatial_loader`：`_guess_format` 增加 `.dwg→DWG` 路由 +
    `_extract_from_dwg()` 分支（转换失败返回空列表，不中断上传）；
  - `data.py`：`.dwg` 加入 `VECTOR_EXTENSIONS`（ZIP 子文件展开生效）、
    移出空间展开跳过名单；
- **实测**: 用户真实 CC.dwg (20KB) → DXF(100KB) → **33 个要素**
  （23 LineString + 10 MultiLineString）全链路通过；
- **回归**: 分类矩阵 17/17、上传路径模拟、GeoJSON/损坏 JSON/DXF 通道全过；
  tests 套件中 test_api 的 4 failed/5 errors 为既有 httpx 异步夹具环境问题
  （Event loop is closed，与本次改动无关）；
- **部署注意**: 服务已重启加载；存量 DWG 记录（优化#5 不留存原始字节）
  无法 reindex，需删除后重新上传。

### 附 5：DWG 闭合多段线面语义还原
- **现象**: CC.dwg 解析出的 33 个要素全部为 LineString/MultiLineString，
  用户反馈"应识别为面"；
- **根因**: DXF 中 44 条 LWPOLYLINE 全部带闭合标志（flag&1=44/44），但
  GDAL DXF 驱动默认输出线要素；`CLOSED_LINE_AS_POLYGON=YES` 打开选项在
  本环境 GDAL 版本无效（实测结果不变）；
- **修复**: `spatial_loader._cad_lines_to_polygons()`——闭合环（≥4 点且首尾
  重合容差 1e-6）提升为 Polygon：单环→Polygon、多环→MultiPolygon、
  混合 MultiLineString→拆分为面+线两要素、面积≈0 退化环与无效几何
  （make_valid 失败）保留原线形态；挂接 `_extract_from_dwg` 输出；
- **实测**: CC.dwg 33/33 全部转为 Polygon×23 + MultiPolygon×10，
  坐标域合理（CAD 本地坐标系）；
- **边界说明**: 仅作用于 DWG 转换路径；DXF 直传保持原行为（一致性改造列入
  多类型归类改进计划备选项）。

### 附 6：图层 × 几何类型二维归类 + 质检图层圈定（2026-08-25）
- **背景**: ①列表/详情仅按文件级单一 geometry_type 展示，DXF/DWG/GDB 等混合
  类型文件的线面并存被掩盖（且 fiona 对 CAD 的 layer_name 恒为 'entities'，
  真实图层藏在要素属性里）；②拓扑检查全量扫文件，无法只检指定图层。
- **数据库**（迁移 `0010_layer_stats_and_qc_scope.py`）:
  - 新表 `stored_file_layers`：文件-图层聚合行
    （layer_name/geometry_type/point_count/line_count/polygon_count/feature_count，
    UNIQUE(stored_file_id, layer_name)，api_key_id 可空兼容回填）；
  - `stored_files` 冗余三维计数列 point_count/line_count/polygon_count（列表页零 JOIN）；
  - `quality_reports.checked_layers` JSONB——报告记录本次质检圈定的图层（NULL=全部）。
- **后端**:
  - `feature_store.aggregate_layer_stats()`：三分区表 GROUP BY 实时聚合 →
    DELETE+INSERT 幂等落 stored_file_layers + update() 刷新文件级计数；
    上传（单文件/压缩包逐子文件）/合并两分支/reindex 四路径挂接，
    全部包 `begin_nested()` SAVEPOINT——聚合失败只回滚保存点不拖垮主事务；
  - `spatial_loader._promote_cad_layers()`：CAD 要素 `props['Layer']` 提升为
    feature.layer_name；**顺带修复 DXF 直传缺面还原缺陷**（此前只有 DWG→DXF
    链路挂 `_cad_lines_to_polygons`，直传 DXF 的闭合多段线不转面）；
  - `topology_checker.run_topology_checks(layer_names=...)`：全部 14 个查询点
    贯通圈定（9 处 ORM 附加 where + 5 处原生 SQL `_sql_clause` + ANY(:layers)），
    圈定语义 = 所有规则仅在所选图层要素集内计算（含 T6-T9 跨要素空间关系）；
  - QC 端点新增 Form 参数 `layers`（JSON 数组）：未知图层名 400 并列出可用图层，
    报告落 checked_layers；六个响应构造点（CheckResponse/Brief/Detail×4）补齐字段。
- **API 变更**: `/data/{id}/structure` 重写为真实图层清单（含每层 P/L/G 计数，
  按 feature_count 降序；历史数据无聚合行时回退旧伪单层结构保持兼容）；
  `/data` 列表 geometry_type 筛选改计数驱动（mixed 文件可被命中）；
  `/data/stats` 新增 feature_totals{point,line,polygon,total}。
- **前端**（index.html）: 列表徽章组合化（●点/─线/◆面彩色计数 chip + N图层角标）；
  统计卡改用 feature_totals；详情抽屉加要素构成堆叠条 + 图层清单表；
  抽屉质检页加「检查范围」图层多选 + 按圈定范围质检按钮，报告显示「已检图层」标签；
  修复遗留空指针 loadDInfoTypes（dinfo-type-cnt/dinfo-total 元素不存在时崩溃）。
- **工具**: `scripts/backfill_layer_stats.py` 存量数据幂等回填（--dry-run/--file-id）。
- **验证**: 单层 DWG E2E 12 项 / 双图层 DXF E2E 9 项全过（含未知图层 400、
  checked_layers 落库、删除清理）；Playwright 浏览器端到端（图层表渲染、构成条、
  范围面板、范围质检→报告「已检图层」标签）零 JS 错误；pytest 8/8；
  回归 7/7。已知边界：测试用坐标 (200,200) 以默认 SRID 4326 入库属非法经度，
  T5 变换报 Invalid coordinate 属预期防线（真实数据不受影响）。

### 附 7：无 CRS 的 CAD 平面坐标质检崩溃修复——planar 量算模式（2026-08-25）
- **现象**: 用户上传 `LANDSURVEY_PLANNING_RANGE_Ex.DWG`（用地红线图，7 面，
  CAD 本地平面坐标 X∈[27040,27753] / Y∈[-1940,-1374]）执行质检，
  T03 极大面/T04 极小面/T06 面重叠报 "transform: Invalid coordinate (2049)"，
  T09 面裂隙 SQL 异常跳过，目标投影竟为 EPSG:32760（UTM 60S 南非海域）。
- **根因**（v2.10.5 主修复的盲区——该修复解决"有 CRS 但误标"，本例是"根本没有 CRS"）：
  1) 无 CRS 检出 → 要素入库盲标默认 SRID=4326（米值被当经纬度语义存储）；
  2) `_resolve_metric_strategy` 判定 4326 为地理坐标系 → 动态 UTM 选带；
  3) 选带用单要素质心取样：X=27385 直接当经度 → zone clamp 到 60，
     Y=-1644<0 → 南区 → EPSG:32760；
  4) T03/T04/T06/T09 对全量要素 ST_Transform(geom, 32760) → 经度 27040
     远超 PROJ 有效域 → Invalid coordinate。
- **修复**（`topology_checker.py` 单文件）：
  - MetricStrategy 新增 **planar 模式**（零变换直接量算，单位=数据原生平面单位，
    CAD 图纸即米）；原 legacy(4549) 兜底路径全部退役改 planar——对 SRID=0/
    未注册数据 ST_Transform 本就必然抛异常，planar 是唯一安全选择；
  - `_utm_strategy_for_data` 加**全量 bbox 度域防线**：ST_Extent + ST_SetSRID
    补回丢失 SRID 后变换到 4326，任一角点 |lon|>180 或 |lat|>90 → 判定平面
    坐标走 planar 并告警；同时把"单点取样"升级为"全量 bbox"（覆盖部分要素
    越界场景）；选带基准从质心改为 bbox 中心；
  - 附6 会话中曾误判同类现象（合成 DXF (200,200)）为"测试数据问题"，实为
    同一根因的真实暴露，本附一并根治。
- **实测**: 用户文件 618e35e1 复跑 14 条规则零异常（T03/T04/T06/T09 全转绿，
  score_topology 0→0.929；唯一 fail 为 T11 不规则面 ×1 —— 真实几何质量发现，
  与修复前一致）；CC.dwg（X∈[-8533],Y∈[6908] 平面坐标）同样正确判 planar；
  合成经纬度数据 (116E/39.9N) 仍正确选带 UTM 50N(EPSG:32650) 不误伤；
  pytest 8/8、回归 6/6 全过。

---

## v2.10.4 — 量算平面化 + 规则异常隔离【补录】 

**类型**: Bug 修复（2026-08-24 补录；原发日期未记录，内容据 BASELINE 5.2 摘要）

- **米制量算平面化**: `ST_Transform(geom,4326)::geography` 改为平面投影
  `ST_Transform(geom,4549)`（CGCS2000/3度GK），规避 geography 球面面积
  `area < 0.0` InternalServerError 并级联拖垮整次质检 → 500；
- **规则隔离**: T03/T04/T06/T07/T08/T09 增加 try/except + db.rollback，
  单规则异常不再中断整次检查；
- **MissingGreenlet 修复**: `quality.py`/`data.py` 在 session 过期前缓存
  `file_name`，消除报告构建期同步懒加载 → 500。

---

## v2.10.3 — 米制阈值体系建立【补录】

**类型**: 功能修正（2026-08-24 补录；原发日期未记录，内容据 BASELINE 5.2 摘要）

- 面积/距离/长度返回真实米制单位；前端 km² 输入 ×1e6 转 m²；
- 默认阈值修正 max_area=1e10 m² / min_area=0.01 m²。

---

## v2.10.2 — 连接池优化 + 子任务并发控制 (2026-08-21)

**类型**: 性能优化 + 稳定性修复

- **连接池调优**: 异步引擎 `pool_size=5` / `max_overflow=15`（总上限 20），同步引擎自动 `min(3)` / `min(5)`（总上限 8），新增 `pool_timeout=30`
- **子任务并发控制**: 新增 `asyncio.Semaphore(5)` 限制 asyncio.gather 并发子任务数量，防止连接池被单次上传撑满
- **根因**: `asyncio.gather` 并发启动 N 个子任务（_load_extracted_features / _load_shp_group_features / _load_all_features_to_spatial），每个创建独立 session，N>20 时连接池耗尽
- **影响**: `app/config.py`、`app/db/session.py`、`app/api/v1/data.py`（3 个 async 子任务添加 semaphore）
- **验证**: GeoJSON 上传 → feature_count=1，point_features 分区 1 条；OBJ zip 上传 → 子记录 + 3D 要素写入正常

---

## v2.10.1 — 质检检查器修复 + 规则生成器 v2 适配 (2026-08-19)

**类型**: Bug 修复 + 功能增强

- **拓扑检查器**: 移除全部 13 处 `api_key_id` 过滤条件（要素表 api_key_id 为 NULL 导致恒 0 结果）
- **属性检查器**: `load_features_by_file()` 移除 api_key_id 过滤
- **规则生成器 v2 适配**: 重写使用 v2 `StandardField` 模型，新增字典关联 `VALUE_IN_SET` 规则 + 主键 `UNIQUE` 规则
- **标准质检概览**: 新增 `GET /data-standards-v2/{id}/qc-summary` 端点
- **影响**: `topology_checker.py`、`attribute_checker.py`、`rule_generator.py`、`quality_rules.py`、`data_standard_v2.py`

---

## v2.10 — 空间分表 LIST 分区化 + 流式全量落库 (2026-08-08)

**类型**: 架构重构 + 性能优化

- **三张分表转分区**: point/line/polygon_features → `PARTITION BY LIST (stored_file_id)`，每源一物理分区
- **PK 改复合**: `(stored_file_id, id)`；`stored_file_id` NOT NULL
- **properties JSON→JSONB**（GIN 索引要求）
- **Geometry 统一 `'GEOMETRY'`**（分区父/子 typmod 须一致）
- **流式全量**: `read_layer_features` 生成器化 + `write_features_batch_sync`（SyncSessionLocal + flush_every=1000）+ `_load_gdb_layer_async` → `to_thread(_sync_load_gdb_layer)`
- **移除 `max_features_per_upload`** 与全部 truncated 逻辑
- **删除端点**: `_delete_file_recursive`（递归删子记录 + 分区 DETACH+DROP 秒删）
- **asyncpg DDL 兼容**: `_ensure_partition` 的 `FOR VALUES IN` 改内联（防御转义）
- **影响**: `spatial_feature.py`、`stored_file.py`、`config.py`、`session.py`、`feature_store.py`、`gdb_processor.py`、`data.py`

---

## v2.9.1 — scope_clause 全链路健康检查 (2026-08-17)

**类型**: Bug 修复

- **Bug**: `scope_clause()` 超级管理员返回 Python `True`，`source_data.py` 使用 `|` 运算符组合 → `TypeError`
- **修复**: `scope_clause()` 改为返回 `sqlalchemy.true()`（`ClauseElement`），支持所有 SQLAlchemy 表达式运算符
- **影响**: `app/core/auth_context.py`（1 行修改 + 1 行 import）
- **验证**: 全组件 14 个端点 HTTP 200 回归通过

---

## v2.9 — 数据字典展示拆分 (2026-08-12)

**类型**: 功能拆分

- **全局/标准双维度**: 数据字典面板拆分为「🌐 全局字典」与「📋 标准字典」两个独立 tab
- **后端**: 标准端点强制 `scope='standard'`；新增 `POST /dictionaries` 全局创建端点；全局列表只返回 `scope='global' AND standard_id IS NULL`
- **前端**: tab 切换 + 按维度加载 + 创建分流 + 删除联动
- **数据清洗**: 清理 2 条隐身脏字典 + 32 个测试标准
- **验证**: 8 项集成测试 + Playwright 浏览器级验证通过

---

## v2.8 S1 — PostGIS 入库稳定性 (2026-08-04)

**类型**: 稳定性修复

- **make_valid 兜底**: `geojson_to_wkt` 解析成功但无效几何 → `shapely.validation.make_valid()` 修复
- **状态化报告**: `GeometryStatus` enum（OK / CORRECTED / FAILED / EMPTY）+ `validation_status` / `validation_message`
- **子任务独立 AsyncSession**: `asyncio.gather` 并发时每个子任务创建独立 session，不再共享
- **单文件隔离事务**: 每个子文件独立 `async with AsyncSessionLocal()`
- **同步全量落库**: `_sync_load_gdb_layer` 使用 `SyncSessionLocal` + `flush_every=1000`（异步 `await to_thread`）
- **移除 `max_features_per_upload`**: 旧截断逻辑全删，真实图层不再截断
- **GDB schema 丢失修复**: Fiona GDB driver 对含 `_` 前缀图层无 schema → 手动 `detect_union_schema`
- **WKT 吞异常修复**: 解析失败不再静默返回 EMPTY

---

## v2.7 — ERROR.txt 批量修复 (2026-08-04)

**类型**: Bug 修复 + 功能完善

**8 模块 30+ 项修复**:
- **管理 API**: Key 启用/重新生成端点
- **前端通用**: `esc()` 单引号转义、fetch 30s 超时、adminKey 持久化、拖拽拦截、压缩包过滤
- **字典 CRUD**: 增删改、层组父级下拉、showGroupModal 竞态
- **质检空要素**: 前置 400 + Phase 0.5 守卫 + score 0 分修正
- **数据标准 v2**: 字典 409/scope 400、Excel v2 优先解析
- **健康检查**: PG/Redis ping 2s 超时
- **WebSocket**: Redis 不可用时连接存活检测
- **下载**: RFC 5987 文件名编码
- **restart.bat**: Start-Process 隐藏窗口 + 自动关窗 + curl 超时

---

## v2.6 — 质检页重构 (2026-07-31)

**类型**: 前端重构 + 后端适配

- **紧凑布局**: 单页工作台（文件栏 + 标准栏 + 可折叠检查配置 + 报告表）
- **检查项横向排列**: 胶囊形式 flex-wrap，13 拓扑 + 9 属性
- **字号体系**: 表格 12px / 按钮 11px / 全选 10px
- **无标准纯拓扑检查**: `standard_id` 为空时仅执行拓扑
- **超级管理员可见性**: `is_super_admin` 标记 + scope/owns 放行
- **验证**: Playwright + Chrome 20 项全通过

---

## v2.5 — 分组权限 + 超级管理员 (2026-07-29)

**类型**: 权限体系升级

- **KeyGroup 分组**: API Key 归入分组，同组 Key 共享数据访问
- **AuthContext**: `accessible_key_ids` / `scope_clause()` / `owns()` 三级权限模型
- **超级管理员**: `ADMIN_INIT_KEY` 可查所有数据
- **API Key 硬删除**: 永久移除废弃 Key
- **查询模式迁移**: 68 处 `api_key.id` → `scope_clause()` / `owns()`（9 个路由文件）
- **验证**: 分组数据共享、超级管理员可见性、硬删除 E2E 全通过

---

## v2.4 — 上传模块重建 + 数据类型细分 (2026-07-22)

**类型**: 核心模块重建

- **标准分类入库**: 上传时关联已有/新建标准
- **自动建标准表**: 根据 field_schema 自动创建层组→表→字段层级
- **格式检测重写**: BOM 修复、几何类型检测、FormatResult 新增 geometry_type/data_type
- **大文件切片上传**: upload_id + chunk_index + total_chunks
- **数据类型细分**: geometry/table/archive/unknown + Point/LineString/Polygon/3d/table
- **前端上传配置**: 三互斥 Radio（不关联/关联已有/新增标准）

---

## v2.3 — 质检模块重建 (2026-07-21)

**类型**: 核心模块重建

- **标准驱动质检**: 读取 v2.2 标准定义驱动检查
- **属性检查 9 项**: A1-A9（图层/类型/结构/必填/值域/唯一/空值/小数位/长度）
- **拓扑检查 13 项**: T01-T13（混合 PostGIS SQL + shapely）
- **五维评分**: completeness 25% / accuracy 25% / consistency 20% / uniqueness 15% / topology 15%
- **可配置容差**: 8 项拓扑容差参数

---

## v2.2 — 数据标准功能 (2026-07-15)

**类型**: 功能升级 + 数据库扩展

- **合并双轨模型**: DataStandard + SourceTable → 统一层级体系
- **嵌套层组**: 最多 5 层树形结构
- **共享字典**: standard / global 两种作用域
- **多格式导入**: JSON / Excel / CSV
- **版本管理**: 发布版本快照 + 版本对比
- **45 个新 API 端点**: 标准 CRUD / 层组 / 数据表 / 字段 / 字典 / 版本 / 导入

---

## v2.1 — 数据库架构升级 (2026-07-15)

**类型**: 数据库架构重构

- **单基线迁移链**: 消除双基线问题
- **外键约束**: 全表添加 FK 确保数据完整性
- **BYTEA 大文件存储**: `max_single_file_size_mb=100` + 分片策略
- **空间约束**: `ST_IsValid(geometry)` CHECK
- **审计字段**: 所有表添加 `updated_at`

---

## 历史迭代稿（已删除，仅保留最终版）

以下 HTML 原型已删除（7 个中间迭代稿，共 ~2,900 行），仅保留 `qc_redesign_v5.html`（v2.6 实现依据）：

- ~~`qc_page_mockup.html`~~ / ~~`qc_page_mockup_batch.html`~~ / ~~`qc_page_mockup_v2.html`~~ — v2.5 早期布局稿
- ~~`qc_redesign_preview.html`~~ / ~~`qc_redesign_v2.html`~~ / ~~`qc_redesign_v3.html`~~ / ~~`qc_redesign_v4.html`~~ — 中间迭代稿

---

*详细变更记录见各版本 `archive/revision_vX_Y.md` 原文。*
