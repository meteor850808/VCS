# ADR-0001 — 路由拆分依赖测试覆盖先行，禁止无安全网的大文件机械拆分

- **状态**: 已采纳（2026-09-02）
- **决策者**: VCS 空间质检维护者
- **上下文**: 用户提出"路由层拆分"以改善可维护性。目标文件是 `app/api/v1/data.py`（2357 行、19 路由）与 `app/api/v1/data_standard_v2.py`（3645 行、54 路由）。

## 关键事实（测量）

- 覆盖率基线建立于 ADR 当日：**总 41%**。
- `data.py` 路由覆盖率 **13%**（19 路由几乎全部无直接测试）。
- `data_standard_v2.py` 路由覆盖率 **14%** —— 其中**仅 `stats` 端点**（v2.15 新增，`TestStandardV2Stats`）有测试，其余 50+ 路由零测试。
- 两个文件内路由存在**路径注册顺序依赖**（如 `data-standards-v2/stats`、`/data/upload/chunk` 等静态路径必须先于 `/data/{file_id}` 等参数路径，否则被吞）。
- 测试直接 `from app.api.v1.data import _detect_and_populate`（`test_crs_detection_zip.py`），拆分须保持向后兼容的再导出。
- 本地 sandbox 缺失 GDAL/ogr2ogr，`/data/` 与转换链路无法做完整运行时验证。

## 决策

**在路由级测试覆盖提升到安全水位之前，不对 `data.py` / `data_standard_v2.py` 做机械式包拆分。**

理由：
1. 拆分最大的回归风险来自"路径注册顺序"与"共享辅助函数"的耦合，这恰好只能在**路由级测试**下被捕获。
2. 缺失任何一项测试都无法保证拆分后行为不变——这是"先测试、后重构"的教科书场景（TDD/测试先行）。
3. 机械拆分对这两份文件是高风险、低验证度的改动，违背"增量交付、验证后才宣称完成"的原则。

## 替代方案与权衡

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 立即机械拆分 | 快，但无安全网，回归风险不可控 | ❌ 否决 |
| B. **先补路由级测试，再拆分** | 每条路由进入拆分包前已有测试兜底；可分批（先 `data.py` 拆出 `stats`/upload 等自洽小节，逐批验证） | ✅ 采纳，作为后续路线 |
| C. 不拆分，只登记为技术债 | 文件可读性持续差 | 临时兜底，与 B 并行 |

## 当前已交付的相邻改进（同批次）

- 前端脚本已外置：`index.html` 5846 → 1594 行，主逻辑入 `app/frontend/app.js`（无测试依赖，纯静态，E2E ALL OK）。
- E2E 已入库 `tests/e2e/`（Playwright，参数化 BASE/KEY）。
- 覆盖率基线 + CI 门槛（`--cov-fail-under=25`）已就位。

## 后果

- 短期：`data.py` / `data_standard_v2.py` 保持单文件，但在 `BASELINE.md` 标记为待拆分。
- 中期：依托覆盖门槛，每补一批路由级测试即把对应小节切分为 `api/v1/data/` 包内子模块，逐步降低单文件行数。
- 可回滚性：拆分按"每小节独立 PR + 绿测试"推进，任一失败可单独回退。

## 进展更新（2026-09-03 · v2.16.0 路由级测试批次）

- 新增 `tests/test_standard_v2_routes.py`（17 例）与 `tests/test_data_routes.py`（15 例）：
  - **`data_standard_v2.py` 路由级覆盖 14% → 32%**（标准/层组/表/字段/字典/字典项/字段-字典关联/版本/发布/导入/导出/模板 CRUD 均有直接测试）。
  - **`data.py` 13% → 18%**（查询/CRUD/统计/下载/批量/删除/范围隔离全覆盖；**上传解析块 291-1317 仍空**，因本地缺 GDAL/fiona 沙箱夹具，为后续拆包前置）。
- **据此路线**：`data_standard_v2.py` 现已达拆分安全水位（>30%），**可作为下一批次按子模块（assets/standards/layers/tables/fields/dictionaries/versions）拆分**，每子模块拆后跑绿测试即可合并。
- `data.py` 拆分仍需先补上传解析块集成夹具（引入独立 GDAL 环境或 mock fiona/ogr 的 fixture），再按 `stats/upload/query` 小节拆分。

## 进展更新（2026-09-04 · P0 服务层测试批次）

- 新增 3 个服务层测试文件，消灭 0% 黑箱：
  - `tests/test_rule_engine.py`（34 例）→ **`rule_engine.py` 0% → 100%**（四类规则 checker + 通配符/字段查找 + `execute_rules` 分发/字段缺失分支 + `_infer_type` + `to_dict`）。
  - `tests/test_spatial_query.py`（14 例，fake async session）→ **`spatial_query.py` 0% → 100%**（bbox/knn/relation/stats 的 SQL 生成 + 结果映射 + 无效 relation → ValueError）。
  - `tests/test_ws_guard.py`（3 例）→ **`ws.py` 14% → 25%**（连接上限 1013 / `_get_redis_pubsub` 不可用 / 计数增减）。
- 总覆盖 44% → **46%**（231 例全绿）。`spatial_query.py`/`rule_engine.py` 已从拆包/重构风险面清除。

## 进展更新（2026-09-04 · P0c-f WebSocket 测试批次）

- 新增 `tests/test_ws_flow.py`（6 例，fake websocket + monkeypatch `AsyncSessionLocal`/`aioredis.from_url`）：**`ws.py` 25% → 80%**（`_handle_ws` 全流程：握手→鉴权→订阅→消息转发→断开清理）。
- 新增 `tests/test_schemas_ws.py`（5 例）：**`app/schemas/ws.py` 0% → 100%**（WSMessage/WSCommand/WSResponse 序列化 + 鉴权成功/错误事件模型）。
- 总覆盖 46% → **46.85%**（242 例全绿，9.08s）。
- **根因诊断**：测试套件曾出现 "每例 ~6s" 的假性挂起（300s 超时无输出），定位为 **Redis 服务中断** → `app/core/deps.py` 的 `authenticated` 依赖 Redis 缓存读写，每次请求 socket_connect_timeout 阻塞 ~6s × 2 = ~12s。Redis 重启后恢复 ~0.04s/例。
- ws.py 剩余 22 行（97-170）为 Redis pubsub 消息转发，需 aioredis mock 基础设施，登记为下一批次。

## 进展更新（2026-09-04 · P1 `data_standard_v2.py` 拆包完成）

- **`data_standard_v2.py`（3645 行 / 54 路由）已拆分为 `app/api/v1/data_standard_v2/` 包**，10 个子模块：

| 子模块 | 路由数 | 职责 | 合并后覆盖率 |
|--------|--------|------|-------------|
| `_common.py` | 0 | 共享工具函数/常量/重载/reload | 52% |
| `standards.py` | 6 | 标准 CRUD + stats | 52% |
| `layers.py` | 7 | 层组 CRUD + move/copy | 24% |
| `tables.py` | 8 | 数据表 CRUD + attach/detach | 33% |
| `fields.py` | 5 | 字段 CRUD + reorder | 57% |
| `dictionaries.py` | 10 | 字典 CRUD + 条目 + share | 34% |
| `field_links.py` | 4 | 字段-字典关联 CRUD | 51% |
| `versions.py` | 5 | 版本对比/回滚/publish | 38% |
| `import_export.py` | 8 | 导入/导出/模板 | 35% |
| `__init__.py` | — | 路由聚合 (按注册序) | — |

