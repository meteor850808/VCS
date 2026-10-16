# v2.22.1 修订记录

> **版本**: v2.22.1
> **日期**: 2026-09-28
> **批次**: 架构优化 — 孤儿模块清除 + pytest 基建现代化 + 审慎放弃记录
> **契约守卫**: 全量回归（1525 passed in 44.66s）· `git diff` 装饰器账目复核（删除 629 / 新增 0）

---

## 一、孤儿模块清除：`app/services/quality/scoring.py`

### 背景
架构评审（Top 1 发现）指出 `scoring.py` 为五维评分模型孤儿实现。经证据核实：

- **生产零引用**：`scoring_in_prod = 0`，无任何 route/service/worker 导入；
- **真实评分路径存在两处且均独立**：
  - `app/services/quality_checker.py:345` — `_calculate_score_legacy`（单维通用评分，生产质量报告实际走此路径）；
  - `app/services/quality/reporter.py` — `_score_color`（评分颜色阈值）；
- **算法不一致**：scoring.py 的五维（完整性/准确性/一致性/唯一性/拓扑）weighted clamp 模型与 `_calculate_score_legacy` 的单维 generic 算法是**两套互不兼容**的实现——从 2026-06-18 Phase A 起从未接线；
- archive 文档（quality-check-refactor-plan.md）早以 "五维评分模型从未调用" 标注为死代码。

### 变更
- 删除 `app/services/quality/scoring.py`（85 行）；
- 删除 `tests/test_scoring.py`（16 例，该模块 0% → 100% 的唯一夹具）；
- **保留** `rule_engine.py`（同为质量子包孤儿但角色不同）：它是生产内**唯一的规则执行引擎**（`execute_rules` / `_RULE_CHECKERS`），存在待接线价值，删除将丢失唯一执行器能力 → 保留为 v2 规则执行接线对象。

### 验收
| 项 | 结果 |
|----|------|
| 残留 import 引用 | **0**（app + tests 双重 grep）|
| quality 相关测试 | **75 passed in 1.34s** |
| 全量回归 | **1525 passed** |

---

## 二、pytest 基建现代化：`asyncio_mode=auto`

### 背景
测试套件 629 处手写 `@pytest.mark.asyncio` 属 pytest-asyncio 旧式写法；pytest 9.0.3 / pytest-asyncio 1.4.0 已支持 `asyncio_mode=auto`（async def test 自动收集，无需装饰器）。

### 变更
- `pytest.ini` 新增 `asyncio_mode = auto`（注释含 v2.22.1 批次说明）；
- 全仓删除 **629 处** `@pytest.mark.asyncio`（AST 脚本逐文件处理，覆盖 41 个测试文件）；
- **账目闭环**：`git grep -c` 复核 HEAD 基线 = 629 行；工作区 grep = 0 残留；`git diff HEAD` 复核删除 629 行 / 新增 0 行——无装饰器误删、无其他 `@` 装饰器受影响。

### 漏跑误报纠正
早期帧扫描声称 "45 例 async 测试缺装饰器永久漏跑"（test_data_download_export ×13 / test_spatial_query ×17 / test_ws_flow ×13 / test_ws_guard ×3）。**git HEAD 复核为误报**——这些装饰器挂在**类级**（`@pytest.mark.asyncio` 置于 class 上，pytest 传播到类内 async 方法），AST 脚本只检查函数级 decorator_list 故误判缺失。真实基线 **0 缺漏**。全量收集数账目自洽：1541 − 16（test_scoring 删除）= 1525。

---

## 三、conftest 加固：审慎放弃（附审计结论）

### 提议（架构评审 Top 2）
`tests/conftest.py` 的 `client` fixture 不依赖 `test_db`/`api_key_header`，存在测试误连生产库 `vcs_db` 的理论风险。

### 审计结论（否决加固）
- conftest 注释明确声明非 autouse 是有意的分层设计：**纯路由/健康检查测试不应触碰数据库**，强制依赖会拖慢并扩大测试面；
- 19 个仅依赖 `client` 的测试函数逐一核查：11 例（health/401/api-key）确无 DB 触碰，8 例经 `_file_with_point_feature -> db_session -> test_db` 链路**已显式到达 test_db**——无 "client 裸连生产库" 路径；
- 14 处 `_autouse_test_db` 包装属既定 3A 文件夹具模式（每文件 1 处），去重收益 <1 行/文件且引入跨文件隐性依赖，**负收益**。

### 结论
不做改动。完整审计证据留存本档，避免后续重复评估。

---

## 四、验收汇总

| 项 | 结果 |
|----|------|
| main.py version 字段 | `2.22.1` ✅ |
| 六锚文档批次 | revision_history/CONTEXT/BASELINE/deploy_linux/deploy_windows/restart 全命中 v2.22.1 ✅ |
| 全量回归 pytest（Redis 在线）| **1525 passed in 44.66s** |
| 装饰器账目 | HEAD 629 = 删除 629 + 残 0，新增 0 ✅ |
| scoring 残留 | app/tests 双 grep 0 ✅ |
| README/BASELINE 目录树 | quality 子包 6 → 5 files ✅ |

---

## 五、二次修复批（架构报告剩余发现，2026-09-28）

### 5.1 `_redis_available` 双实现收敛
- **问题**: `app/api/v1/batch.py` 与 `app/api/v1/convert.py` 各自维护同构的 `_redis_available()`（`redis.from_url` + 2s 超时 + `protocol=2`），仅 docstring 不同，存在未来演进漂移。
- **修复**: 收敛到 `app/core/cache.py` 新增 `redis_available()`（同步探活，语义逐字节与旧实现一致），batch/convert 改 import 引用，**双份实现删除**。
- **测试适配**: 原测试盯 `app.api.v1.batch._redis_available` / `cv._redis_available` patch 目标，收敛后失效（`AttributeError: does not have the attribute`）→ patch 目标改 `app.api.v1.batch.redis_available` / `cv.redis_available` 模块符号（测试归属正确地转移到共享函数）。

### 5.2 CRS→WKT 兜底对齐
- **问题**: data.py `_resolve_export_wkt` 与 spatial.py 内联目标 WKT 解析两处行为不一致：data.py 有 `logger.warning`、spatial.py 无；data.py 硬编码 `910000` 绕过 `CUSTOM_SRID_BASE` 常量。
- **修复**: data.py `if srid < 910000` → `if srid < CUSTOM_SRID_BASE`（常量引用）；spatial.py pyproj 兜底 `except Exception:` → `except Exception as _crs_e: logger.warning(...)`（可观测性对齐）。**零行为变更**，两处注释互相指向权威实现。

### 5.3 StoredFileBrief `layer_name` 漂移缺陷（真实 bug）
- **问题**: `StoredFile` ORM 与 `StoredFileBrief` schema 均有 `layer_name` 字段，但 data.py 分页列表手工 18 字段构造块**漏掉 layer_name**，导致列表接口丢弃该字段；且手工逐字段复写是 schema 演进拖尾漂移温床。
- **修复**: `items = [StoredFileBrief.model_validate(sf) for sf in rows]`（from_attributes=True 自动映射），消灭手工块。仅此一处 `StoredFileBrief(` 构造点，影响面收敛。

### 5.4 restart.bat 回退端口 45680→45681
- **问题**: restart.bat L78 端口占用 fallback 跳到 45680，但 v2.5 已记录"恢复端口 45679（曾被改为 45680）"——45680 是**历史错误端口**，fallback 残留与错误历史语义纠缠；BASELINE 端口表也把它标为"备用"。
- **修复**: fallback 改 `set PORT=45681`（干净独立备用，含注释说明），BASELINE 端口表 45680 行改 45681 并标注废弃。

### 5.5 CONTEXT.md 目录计数实测更新
| 项 | 文档旧值 | 实测值 |
|----|----------|--------|
| API 端点 | ~111 | **147**（含 1 WebSocket） |
| ORM 模型 | 20 | **28** |
| 路由模块 | 11 | **26**（含 cad/ data_standard_v2/ 子目录） |
| services | 23+6 | **32**（27 top + 5 quality） |
| 测试文件 | — | **77**（1525 例） |
| scripts | 15 | **16** |
| docs/archive | 38 | **31** |