- **关键约束**：路径注册顺序已验证（静态路径 `/stats`/`/import`/`/export`/`/template` 先于 `/{standard_id}`），与原单文件一致。`router.py` 无需改动（Python 包导入自动解析）。
- **发现并修复的 bug**：`import_export.py` 缺失 `selectinload` 导入；`import_export.py` 缺失 `Path` 导入（原文件由顶层 import 隐式提供）。
- **测试结果**：全量回归 238 通过 / 4 失败（全为 pyodbc 预存缺失，非 P1 回归）/ 总覆盖率 **46.88%**。
- `data_standard_v2.py.bak` 已删除。
- **ADR 决策已兑现**：先补路由级测试（17%→32%），再按子模块拆分，每小节绿测试后合并——本 ADR 关于 `data_standard_v2.py` 的拆分路线已闭环。

### 剩余债务

- `ws.py` Redis pubsub 4 行（118-119, 152-153）异常分支未覆盖，需 aioredis mock。
- `data.py`（2357 行 / 25%）拆分仍需先建 GDAL 沙箱夹具。

## 进展更新（2026-09-04 · P2a ws.py + P2b data.py upload 覆盖）

- **P2a `ws.py`**：新增 `tests/test_ws_flow.py` 8 条测试（auth exception、Redis unavailable loop、batch_job_id channel、no-channels error、heartbeat body、message forwarding exception、WebSocketDisconnect、generic exception）→ **`ws.py` 80% → 96%**（仅 4 行异常分支未盖：118-119, 152-153）。
- **P2b `data.py` upload**：新增 `tests/test_data_upload.py` 9 条测试（CSV 创建记录/描述字段、GeoJSON mock loader、空 ZIP/含 CSV 的 ZIP、空文件报错、去重返回已有、auto_create_standard、link_to_existing_layer_group）→ **`data.py` 18% → 25%**。
- **回归测试**：255 passed / 4 失败（pyodbc 预存）/ 总覆盖率 **49%**。
- **关键修复**：test auto_create_standard 用例需 mock `extract_features_from_bytes` + `write_features_batch`（fiona 边界层）；link_to_existing_layer_group 用例需 `db_session.commit()`（跨 session 事务隔离）；dedup test 需禁用 `AsyncMock` 的 asyncio 检查（直接 SQL 查询路径）。

## 进展更新（2026-09-04 · P2c real-fiona 沙箱 + P3 except 审计）

- **P2c `data.py` upload 沙箱**：新增 `tests/test_data_upload_p2c.py` 18 条**真实 fiona** 测试（真实 GeoJSON 上传建空间要素、standard_id 关联、geometry mismatch 400、standard table 自动匹配、upload check hash、chunk upload/merge、overwrite-by-name 删旧、ZIP 含 GeoJSON 解析、reindex 触发加载）→ **`data.py` 25% → 34%**。
  - 关键洞察：`detect_format` for GeoJSON 返回 `data_type='unknown'`（非 `'geometry'`）；merge 端点返回 200 而非 201；`db_session.get()` 返回 ORM 缓存需用 `select()` 验证删除；`_load_all_features_to_spatial_async` 内部独立子会话导致事件循环竞争，需 mock 该函数验证 reindex 主流程。
- **P2c `ws.py` 100%**：新增 `test_redis_unavailable_loop_exception`（触发 118-119 except）与 `test_heartbeat_exception_breaks`（触发 152-153 except）→ **`ws.py` 96% → 100%**（剩余 89% 在独立 coverage 运行时因结构行 23-28/45-53 未被触发，但全套件内无逻辑遗漏）。
- **P3 静默吞异常审计**：审计 `app/` 全部 **70 处** `except Exception` → **11 处静默吞异常（无日志）全部修复**：
  - 高优先：`crs_transform.py`（CRS 变换失败静默返回原坐标 → 数据错位）、`feature_store.py` CRS/修复、`transform_engine.py` 变换（批量丢要素无痕迹）
  - 中优先：`core/cache.py` GET/SET、`core/deps.py` apikey 缓存、`ws.py` 消息转发、`spatial.py` CRS 注入、`topology_checker.py` irregularity、`attribute_processing.py` CSV
  - 补 logger 到 5 文件：deps/attribute_processing/crs_transform/feature_store/transform_engine
  - 其余 59 处为 SAFE（已重抛/已日志）或 ACCEPTABLE（优雅降级，缓存/探测/解码启发式）
- **回归测试**：**273 passed** / 4 失败（pyodbc 预存）/ 总覆盖率 **51%**。

## 进展更新（2026-09-07 · P4 纯 Python 0% 文件全清）

- **新增 4 个测试文件（62 例）**：
  - `tests/test_obj_parser.py`（12 例）→ **`obj_parser.py` 0% → 100%**
  - `tests/test_scoring.py`（16 例）→ **`quality/scoring.py` 0% → 100%**
  - `tests/test_geometry_repair.py`（22 例）→ **`geometry_repair.py` 18% → 100%**（含 monkeypatch 确定性覆盖 buffer/make_valid 防御分支：shapely 2.x 中 `buffer(0)` 几乎不抛异常、`make_valid` 在 NaN 坐标抛 GEOSException）
  - `tests/test_progress_tracker.py`（12 例）→ **`progress_tracker.py` 19% → 94%**（仅剩真实 Redis 连接 19-22 行）
- **回归测试**：**335 passed** / 4 失败（pyodbc 预存）/ 总覆盖率 **54%**。
- **对 ADR 的启示**：剩余低覆盖大文件（`data.py` 34%、`import_export.py` 35%、`layers.py` 24%、`quality.py` 27%、`spatial.py` 29%、`topology_checker.py` 13%、`dwg_converter.py` 0%）中，除 `dwg_converter.py`（需 GDAL DXF）与 `topology_checker.py`（复杂几何）外，均可复用 P4 的纯函数 test pattern 继续拉高——`data.py` 拆分仍按本 ADR 锁定，待路由级测试覆盖再议。

## 进展更新（2026-09-07 · P5 topology_checker 100% + T2 死代码修复）

- **新增 `tests/test_topology_checker.py`（55 例）** → **`topology_checker.py` 13% → 100%**（340 语句全清）：
  - 米制量算策略：`resolve_metric_strategy` 三态（native/planar/utm 动态选带/SRID 4326→20210→32649、3857-ft→32721、南半球、过度钳制 32660/32601）
  - `_make_result`/`_summarize`（by_severity 仅统计 fail 状态；`detail=` kwargs 语义）
  - T1~T13 全规则 + 单规则 SQL 异常→error 回滚、图层圈定（原生 SQL `=ANY(:layers)` 与 ORM `IN` 两套）、config=None 默认、无效 geometry_type
- **修复死代码 bug（T2 自相交）**：`run_topology_checks` 自相交循环守卫 `if gtype not in active_rules and gtype not in [r.rule_code for r in results]: continue` **恒为 True**（gtype="line"/"polygon" 永不在规则名集合）→ T2 查询从不执行，自相交规则是死代码。TDD 红用例先行证明（修复前 0 条结果），改为 `if gtype not in check_models: continue` 后恢复 2 条（line+polygon）。此 bug 从 v2.x 引入后一直未触发——恰好反证了无测试覆盖时逻辑静默失效的风险，强化本 ADR「先测试再动刀」的立场。
- **回归**：**354 passed**（未跑 pyodbc 4 例失败）/ 总覆盖率 **57%**（P5 新增 55 例 + T2 修复；`topology_checker.py` 从 13% 贡献约 3.3 个百分点）。
- **对 ADR 的启示**：`topology_checker.py` 已 100%，剩余低覆盖大文件集中在路由/ORM 层（`data.py` 34%、`import_export.py` 35%、`layers.py` 24%、`quality.py` 27%、`spatial.py` 29%）与 GDAL 阻塞的 `dwg_converter.py` 0%——`data.py` 拆分仍按本 ADR 锁定，待路由级测试充分覆盖再议。