### 5.6 pytest.ini 覆盖率注释
- 注释 "当前 41%" 严重过时，实测 **98.40%**（远超 `--cov-fail-under=25` 基线），已更新。

### 5.7 验收
| 项 | 结果 |
|----|------|
| `_redis_available` 残留 | 0（实现符号）；test_progress_tracker 的 `test_redis_available` 为测试命名，非实现引用 ✅ |
| 全量回归（Redis 在线）| **1525 passed in 49.93s** |
| 失败诊断 | 收敛后 10 例因 patch 旧符号失败 → 改 patch 新符号后 33 passed → 全量绿 |

---

## 六、第三批（假引用修正 + 事务边界 ADR，2026-09-28）

### 6.1 `_infer_type` 假引用与双副本标注
- **核实**：`format_detection._infer_type`（L646，GeoJSON 采样推断，宽松版：bool→string/int/float/string，不解析字符串）**非死代码**——L472 内部调用 + `test_format_detection_p6d.py` 5 断言，双引用。其与 `quality/rule_engine._infer_type`（L179，TYPE_MATCH 规则用，字符串 numeric 解析增强版：`"42"→integer`）是**语义不同的两份独立实现**，`test_rule_engine.py` 7 断言覆盖增强版。
- **修正**：rule_engine.py:180 docstring 原声称"与 `quality_checker._infer_field_type 一致"——**该函数实不存在（grep 0 匹配），属假引用**。改为准确描述 + 差异说明。format_detection._infer_type 上方补注释，标注 rule_engine 增强副本存在，双向可见。
- **不强行合并**：两者分属独立模块内聚（规则引擎 vs 格式检测），共享会引入跨域耦合；string 解析语义差异被各自测试锁定。

### 6.2 服务层事务边界 → ADR-0002
- **测量**：services 28 文件 0 commit / 5 db.add / 47 查询；api/v1 13 文件 58 commit / 39 add / 98 查询；quality/ 6 文件 0 commit。
- **风险扫描**：按端点切块的 `db.add` 无 `db.commit` 端点数 = **0**，无静默丢弃分支。
- **决策**：保持 Unit-of-Work 在 API 层（ADR-0002 已立档），服务层按注入 session 契约不自行 commit；新增端点级扫描作为回归守卫；触发重评估条件已记录。

### 6.3 验收
| 项 | 结果 |
|----|------|
| rule_engine + format_detection 目标测试 | 133 passed ✅ |
| 全量回归（Redis 在线）| **1525 passed in 89.03s** |
| ADR 目录 | ADR-0001 + 新增 **ADR-0002** |

---

## 七、覆盖回跌技术债收口（2026-09-28）

BASELINE §17「覆盖回跌」项：P7c（v2.17.0）全库 100% 后，v2.18 CAD + v2.19 导出新增代码未配套全覆盖，v2.20.0 实测 98.94%（118 miss）。本批按文件补齐分支/异常契约测试，**优先收回 CAD 链 + 日志轮转四个文件**。

### 7.1 `logging_setup.py` 90% → 100%
- `tests/test_core_a_p7a.py::TestLoggingSetup` 追加 2 例：
  - `test_no_file_handler_when_log_file_empty`：`log_file=""` 短路（哨兵值验证不触碰 sf 属性）。
  - `test_handler_graceful_degrade_on_oserror`：`patch.object(logging_setup.Path, "mkdir", side_effect=OSError)` 命中 `except OSError` 降级。
- **技巧沉淀**：`patch.object(模块, "RotatingFileHandler")` 会破坏 L30 `isinstance` 检查（TypeError）——改 patch `Path.mkdir` 抛异常更贴近真实场景。

### 7.2 `cad_parser.py` 94% → 100%
- `tests/test_cad_parser.py` 新增 `TestCADParserBranchCoverage` 5 例：
  - `aci_to_rgb` 中段公式分支（10–249）+ 越界 None（250/1000）。
  - `extract_metadata` 检测 DIMENSION/INSERT/HATCH → `has_dimensions/has_blocks/has_hatches` 置位（mock modelspace）。
  - metadata extent header 读取抛异常 → 回退 `(0,0)/(100,100)`。
  - `_entity_to_info`：DIMENSION `get_text` 抛异常吞掉、实体属性访问异常 → None。
- fixture 实证：`simple/complex.dxf` 均**不含** DIMENSION/INSERT/HATCH（复杂 fixture 仅 LINE/CIRCLE/LWPOLYLINE/MTEXT/TEXT），故 has_* 分支必须 mock。

### 7.3 `cad_cache.py` 70% → 100%（新建 `tests/test_cad_cache.py`）
- 全模块此前**零直接测试**（70% 为 cad 路由间接带出），新建 13 例覆盖 5 组分支：
  - `cache_cad_snapshot` 异步入口：空 content 短路（哨兵保留）/构建异常记告警/none 结果不赋值/成功赋值。
  - `_build_snapshot`：.dxf 真实 DXF 字节全链路、未知后缀按 .dxf、.dwg 转换成功/失败短路、解析异常兜底、finally 清理临时文件。
  - `_generate_previews`：目录创建失败/SVG+PNG 成功返回 svg 路径/生成异常吞掉返回 None。
- **patch 目标注意**：`dwg_to_dxf`/`CADParser`/`CADPreviewGenerator` 均为函数内 import——须 patch 真实模块路径（`app.services.dwg_converter.dwg_to_dxf` 等），`app.services.dwg_converter` 包属性访问会 AttributeError。

### 7.4 `cad_preview.py` 81% → 100%
- `tests/test_cad_preview.py` 新增 `TestCADPreviewFallback` 12 例：
  - SVG/PNG 渲染失败 → 占位 SVG（`Preview unavailable`）/占位 PNG（`\x89PNG`）。
  - `_fit_svg_viewbox`：无 bbox 数据 → None、bbox 计算异常 → None、有数据重写 viewBox + preserveAspectRatio。
  - `_render_pillow`：无坐标点直接返回 img、TEXT/ELLIPSE 绘制异常吞掉、图层色板越界 `(0,0,0)`。
  - `_fallback_png` 无 Pillow → `_MINIMAL_PNG` 1×1 透明 PNG。
- **技巧沉淀**：MagicMock 的 `hasattr` 恒 True + 属性自动创建会导致 `tuple(ent.dxf.start)[:2]` 变空元组（IndexError）——实体用 `SimpleNamespace` + 手工 `dyftype=lambda`，坐标用 `MockVec3`（`.x/.y/.z` + 可迭代）。

### 7.5 验收
| 项 | 结果 |
|----|------|
| 四个文件独立覆盖（term-missing） | `logging_setup`/`cad_parser`/`cad_cache`/`cad_preview` 均 **100%（0 miss）** |
| 目标测试 | 97 passed（4 文件）|
| 全量覆盖 | **98.94% → 99.58%**（11094 stmts / 47 miss，118→47 清零 71 行）|
| 全量回归（Redis 在线）| **1557 passed in 77.39s**（+32 例）|
| 剩余技术债 | `export_service.py`(34)/cad 路由(4)/`data.py`(7) 低频异常分支 → 下一批 |

## 八、覆盖回跌收官 — 全库 100% 里程碑（2026-09-28）

批七遗留的 47 miss 在本批全部清零（47 → 0），**全库覆盖第二次达 100.00%**（11094 stmts / 0 miss）。四个文件名下分述：