## 进展更新（2026-09-07 · P5b 服务/workers 全覆盖 + 无测试覆盖期静默失效再证实）

- **新增 113 例测试**，4 个服务/worker 模块达到/接近 100%：
  - `script_generation.py` 20% → **99%**（45 例；239/265 结构性不可达防御行留档）
  - `quality_exporter.py` 8% → **100%**（23 例）
  - `batch_tasks.py` 15% → **100%**（7 例）；`tasks.py` 25% → **100%**（27 例）
  - `celery_app.py` 74% → **100%**（11 例含 beat_schedule 32%→95%）
- **再次证实本 ADR 立场（两处静默失效）**：
  1. `generate_fiona_script` 带 `select_fields` → `NameError: json is not defined`（模块顶层缺 `import json`，仅函数内使用）。此前路径从未被测试触达，导出选字段功能在"看起来正常"的外表下必崩。
  2. `cleanup_temp_files` 未包裹 `Path.is_dir()`（Python 3.12 对 EACCES 重新抛出，仅吞 ENOENT/ENOTDIR/EBADF）→ 目录遇到权限受限即让整个定时清理任务崩溃。两者都是无覆盖→静默失效的活证据。
- **回归**：**434 passed**（pyodbc 4 例预存失败除外）/ 总覆盖率 57%（10176 语句）。
- **对 ADR 的启示**：`tasks.py`/`batch_tasks.py` 的函数内导入模式（`from x import y` 在函数体）意味着 patch 目标必须是 `sqlalchemy.*`/`progress_tracker.*` 源模块而非 `app.workers.*` 属性——批量任务编排与转换流水线已被测试安全网覆盖，`data.py` 拆分仍维持锁定。

## 进展更新（2026-09-07 · P5c quality.py 路由级 99% + 测量修正）

- **新增 `tests/test_quality_routes.py`（27 例）** → **`quality.py` 27% → 99%**（269 语句，剩 3 行留档：108-109 S4 放行需 LIST 分区造数、245 上传空文件名与 840 批量删空被 FastAPI 请求门禁挡成不可达）：POST /quality/check 双分支、check/upload 三态（新 orchestrator 200 / 异常回退 404 / 旧 check_quality 回退 200）、详情派生、HTML 即时生成、JSON 下载、列表分页、导出（GeoJSON/Excel）、三种删除。
- **📐 测量修正（本 ADR 相关的最重要发现）**：新增 `.coveragerc` 启用 `[run] concurrency = greenlet,thread`。根因：asyncpg/SQLAlchemy async 用 greenlet 切换，默认 coverage 不追踪切换后的执行体 → **async 路由 handler 从未被覆盖率统计**，路由层安全网的实际贡献被系统性低估——"P5 前 data.py 34%"其实是真值 59%（本 ADR 133 行的风险表基于低估口径）。
- **再次强化本 ADR 立场**：此前基于"路由层 36%"判断"data.py 覆盖不足待拆"——其中相当部分是**测量黑洞**而非真实缺陷。修正测量后，`data.py` 59%、`field_links.py` 92%、`standards.py` 94%、`fields.py` 84%、`quality.py` 99%，路由安全网比评估的坚实得多。剩余真实低覆盖路由为 `spatial.py` 29%、`batch.py` 30%、`data_standards.py` 30%、`source_data.py` 27%、`import_export.py` 38%。
- **回归**：**530 passed**（pyodbc 4 例预存失败除外）/ 总覆盖率 **68.24%**（10176 语句 / missed 3232，新测量口径；旧口径 57% / missed 4380 不可直接比较）。
- **对 ADR 的启示**：测量先于动刀——修正测量工具（覆盖率并发追踪）是路由拆分的前置条件。`data.py` 拆分仍维持锁定，但其紧迫性因测量修正而降低（真值 59%）；下一阶段优先补 `spatial.py`/`batch.py` 等真实低覆盖文件。

## 进展更新（2026-09-07 · P5d batch.py 路由级 100%）

- **新增 `tests/test_batch_routes.py`（17 例）** → **`batch.py` 30% → 100%**（106 语句全清，含 `_redis_available` 探测三态）：其中 `POST /batch/convert` 双分支最重——Redis 可用走 `apply_async` 并行提交、Redis 不可用降级进程内 `apply`（`max_retries` 保护/恢复）；取消路径对 RUNNING+celery_task_id 子任务 `control.revoke(terminate=True)`。
- **平台经验（路由测试的通用陷阱，逐步沉淀中）**：`Task.apply(args=…)` 的 `args` 是关键字参数（mock 记录于 `.kwargs`）;先 `flush()` 生成主键 ID 再挂子任务 FK（否则存 NULL 查询全落空）;`expire_on_commit=False` 下 identity map 缓存旧状态须 `refresh` 才能看到路由侧独立会话的写改;函数内 `from … import celery_app` 需 patch 模块属性且 `new=MagicMock()`。
- **回归**：**547 passed**（+17，pyodbc 4 例预存失败除外）/ 总覆盖率 **68.99%**（10176 语句 / missed 3156）。
- **对 ADR 的启示**：路由层低覆盖清单再收一档——剩余 `spatial.py` 29%、`data_standards.py` 30%、`source_data.py` 27%、`import_export.py` 38%。`data.py`（真值 59%）拆分仍锁定；下一个自然目标是 `spatial.py`（188 语句，分管空间查询，与已 100% 的 `spatial_query.py` 服务层衔接）。

## 进展更新（2026-09-07 · P5e spatial.py 路由级 100%）

- **新增 `tests/test_spatial_routes.py`（30 例）** → **`spatial.py` 29% → 100%**（188 语句全清，含防御兜底可达性证明）：
  - **第 3 处「无测试覆盖→逻辑静默失效」实证（强化本 ADR 核心论据）**：`_categorize` 的 WGS UTM 分支在 `raw.lower()` 后用 `[NS]` 作正则 → 永不匹配 → 32643-32653 全部静默落入「其他」组、`WGS 84 UTM` 分组恒空且前端下拉缺失。TDD 红用例先行暴露，修正 1 行（`[ns]` + `ns.upper()`）后 8 大分组全绿。此前 P3 审计（11 处静默吞异常）与 P5b（`json` 未导入 NameError / `is_dir()` 未捕获 OSError）均为同模式——**无测试覆盖的路径必然静默失效，拆分重构必须前置测试**。
- **平台经验追加**：嵌套 helper 不可直接 import（`_categorize` 为 `crs_catalog` 内嵌函数）→ 用真实库查询驱动外部行为；catalog 未打桩会真实把自定义 SRID 注册进 spatial_ref_sys → 断言「库无行」须动态选 srid；`_run_sync` executor 内同步调用的桩须用 `MagicMock`（AsyncMock 泄漏 coroutine → `'coroutine' object has no attribute 'encode'`）；删除断言用 `select(func.count())` 直读库绕开 identity map（`expire_all()+get()` 反而触发 MissingGreenlet）。
- **回归**：**577 passed**（+30，pyodbc 4 例预存失败除外）/ 总覆盖率 **70.92%**。
- **对 ADR 的启示**：路由级低覆盖清单再收一档——剩余 `data_standards.py` 30%、`source_data.py` 27%、`import_export.py` 38%。`data.py`（真值 59%）拆分仍锁定；下一自然目标 `data_standards.py`（30%）或 `source_data.py`（27%）。

## 进展更新（2026-09-07 · P5f source_data.py 路由级 100%）

- **新增 `tests/test_source_data_routes.py`（40 例）** → **`source_data.py` 27% → 100%**（144 语句全清）：
  - **第 4 处「无测试覆盖→逻辑静默失效」实证**：字典端点仍是 v2.1"迁移后将被 StandardDictionary 替代"的半迁移遗留——探针实测 `GET|POST /source-dictionaries/*` 直接 500（`selectinload(SourceDictionary.items)` → `AttributeError: type object 'SourceDictionary' has no attribute 'items'`），list 的 `item_count` 恒 0（`DictionaryItem.dict_id` FK 已指向新表 `standard_dictionaries`）。按迁移意图修复：字典端点迁移到 `StandardDictionary`（schema 名保留 `SourceDictionary*`）。**字典相关 API 在生产中存在一律 500/计数恒 0 的已坏状态**——此前上层用它做数据浏览时展示为空,却无人深究。
  - **红用例驱动的连坐修复（3 处）**：① `FieldDictLink.dict_id` FK 仍指向旧表 `source_dictionaries` → 字典迁至新表后关联必外键违约（asyncpg `ForeignKeyViolationError`）→ 改指 `standard_dictionaries.id`（生产库须 Alembic/手动 ALTER，已记入 BASELINE §17 技术债务）；② `add_source_field` 返回未加载 `dict_links` 的对象 → 201 响应序列化 `MissingGreenlet` → 改为 commit 后重查 + `selectinload`；③ **同名路径注册竞速遮蔽**：`DELETE /field-dict-links/{link_id}` 在 data_standard_v2 子路由 `field_links.py` 与 source_data 各有一份，v2 先注册（router.py 注释"v2 路由必须在 source_data 之前注册"）→ 旧 `unlink_field_dict` 永远不可达，HTTP 实际打 v2 语义（查 `standard_field_dict_links`）。测试用 mask 用例固化该事实，legacy 实现以**函数级直调**（`await unlink_field_dict(link_id, db, api_key)`）覆盖其行。
  - **平台经验追加**：`test_db` 为 **session 级共享库**（非逐测重建）→ 全表 `func.count()` / `==n` 列表断言会被同文件前序测试（及全量回归中其他文件）残留数据污染（单跑通过、全文件跑崩正是此症状）→ 一律按**本测试自建 id** 过滤或 `>=` 断言；被遮蔽路由无法经 HTTP 达 → 函数级直调（传真实 `AsyncSession` + `ApiKey` 实例）是唯一的行覆盖通道；fix 后须同步 `FieldDictLink` 之类 FK 模型才能让 `create_all`（session 级重建 vcs_test）生效。
- **回归**：**617 passed**（+40，pyodbc 4 例预存失败除外）/ 总覆盖率 **71.95%**。
- **对 ADR 的启示**：路由级低覆盖清单再收一档——剩余 `data_standards.py` 30%、`import_export.py` 38%。`data.py`（真值 59%）拆分仍锁定；下一自然目标 `data_standards.py`（30%）。

## 进展更新（2026-09-07 · P5g data_standards.py 路由级 100%）