### 8.1 `export_service.py` 34 → 100%（新建 `tests/test_export_service_branches.py`）
20 例覆盖 export_service 全部残留低频分支：
- `_build_gdb_schema`：跨行 int64 升级（int→int64→DOUBLE）/bool→str/bool→TEXT/无几何 raise/`layer_name` 计入 count（props_schema 非空时 count==2）/空 props → `fid` int。
- `_gdb_origin`：空坐标跳过/全空 → `(0,0)`/负坐标 floor 后 −1（`(-3.2,-5.7)→(-5,-7)`）。
- `_scalar_sql_type`：bool→TEXT、str→MEMO。
- Jet4 模板链：模板缺失 → None/`_is_jet4_mdb` OSError/坏文件头/bad header/ADOX 回退/导入失败 raise（pywin32 缺失）/模板复制 + drop。
- `_drop_all_user_tables`：容错 DROP。
- `TestExportMdbMergeType`：跨行混型 `_merge_access_type` int→float 升 DOUBLE（L653，此前测不到的深层分支）。
- **技巧沉淀**：`os`/`shutil`/`pyodbc`/`win32com.client`/`shapely.geometry.shape` 均为**函数内 import**——须 patch 真实模块路径（`os.path.isfile`/`shutil.copy`/`pyodbc.connect`/`builtins.__import__` 拦 `win32com` 前缀/`shapely.geometry.shape`），patch `export_service.pyodbc` 会 AttributeError；fake `_create_empty_mdb` 必须真实写文件（`b"DUMMY-MDB"`），否则后续 `open(mdb_path,"rb")` FileNotFoundError。

### 8.2 cad 路由 4 → 100%(`tests/test_cad_routes.py` TestCADBranchCoverage +4)
- `entities?entity_type=LINE` → 只返回 LINE（`entities.py` L34 内存过滤分支）。
- `cad_metadata=None` → **421**（`_common.py` L45 缓存缺失分支；绕过上传，DB 直造记录）。
- `cad_metadata` 为 JSON 字符串 → `json.loads` 解析成功（`_common.py` L50 字符串缓存分支）。
- 预览文件不在磁盘 → **404**（`preview.py` L25；区别于 no-such-id 的 404——后者走 `_load_cad_file`）。
- **技巧沉淀**：路由有 `auth.scope_clause` 过滤——DB 直造 `StoredFile` 必须用 `X-API-Key` 对应 key 的 `api_key_id`（反查 `hash_api_key`）；`StoredFile` 无 `file_name`/`file_path` 字段（字节存 BYTEA），构造用 `original_name`/`content_type`/`cad_metadata`/`cad_preview_path`。

### 8.3 `data.py` 7 → 100%
- **L344-345**（上传流 CAD 缓存异常吞掉）：新建 `tests/test_data_cad_cache_fail.py`，patch `app.services.cad_cache.cache_cad_snapshot` 抛 `RuntimeError` → 上传仍 201（不阻断）。注意 patch 目标为真实模块路径（data.py 函数内 import）。
- **L1589-1590**（`_resolve_export_wkt` PROJCS 非法回落）：`srid=0`（falsy 跳过第一条 if）+ `detected_crs` 含 `PROJCS` + patch `pyproj.CRS` 为抛 `CRSException` 的桩 → 返回 None。
- **L1709-1711**（导出端点 500）：patch `app.api.v1.data._run_export_sync` 抛异常 → `format=geojson` 导出返回 500 + "导出失败"。

### 8.4 `feature_store.py` 最后 2 → 100%（新建 `tests/test_feature_store_guards.py`）
- L512-513：`ST_AsText()` 返回 None → feature 跳过。
- L515-516：`POLYGON EMPTY` 空几何 → feature 跳过。
- 附正常路径断言（GeoJSON 组装 + layer_name 补全）。fake db 区分 `ST_AsText` 查询与特征表查询返回不同对象。

### 8.5 验收
| 项 | 结果 |
|----|------|
| export_service / cad 路由 / data.py 目标行 | 全部清零（feature_store 最后 2 miss 同时闭环） |
| 新增测试 | 30 例（`test_export_service_branches.py` 20 + `test_cad_routes.py` TestCADBranchCoverage 4 + `test_data_cad_cache_fail.py` 1 + `test_data_download_export.py` 2 + `test_feature_store_guards.py` 3）|
| 全量覆盖 | **99.88% → 100.00%**（11094 stmts / 13 miss → 0 miss）|
| 全量回归（Redis 在线）| **1587 passed in 150.56s**（+30 例）|
| P7 技术债 | **闭环** — 覆盖回跌项关闭，全库第二次 100% 里程碑 |