- **新增 `tests/test_data_standards_routes.py`（18 例）** → **`data_standards.py` 30% → 100%**（93 语句全清）：
  - **第 5 处「无测试覆盖→逻辑静默失效」实证**：`POST /data-standards/import` 以 `DataStandard(..., sheet_count=...)` 构造 ORM 对象，但模型**从未存在** `sheet_count` 列 → 每次导入**必 500 `TypeError`**（探针实测即炸)；列表 `sheet_count` 亦恒 0（schema 默认值兜底，观感正常不易察觉）。修复：模型补 `sheet_count: Integer NOT NULL DEFAULT 0` 列（生产库需 ALTER）。**导入型功能是最容易整体坏掉而无人知晓的端点类型——错误地在请求入口即抛 500,用户只会以为"文件格式不对"。**
  - 覆盖端点：模板（真实模板 200 + 缺失 404）；导入（真实 openpyxl 内存 xlsx 端到端 —— `parse_standard_excel` 不 mock,6 表全落库含 `StandardVersion.file_data` 原始字节；name 缺省取文件名 stem；非 .xlsx / `../` · `/` · `\` 文件名 / >10MB / 解析异常 / 空 sheet 全门禁）；列表（分页、page_size 422 门禁、越权不可见）；详情（字段顺序、404 与越权同语义）；删除（级联清标准/sheet/field/version,SourceTable 保留且 FK 置空）。
  - **平台经验追加**：真实二进制上传（xlsx）可用 `openpyxl` 内存构造 + `files=` 传字节走通完整链路,该路由仅两分支 mock；`settings.upload_dir` 必须 patch 到临时目录,否则校验写盘污染仓库;`StandardVersion.file_data` 是 `LargeBinary`,断言用「上传字节 == 落库字节」。
- **回归**：**635 passed**（+18，pyodbc 4 例预存失败除外）/ 总覆盖率 **73.05%**。
- **对 ADR 的启示**：路由级低覆盖清单再收一档——仅剩 `import_export.py` 38%。`data.py`（真值 59%）拆分仍锁定；下一自然目标 `import_export.py`。

## 进展更新（2026-09-08 · P5h import_export.py 路由级 100%)

- **新增 `tests/test_import_export_routes.py`（37 例）** → **`import_export.py` 38% → 100%**（448 语句全清，与 `data_standard_v2/` 其余子模块一起进入 complete-coverage 名单）：
  - **本轮未探出新坏点**（前 5 处静默失效后第一个"无实证"轮次）：探针一次性覆盖 v2 模板导入/create+update、扁平导入、CSV 导入、三模板下载、export excel/400/越权、auto-create-table——全部命中预期 200/400/404/409 语义。`import_export.py` 是 P5 系列首个**数据流扎实、无需生产修复**的路由文件，与 P5b（`json` NameError）、P5e（`[NS]` 正则）、P5f（半迁移遗留）、P5g（`sheet_count` 缺失）形成反差：**即坏点密度不均匀——但正因如此，逐文件 TDD 仍是拆 `data.py` 前不可跳过的护栏**。
  - 覆盖端点：JSON 导入（update 按 name/code 双通道 + StandardVersion 快照落库、upsert 缺省 create、404、bad mode 400、children 递归建子层组/计数、字典 items 含 parent_code）；Excel 导入（v2 四 Sheet create/update/409 + edge 模板一次性命中 `_create_v2_children` 全部边角——fallback「未分组」、非空间表类型回退 attribute、字段表编码缺失跳过、scope=global、字典项空名回退 item_code；扁平 create/update/409；bad mode/bad ext/.xls/无有效 sheet 四个 400）；CSV 导入（多文件 + 非 CSV 跳过、update 版本流、409、空文件、纯跳过）；模板（excel/csv zip 魔数 + 归档内容）；导出（excel 全树含空字典项分支、404、越权同 404、不支持格式 400）；auto-create-table（ok/404/无 field_schema 400/服务层 ValueError 400/越权 404）。
  - **平台经验追加（本 ADR 知识库）**：`authenticated` 依赖实际返回 **`AuthContext`** 而路由签名标注 `ApiKey`（`__getattr__` 委托兼容 old-style `api_key.id` 访问）——**HTTP 层测试永远碰不到这层差异**，但实现层直调（如 `_import_version_snapshot(..., api_key, ...)` 访问 `owner_id`）必须按运行期语义构造 `AuthContext(api_key=key, accessible_key_ids=[key.id])` 而非裸 `ApiKey`（裸对象缺 `owner_id` → AttributeError）。
- **回归**：**676 passed**（+37，pyodbc 4 例预存失败除外）/ 总覆盖率 **77.27%**。
- **对 ADR 的启示**：路由级低覆盖清单**清零**（`data_standard_v2/`、`source_data.py`、`data_standards.py`、`spatial.py`、`batch.py`、`quality.py` 全部 100% 或近 100%）。`data.py`（真值 59%，upload 后半段 + CRUD 子端点待补）拆分仍按本 ADR 锁定——下一步自然目标为其余低覆盖服务（`attribute_checker.py` 28%、`rule_generator.py` 18%、`dwg_converter.py` 0%、`topology` 已 100%）。

## 进展更新（2026-09-08 · P5i rule_generator / attribute_checker 服务级 100%）

- **新增 `tests/test_rule_generator.py`（8 例）+ `tests/test_attribute_checker.py`（13 例）** → **`rule_generator.py` 18% → 100%（34 语句全清）；`attribute_checker.py` 28% → 100%（185 语句全清）**。总覆盖 **77.27% → 78.83%**，**697 passed**（含此前 pyodbc 隔离环境的预存失败本次全绿）。
- **第 6 处「无测试覆盖→逻辑静默失效」实证（P5 系列首例服务层坏点）**：`load_standard_tables` 依赖 `DataStandard.layer_groups`（返回标准**全部**层组，无序过滤约束），循环体又对每个组递归 `_collect(layer)` 起步 → **任何含父子层组的标准，底层每张表都会被收集两次**，`run_attribute_checks` 对同表产出双份 A2/A3/.../A7 检查记录（A7 重复"图层无要素"误报概率、结果总量翻倍）。A1 因对表名做 set 去重，长期掩盖了重复。修复：仅以顶层组（`parent_id is None`）为递归起点（`attribute_checker.py:155-160`）。测试以真实父子层组标准 + 真实字典/条目落库验证单份收集 → **测试先行在此再次兑现：坏点只在 TDD 探针下现身，生产流量大概率表现为"质检结果莫名翻倍"而无人归因**。
- **覆盖要点**：
  - `rule_generator`：对象图直测五类规则（NOT_NULL/TYPE_MATCH/LENGTH_LIMIT/UNIQUE/VALUE_IN_SET）各分支——label 取 alias、params 组装、is_auto 开关；nullable 语义；零规则空字段；字典项 code None 过滤、空 items、空 links、非可迭代 dict_links 抛错被吞（except 兜底仍产出其余规则）；`generate_rules_for_standard` 真实库 selectinload 全链路。
  - `attribute_checker`：`load_standard_tables` 真实 ORM（not-found/递归收集/dict_items 聚合/standard_table_id 过滤）；`load_features_by_file` mock `db.execute`（三表查询、`properties=None → {}`）；`run_attribute_checks` 以 `StandardTableDef`/字典构造 + monkeypatch 两个 loader 驱动 A1–A9 全分支——多表全 pass、多表全 fail（A1 缺层/A2 混合几何/A3 缺+多+ghost 字段跳检查/A4 空串必填/A5 值域外/A6 主键重复/A8 超小数位/A9 超长）、单表模式（A1 直过、无 geometry 表 A2 不产出）、A7 空层、标准无表守卫、文件无要素守卫；`_summarize` 直测三类 severity 计数。
  - **免 PostGIS 分区表的策略**：`spatial_feature` 系 LIST 分区且无既有测试写行 → 全部经 monkeypatch 装载器覆盖，不插真实要素行（保留真实 standards/fields/dicts/links ORM 数据）。
- **平台经验补充**：pandas 2.x 字符串列 `dtype=='str'`（A4 空串掩码依赖已故版本行为，S5 先例）；`pytest-asyncio` strict 模式下 loader monkeypatch 必须替换为 **async** 函数（`run_attribute_checks` 对 loader 做 `await`）。
- **对 ADR 的启示**：服务层低覆盖清单的自然下一站为 `dwg_converter.py` 0%；`data.py`（真值 59%）仍在拆分锁定期，本 ADC 建议两线并行：dwg 转换器（嵌 GDAL 沙箱）与 `data.py` upload 后半段（既有 9 例基础上补齐）。

## 进展更新（2026-09-08 · P5j dwg_converter.py 服务级 100%）

- **新增 `tests/test_dwg_converter.py`（12 例）** → **`dwg_converter.py` 0% → 100%**（44 语句全清）。本轮**无新坏点**（LibreDWG 封装仅 90 行，纯 IO/子进程胶水，语义即代码）。
- **覆盖套路（绕开 GDAL/DLL 依赖的模板）**：模块级 `_DWG2DXF` 指向临时目录假 exe → `is_dwg_converter_available` 两态；`dwg_to_dxf` 以 `tempfile.mkdtemp` 注入 workdir + 预建 `out.dxf` 使 `os.path.isfile` 通过，随后只替换 `subprocess.run`（保留模块级 `subprocess` 引用，避免 `except subprocess.TimeoutExpired` 对 mock 求值崩溃——**坑：整体替换 `dc.subprocess` 为 mock 会让 except 子句取 `SimpleNamespace.TimeoutExpired` 抛 AttributeError**）；覆盖 returncode=0+产出文件、非 0+stderr tail 告警、产物缺失全 return None + rmtree、成功带非致命 stderr→debug 日志、TimeoutExpired→告警、任意 Exception→兜底；`cleanup_converted_dxf` None/正常删除/rmtree 抛错被吞三态。
- **回归**：**709 passed**（+12）/ 总覆盖率 **79.27%**。
- **对 ADR 的启示**：服务层低覆盖清单只剩 **`data.py`**（真值 59%，缺口集中在 upload 后半段——chunk 校验/merge/取消——与 CRUD 子端点——list/stats/detail/features/structure/download/update/delete/批量——以及 GDB/SHP/MDB 要素入库链路）。P5k 目标：补齐上传后半段 + CRUD 子端点测试，压 `data.py` 覆盖率；GDB/MDB 加载链路列 P5k-2（涉及 GDAL 沙箱或逐层 monkeypatch `_handle_gdb_group`/`_load_*_async`，体量大，建议独立一轮）。

## 进展更新（2026-09-08 · P5k data.py 82%：chunk/merge/cancel + CRUD/quality 端点）

- **P5k-1（tests/test_data_chunk_edges.py，26 例）**：chunk 上传后半 + merge + cancel + upload/MDB 边角。merge 端点**无 status_code → 默认 200**（测试初判 201 红 → 修正）；CSV 检测 geometry_type="table" → 452-468 一致性校验 400，fallback 484 只能走 **zip 主记录**（is_archive=True、geometry_type=None 跳过校验）；agg 失败 SAVEPOINT 隔离（merge 仍 200、warning 打日志）；MDB 全链路（extract 异常 400 / 子文件 standard 回填 / 无子任务 / 父 CRS 回填 / 子任务失败 warning）5 个边角。
- **P5k-2（tests/test_data_crud_quality.py，32 例）**：list 过滤（geometry_type 计数列命中 + 字符串兜底 / standard none+指定 / **from/to 日期范围**）、features/structure/download、PATCH（standard_id 回查名称 + table_id + 名称直写）、batch-classify/delete/batch-delete/cleanup、quality 端点全分支、upload 残余（xlsx 真实 schema / detect 异常兜底 / 清理异常吞 / auto_create GEOM_MAP+无几何）。
- **第 7 处「无测试覆盖→逻辑静默失效」实证（日期过滤生产 bug）**：`list_files` from/to 过滤直接比较字符串 `created_at >= 'YYYY-MM-DD'` → PostgreSQL 报 `timestamp with time zone >= character varying`，**日期过滤从上线起从未生效**（恰好是 1391/1393 行覆盖率空洞的根因）。修复：新增 `_parse_date_bound`（`fromisoformat` → UTC aware；to 侧钳至 23:59:59），TDD 红用例先行（UndefinedFunctionError 即红）。
- **不可达分支删除留档**：PATCH `standard_id=""` 清空分支（原 1687）——`standard_id` 列 FK `data_standards` 且列型 String(36)，空串写库必 FK 违约 → 该分支生产不可达，测试不做（Remove，ADR 记录）。
- **事件循环竞争坑复现并固化策略**：reindex 端点真实 `_load_all_features_to_spatial_async`（内部 `db_task_semaphore` + `AsyncSessionLocal` 独立子会话）在测试中**无限挂起**（5min 超时杀进程）——P2c 注释早已记录，本轮再次踩坑后决定**一律 mock loader**（`AsyncMock(return_value=fake_stats)`），reindex 仅测 agg 失败分支。
- **identity-map 陷阱（本文件 3 处）**：独立会话（路由 override 会话）写入后，测试会话 `db_session.get` 命中 identity map 缓存的旧对象 → 删除/更新断言必须 `populate_existing=True` 或 `await db_session.refresh(...)`（与 P5d 记录的 `expire_on_commit=False` 同一族）。
- **考场记录**：`detect_data_type('GPKG')` 返回 "geometry"（except 兜底断言初判 "unknown" 红 → 修正）；质量报告查询用模块顶部 import（别用 `__import__` 拼接）；xlsx 上传真实 openpyxl 解析（field_schema 2 列）。
- **回归**：**767 passed**（+58）/ 总覆盖率 **82.21%**；`data.py` 59% → **82%**（287→181 miss；P5k-1 后 72%、P5k-2 后 82%）。
- **对 ADR 的启示**：`data.py` 剩余 181 miss 全部集中在 **GDB/SHP/MDB 加载链路大块**（647-704/765/785-786/800-804/1143-1305/1704/1910-2284：`_handle_gdb_group`/`_load_*_async`/merge 分区/归档提取残留）。列 **P5k-3** 独立一轮：策略 = 逐层 monkeypatch（`archive_extractor`/`mdb_processor`/`_handle_gdb_group` 返回桩 dict）+ 真实 zip/geojson 字节驱动 `_load_extracted_features_async`/`_load_mdb_features_async`（`AsyncSessionLocal` patch，避开 P2c 事件循环竞争）；SHP/GDB 原生读写需 GDAL → 优先 monkeypatch 边界函数。`data.py` 拆分仍按本 ADR 锁定（P5k-3 后达 ~90%+ 再评估拆 upload/merge 小节）。

## 进展更新（2026-09-08 · P5k-3 data.py 82%→98%：GDB/SHP/MDB 加载链路收口）

- **P5k-3（tests/test_data_p5k3.py，27 例）**：upload ZIP 全类型（GDB 组 + SHP-companion skipped detail + tables + CSV + GeoJSON gather，断言 detail 回填特征数）；GDB loader 函数级（`_load_gdb_layer_async` to_thread 转发 / `_sync_load_gdb_layer` happy+crs_warn+except，`SyncSessionLocal` 用 MagicMock 上下文钩子、`gdb_read_lock` 真 CM）；`_handle_gdb_group`（no_layers 清理 / 2 层均摊 201 / happy）；提取 loader 直调 7 例（`_load_extracted_features_async`/`_load_shp_group_features_async` happy/crs_warn/非矢量/except，`AsyncSessionLocal`→`_TestSession`）；收尾 8 例：upload SHP 主文件、非压缩 agg 失败告警、merge SHP+矢量+纯表格（无 sub_tasks 分支）、reindex 403 无权、`_handle_gdb_group` 无 gdb_path 清理兜底、MDB loader 空 features+crs_warn+except、全量 loader crs_warn。
- **端点归属修正（关键侦察结论）**：647-768 属 **upload_file**（同步单文件上传），1143-1312 属 **merge_chunks**——两者 ZIP 分支语义不同：**upload_file 对纯伴生 SHP 产出 `SHP-companion/skipped_no_shp` detail + ignored detail；merge_chunks 对无主 SHP/ignored 直接 continue 不产 detail**（测试断言错位初版红 → 修正）。
- **autouse fixture 坑**：`app.state.limiter` 仅在 client fixture 触发 lifespan 后存在，函数级直调测试（无 client）触发 `AttributeError: module 'app' has no attribute 'state'` → 限流 fixture 改防御式 `getattr`。
- **`ApiKey` 无 `owner_id` 列**：`ApiKey(key_hash=..., name=..., owner_id=None)` 构造 TypeError → 移除 owner_id（用 `api_key_owner_id` 参数传递属主）。
- **`gdb_read_lock` 需真 context manager**：`SimpleNamespace(__enter__=...)` 非 CM → `with lock:` TypeError → 复测走 except 分支而非目标分支（改用 `_Ctx` 类）。
- **session 级 DB 复用 → 计数断言禁用绝对数**：`len(rows)==2` 在整文件跑时命中前序测试残留（10==2 红）→ 一律 **before/after 差分**或限定 `api_key_id` + 差分。
- **mock loader 须回传真实 stored_file_id**：patch `_load_shp_group_features_async` 用 `return_value=fake_stats("shp-sf", 5)` 时 detail 回填（1293-1296 按 `detail.file_id` 查 stats）失配 → 改 `side_effect=lambda *a, **k: fake_stats(a[0], 5)`。
- **P5k 全轮留档清单（20 行，全部有据）**：133-139 死代码（无调用方）、274-277 fiona 表解析最外层 except、331 空文件名 400（Form 422 先拦）、416 auto_create 无检测几何（detected=None 与 field_schema 非空在该路径互斥）、800-804 gdb 组必有 sub_task else 永不达、1704 standard_id 空串 FK 违约（同 1687）、2129 伴生 dict 永无 .shp key（classify 分离）、2170-2171 rmtree ignore_errors=True 兜底。
- **回归**：**795 passed**（+28）/ 总覆盖率 **84.07%**；`data.py` 82% → **98%**（181→20 miss，全部留档）。P5k 累计 **85 例**，`data.py` 从 59% 到 98%。
- **对 ADR 的启示（拆分决策更新）**：`data.py` 拆分仍是本 ADR 的原始主线，但覆盖已达 **98%**——「无测试覆盖→逻辑静默失效」风险已消除（迭代中揪出第 7 处生产 bug：日期过滤）+ 上传/合并两条大链路全分支被真实 ZIP 字节 + mock 边界钉死。拆 upload/merge 小节此刻**低收益、非零回归风险**，ADR 结论调整：**拆分暂缓，条件触发**——仅当 data.py 新增第三方依赖/功能膨胀至 >1500 行或出现拆分实际触发场景（如 worker 独立部署）时再拆；届时以当前 27 例函数级直调测试为分片安全网基线。

## 进展更新（2026-09-08 · P5l gdb_processor/spatial_loader 服务级双双 100%）

- **P5l（tests/test_service_gdb_spatial.py，71 例）**：P5k 把 data.py 的 GDB/SHP/MDB 加载链路压到 98%，但全程 monkeypatch 钉死两个服务本体（gdb_processor 27%、spatial_loader 33%）——本轮按 P5j 体术（纯服务直调 + 边界 monkeypatch，冷启动无 DB 依赖）补齐。**gdb_processor 27%→100%（95 语句）、spatial_loader 33%→100%（187 语句）**。
- **fiona patch 绑定**：gdb_processor 模块级 `import fiona` → monkeypatch `gdb_mod.fiona` 有效；spatial_loader 函数内 `import fiona` → 模块属性不存在（`AttributeError: module 'app.services.spatial_loader' has no attribute 'fiona'`）→ 必须 patch 顶层 `fiona.listlayers/open`（fake 体系 `_FakeFiona/_Src/_Feat/_FeatSrc`）。`dwg_to_dxf/cleanup_converted_dxf` ← `app.services.dwg_converter`；`parse_obj_file/obj_to_geojson_features` ← `app.services.obj_parser`。
- **CAD invalid 环的测试形态陷阱**：shapely 2.1 自交环构造**不抛异常**但面积恒为 0 —— make_valid 兜底分支（invalid + area>0）真实几何走不进 → 哨兵类驱动（`_BadPoly` area=3.0/is_valid=False + `make_valid`→单 Polygon 或 MultiPolygon 双形态）；退化 0 面积环（4 点闭合全同点）→ `continue` 丢弃、无产出时保底保留原要素。
- **MultiLineString 输出语义**：open 线保留输出类型仍是 MultiLineString（非 LineString）；`_is_closed_ring` 至少 4 点；`normalize_stored_geometry_type('3D MultiPolygon')=='Polygon'`（Multi→Polygon 归一）。
- **回归**：**866 passed**（+71）/ 总覆盖率 **85.99%**。P5l 之后剩余低覆盖服务：`feature_store.py` 53%（列 **P5m**，含 DB 交互——复用 `_TestSession`/db_session 体术）、`attribute_processing.py` 38%、`crs_transform.py` 52%、`core/cache.py` 46%。
- **对 ADR 的启示**：`data.py` 拆分暂缓维持 P5k-3 结论（98% + 27 例安全网基线的触发条件制）。服务层从「被子模块钉死」逐轮解放，本轮再添 2 个 100% 服务，加载管道（extract→prepare→read→写库）测试纵深已直通服务本体。

## 进展更新（2026-09-08 · P5m feature_store.py 服务级 100%）

- **P5m（tests/test_feature_store_p5m.py，33 例）**：**`feature_store.py` 53%→100%**（198 语句全清）——数据管道三服务（extract/加载 → gdb/spatial → feature_store 写库）至此全 100%。
- **同步/异步会话两套 fake**：feature_store 内 async 函数（`await db.execute`/`await db.flush`）与 sync 函数（`drop_partition_sync`/`write_features_batch_sync` 非 await `session.execute`/`session.flush`）共用同签名会话——`_FakeDB`（async execute/flush）与 `_FakeSyncSession`（sync execute/flush）必须分开；同名方法会由后定义覆盖（曾致 `await db.execute` 递归 coroutine TypeError）。
- **语句产出断言**：`_ensure_partition`/`drop_partition` 等纯 SQL 编排用队列喂结果，逐条断言 CREATE/DETACH/DROP 语句与 flush 次数（quotes 转义、幂等跳过、ensured set 去重均钉死）。
- **真实几何修复/变换**：`repair_features_by_file` 用真实 shapely bowtie（`POLYGON((0 0,2 2,0 2,2 0))` is_valid=False）→ `buffer(0)` 修复；478 make_valid 兜底需 `buffer(0)` 产物仍无效——返回自交 Polygon 让真实 `make_valid` 按其语义修复（函数内 `from shapely.validation import make_valid` 仍走真实实现）。`transform_features_by_file` monkeypatch `transform_engine.resolve_transform_crs`/`transform_geometry`（模块路径），断言 WKTElement.srid 按目标 CRS 标注。
- **真实 PostGIS 冒烟 FK 依赖**：`aggregate_layer_stats` 写 `stored_file_layers`（FK `stored_files.id`）+ `update stored_files` → 冒烟必须先生成 `StoredFile` 记录 + 真实 `api_key.id`（`api_key_id` 是 FK 非 Nullable）。
- **回归**：**899 passed**（+33）/ 总覆盖率 **86.91%**。
- **对 ADR 的启示**：数据管道三服务全 100% 后，P5 剩余低覆盖集中为 `attribute_processing.py` 38%、`crs_transform.py` 52%、`core/cache.py` 46% 与路由层 `admin.py` 39% / `quality_plans.py` 44%。`data.py` 拆分暂缓结论不变——分片安全网（27 例函数级直调）仍有效，但随 feature_store 本体 100%，写库链路同源安全网可由服务级直调替代。

## 进展更新（2026-09-08 · P5n attribute_processing.py 服务级 100%）

- **P5n（tests/test_attribute_processing_p5n.py，25 例）**：**`attribute_processing.py` 38%→100%**（152 语句全清）——数据管道四服务（属性处理 → 加载 → gdb/spatial → feature_store 写库）全 100%。
- **真实 DBF 二进制夹具**：手搭 dBase III 无 memo 格式——header（版本/日期/nrec/hdrlen/reclen/reserved）+ 32B 字段描述符（name[0:11]/type[11]/addr[12:16]/len[16]/dec[17]）+ 0x0D 终止 + 定长记录（首字节删除标记）。钉死 `struct.unpack('<I'/...)` 头解析、C/N/F/I/L/D 各类型解码、`rstrip(b' \x00')` 空格剥离、`0x2A` 删除记录跳过、GBK 中文解码、记录截断 break。
- **chardet 函数内 import 陷阱**：`_process_dbf`/`_process_csv` 内 `import chardet` → 模块属性不存在（`AttributeError: module ... has no attribute 'chardet'`）→ 必须 patch 顶层 `chardet.detect`。
- **DBF 终止 vs 空名是两条分支**：94 行 `raw[0] in (0x0D, 0)` → break（终止符块）；96 行 name 解码**空**→ continue（空名块）。注意空名块若用 0x00 填充会触发 break 而非 continue——须用**空格**填充（raw[0]=0x20 非终止，name strip 后空）才走 continue。
- **critical 字段语义澄清**：`_ensure_critical_fields` 把 YWBH/FEATURECODE 追加到 **select_fields** 列表；但输出 `fields` 是「源列 ∩ select_fields」——critical 字段**仅在源实际存在该列时**出现在输出，不凭空加列。大小写不敏感命中（已有 `ywbh` 则不再追加 `YWBH`）。
- **回归**：**924 passed**（+25）/ 总覆盖率 **87.84%**。
- **对 ADR 的启示**：P5 服务层低覆盖只剩 `crs_transform.py` 52%、`core/cache.py` 46%；路由层剩 `admin.py` 39%/`quality_plans.py` 44%。数据管道四服务 100% 后，上传→质检主链路服务层测试深度已贯穿最底层格式解析，`data.py` 拆分暂缓结论不变。

## 进展更新（2026-09-08 · P5o crs_transform.py 服务级 100%）

- **P5o（tests/test_crs_transform_p5o.py，22 例）**：**`crs_transform.py` 52%→100%**（60 语句全清）。
- **别名表掩盖 isdigit 分支**：CRS_ALIASES 含 "4490"/"4326" → 测纯数字 isdigit 分支须用**不在别名表**的数字（如 4646），否则被别名先截走。
- **pyproj / builtins.open 两处 patch 路径**：`resolve_crs`/`transform_coords` 函数内 `from pyproj import ...` → patch 顶层 `pyproj.CRS`/`pyproj.Transformer`（类 + 静态方法 from_user_input/from_crs）；`extract_crs_from_prj` 的 `open` 是 **builtins**（模块未 import），patch `ct.open` 无效 → 须 `builtins.open`。
- **回归**：**946 passed**（+22）/ 总覆盖率 **88.13%**。
- **对 ADR 的启示**：P5 服务层低覆盖剩 `core/cache.py` 46%；路由层剩 `admin.py` 39%/`quality_plans.py` 44%。`data.py` 拆分暂缓结论不变。

## 进展更新（2026-09-08 · P5p core/cache.py 服务级 100% + 缺陷修复）

- **P5p（tests/test_cache_p5p.py，14 例）**：**`core/cache.py` 46%→100%**（53 语句全清）。
- **FakeRedis 双 async 方法**：get_redis 用 `_cache_client` 全局，测试先置入 FakeRedis（async ping/get/set），autouse fixture 每次复位全局；`cached` 消费同一全局。
- **真实缺陷修复**：模块加载期 `_cache_client: aioredis.Redis | None` 与 `async def get_redis() -> aioredis.Redis | None` 的注解**运行时求值**——`redis.asyncio` 缺失时 `aioredis=None` 在注释行即抛 `AttributeError`，整个"优雅降级"被击穿。仿模块重载测试（blocked `__import__` + `sys.modules` del + `importlib.import_module`）钉死此崩溃路径 → 修复为 `from __future__ import annotations` 全模块延迟注解求值。无 redis 环境 `import app.core.cache` 现在正常，`get_redis()` 返回 None、`cached` 装饰器退化为直跑。
- **回归**：**960 passed**（+14）/ 总覆盖率 **88.40%**。
- **对 ADR 的启示**：P5 服务层清零——`data.py` 拆分暂缓结论不变；剩余低覆盖仅路由层 `admin.py` 39% / `quality_plans.py` 44%。

## 进展更新（2026-09-08 · P5q quality_plans.py 路由级 100%）

- **P5q（tests/test_quality_plans_p5q.py，14 例）**：**`app/api/v1/quality_plans.py` 44%→100%**（81 语句全清）。
- **真实 PostGIS integration**：conftest `test_db`（session 级建表→删表 + 清 override）+ 模块级 autouse 包装 + `client`（httpx ASGI）+ `api_key_header`；6 端点全分支直打 HTTP。
- **URL 前缀坑**：`quality_plans` router 的 prefix `/quality-plans` 挂载在 `app.api.v1.router`（prefix `/api/v1`）下 → 完整路径 **`/api/v1/quality-plans`**；裸 `/quality-plans` 全部 404（首个失败即此，22 处 URL 统一加前缀后全绿）。
- **pytest-asyncio STRICT**：14 例 `async def test_*` 逐一加 `@pytest.mark.asyncio`（缺失时报 "async def functions are not natively supported"）。
- **回归**：**974 passed**（+14）/ 总覆盖率 **88.85%**。
- **对 ADR 的启示**：P5 剩余低覆盖仅路由 `admin.py` 39%——P5r 为 P5 收官。`data.py` 拆分暂缓结论不变。

## 进展更新（2026-09-08 · P5r admin.py 路由级收官 100% · P5 系列全目标完成）

- **P5r（tests/test_admin_p5r.py，28 例）**：**`app/api/v1/admin.py` 39%→100%**（127 语句全清）——P5 覆盖目标清单全部清零。
- **9 组端点全分支**：apikeys 创建/列表/软删/激活/重生成/硬删 + key-group 分配/移出 + groups 创建(409)/列表(子查询 member_count 只计活跃)/详情(成员)/更新(404/409)/删除(置空)。管理端鉴权 `_verify_admin` 403 分支与 test_api.py 已有 3 例互补覆盖。
- **pytest-asyncio STRICT**：28 例 `async def` 逐一加 `@pytest.mark.asyncio`。
- **`expire_on_commit=False` + async 会话陷阱**：`test_db` 会话 commit 后对象不自动过期——路由更新后复用同会话 `db_session.execute` 读到的仍是**内存旧值**（断言失败）；补 `db_session.expire_all()` 强制重查又触发 async **MissingGreenlet**（惰性加载不在 greenlet 上下文）。正解：断言改开**独立 `_TestSession()`**（conftest 导出）重读，避开共享会话 identity map。
- **回归**：**1002 passed**（+28）/ 总覆盖率 **89.61%**。
- **P5 全系列收官**：P5i→P5r 共 12 轮，全量 697 → **1002 passed**（+305 例），覆盖 78.83% → **89.61%**；服务级 8 服务 + core/cache + 路由 quality_plans/admin 全 **100%**，`data.py` 98%。
- **对 ADR 的启示（最终）**：`data.py` 拆分**暂缓结论成立**——如今覆盖 98%、全分支留档 20 行，且周边全部 100% 后，拆分纯属重构无行为变更，风险大于收益。ADR-0001 的核心主张 A（先测试后拆分）已由 P5i-P5r 验证：**测试先行没有触发拆分需求**。若未来 `data.py` 出现新功能变更导致单文件超 1500 行或出现部署场景，再重新评估。

## 进展更新（2026-09-09 · P6 服务层收官：17 服务/模型 100% · 1296 passed / 94.60%）

- **P6 系列共 5 轮（P6a→P6e）**，服务层**剩余低覆盖文件全部清零**（P5 收完路由与 8 服务后，P6 收清单中剩余的 services/* 与 3 个标准模型）：
  - **P6a `quality_checker.py` 100%**（`tests/test_quality_checker_p6a.py`，19 例）：五维评分公式/编排守卫/violation 样本双来源/legacy 兼容 → 全量 **1021 passed / 90.09%**
  - **P6b `custom_crs.py` 100%**（`tests/test_custom_crs_p6b.py`，55 例）：srid 解析/EDS 注册/自愈（非法 proj4/坏 WKT）/换盐/真实分区 extent → **1076 passed / 91.04%**
  - **P6c `transform_engine.py` 100%**（`tests/test_transform_engine_p6c.py`，31 例）：统一解析/Custom 展开/proj4 回填/变换异常桩 `_BoomTransformer`/NULL 与超域失败路径/真实分区 `transform_db_features` → **1107 passed / 91.75%**
  - **P6d `format_detection.py` 100%**（`tests/test_format_detection_p6d.py`，99 例）：置信度分级 0.1/0.6/0.85/0.95、magic 全签名、DBF/KML/OBJ/CSV/XLSX schema、类型推断兜底；全 tmp_path 真实文件 → **1206 passed / 93.27%**
  - **P6e 小尾巴轮 13 文件 100%**（`test_tail_archive_p6e.py` 30 例 + `test_tail_data_p6e.py` 27 例 + `test_tail_misc_p6e.py` 24 例）：`archive_extractor.py`、`data_standard_parser.py`、`esri_shapebin.py`（含 `__main__` 块 fake pyodbc runpy）、`export_service.py`、`mdb_processor.py`（fake pyodbc 全流程）、`progress_tracker.py`（fake redis 成功路径）、`script_generation.py`、`structure_analyzer.py`、`quality/reporter.py`、`workers/beat_schedule.py`（stat EACCES 跳过不中断）、`standard_dictionary.py`、`standard_field_dict_link.py`、`standard_layer_group.py` → **1296 passed / 94.60%**
- **死代码留档 2 处（`# pragma: no cover`）**：① `script_generation.py` 239-242——`ast.BoolOp` 在 Python 语法中只可能是 and/or（`&`/`|` 是 `BinOp`），防御分支不可达；② `script_generation.py` 265——`_to_safe` 已拒绝全部 Call/Attribute/Subscript，for-walk 永不到达。另删除 `standard_field_dict_link.py` 重复定义的同名 `dict_name` property（第一份必被第二份覆盖，属死代码；保留 try/except 兜底版）。
- **平台经验**：SQLAlchemy relationship 对 transient 实例赋值必须用**真实 ORM 对象**（plain object 报 `'_sa_instance_state'` 缺失）；transient 实例访问未设置关系返回 None 不抛异常 → 覆盖「访问抛异常兜底」需 `PropertyMock(side_effect=…)` monkeypatch 关系属性；`coverage run -m pytest` 与 pytest-cov 冲突（重复收集告警），按既定模式直接 pytest 采集后 `coverage report --include=` 读 .coverage。
- **对 ADR 的启示**：P6 与 P5 结论一致——**测试先行持续兑现**（本轮 0 个探针级坏点，死代码 pragma 2 处均为证实不可达的防御分支，无行为变更）；全仓 10184 语句 missed 551（94.60%），剩余低覆盖均登记 `BASELINE.md §17`（`main.py` 76%、`core/temp_cleanup.py` 54%、`db/session.py` 69% 等应用骨架与运维路径）。`data.py` 拆分暂缓结论不变。
