# VCS 空间质检 — 项目基准线文档

> **维护规则**: 每次功能变更后，同步更新本文档对应章节。  
> **最后更新**: 2026-10-16 · **v2.24.0 CI 双架构镜像构建与发布**：新增/完善 GitHub Actions 流水线——`docker-publish.yml` 用**原生 runner**（`linux/amd64`→`ubuntu-latest`、`linux/arm64`→`ubuntu-24.04-arm`）构建 5 个镜像（vcs-api/worker/beat/flower/nginx）并 `imagetools create` 合并为 **multi-arch manifest** 推送 Docker Hub；矩阵 = 2 runner × 5 镜像 = 10 build + merge-manifest。构建期修复：`libmdbodbc1`→`odbc-mdbtools`（驱动路径动态探测）、LibreDWG `configure --disable-python`（builder 补 `python3`）、curl 下载 `--retry*`；`ci.yml` 的 `prod-config` 纳入新增脚本 `bash -n`。首轮全绿（Run 38900892791，11/11）。详见 `docs/revision_v2_24_0.md` §9。
> **最后更新**（历史）: 2026-10-13 · **v2.24.0 Docker 生产加固（Nginx 反代/自签 TLS + Docker secrets + entrypoint 派生连接串 + LibreDWG/mdbtools Linux 适配 + Flower/Beat 修复 + Makefile）**；（历史）2026-10-09 · **v2.23.0 Windows 离线一键部署子系统（deploy/）**：新增 `deploy/` 离线一键部署子系统——联网机 `build_bundle.ps1` 生成离线资源包（安装包 + Redis/LibreDWG 二进制 + 全量 wheel + 种子，逐文件 SHA-256 落 `offline/manifest.json`）→ 目标机 `deploy_offline.bat` 十二阶段编排安装（00_preflight 校验清单 / 10 Python / 20 PG / 21 PostGIS / 22 ACE [非关键] / 25 VC++ [非关键] / 30 Redis / 40 venv / 50 建库迁移 / 60 凭证+种子 / 90 起服 / 99 验证）；UAC 自提升、`--from N` 幂等续跑、`--dry-run` 干跑、失败分级 + `deploy/logs/` 日志、凭证（ADMIN_INIT_KEY/API_KEY）落 `deploy/.install_state.json`；旧 `deploy_windows.bat` 退役为薄封装转调 `deploy\deploy_offline.bat`。详见 `docs/revision_v2_23_0.md`。
> **最后更新**（历史）: 2026-10-08 · **v2.22.3 CSV 无损归档往返 + 字段-字典绑定模板往返批次**：> ① **绑定随模板往返（A 方案，用户已确认）**：「字段定义」Sheet 新增第 10 列「字典编码」，多值分隔 `,，;；`/`|`/空白，保序去重，**首个编码落 `is_primary=True`**，未知编码跳过并告警；JSON 字段对象同构 `dict_codes`；`V2FieldDef` 增 `dict_codes` + 新增 `_split_dict_codes()`；导入侧 `pending_links` 待字典建完再统一建 `StandardFieldDictLink`；导出补关联列/键；**旧 9 列模板兼容**。闭环 v2.22.2 §17 留档的「绑定零覆盖」。② **CSV 升级为 A2-1 无损归档**（用户已确认）：`export_standard` / `template/csv` / `import_standard_csv` 三端统一为 **ZIP 归档** = `_manifest.json`（仅 `format`/`version`/`encoding`/`generator`/`roles`/`diagnostics`，**不含结构数据**）+ **6 个规范化角色 CSV**（`标准信息`/`层组`/`数据表`/`字段`/`字典`/`字典项`），全部 **UTF-8 with BOM**。**层组编码/表编码/字典编码为唯一连接键，行顺序即 `ordering`** → 归档不依赖服务端 id，跨环境往返结构不变。**被否的「一表一文件」**：表名在标准内不唯一（实测两个层组下各一张「路面」），文件名单独标识无法消歧，且装不下层组层级/表编码/字段类型/字典项/绑定。**被否的 manifest 存结构数据**：会产生「双源不一致」，用户改了 CSV 没改 JSON 时无从判断以谁为准。③ **CSV 也能带字典与绑定往返**（本版核心变化）：字典**拆两个文件**（合并表里同名字典会互相冲突）；**空字典也能表达**（Excel 侧 `if d.items` 会静默丢弃）；字段表比 Excel 多一列「默认值」，避免丢 `StandardField.default_value`。④ **保留 legacy 路径**：ZIP 内无 `_manifest.json` → 走「每文件一表、字段取首行、类型恒 string」旧行为，外部作者零破坏。⑤ **降级与容错**：`tables`/`fields` 缺席 400（静默建空标准没意义），其余可选角色未声明按空处理、声明却缺文件则告警；未知层组/父级/表/字典引用、重复编码、额外未声明文件、单元格容错**均告警而非失败**；角色文件非 UTF-8 → 400（静默按空 = 删数据）。⑥ **`标准信息.csv` 真正被消费**：`_archive_standard_meta` + `_merge_standard_meta` 恢复 `DataStandard.description/category`；**表单显式值优先**，归档仅作默认值来源；**名称永不覆盖**（它是 `update/upsert` 的目标解析键），冲突只告警。⑦ **修掉一个静默 bug**：`标准信息.csv` 原导出成「首格是表头文字 + 3 个数据值」的 **4 列形态且无表头行**，导入时整列错位一列 → 已修为 `[表头, [name, desc, category]]` 并加回归守卫。⑧ **`update/upsert` 保持全删重建**（`_common.py:315-322` `_delete_standard_children`）—— 既有的「模板为权威状态」语义。⑨ **新守卫**：`tests/test_imp_exp_dict_roundtrip.py` **76 例** + `tests/test_import_conflict_messages.py` **6 例** + `tests/test_excel_codeless_dict.py` **8 例** + `tests/test_version_snapshot_fidelity.py` **9 例** + `tests/test_csv_upload_edge_cases.py` **2 例**（无名文件上传 HTTP 层走不到，直测辅助函数），TDD 全程。⑩ 全量回归 **1778 passed**，覆盖 **100.00%**（11665 stmts / 0 miss，125 文件满覆盖）；前端契约 30 passed，Playwright **31/31**，`node --check app.js` OK；另加 `scripts/verify_csv_archive.py` 归档人工校验（独立库 `vcs_verify`，自动建/删，不碰 `vcs`/`vcs_test`）。
⑫ **变异测试** `scripts/mutation_check.py`：对 `import_export.py` 注入 11 条语义变异（必需角色 400、名称不覆盖、空字典存活、重复编码 diagnostics、未知字典告警、BOM、legacy 降级、`standard` 角色错位/不消费等），逐条验证测试会红，**11/11 全部捕获**，运行结束自动还原源文件。首轮 M11「存活」追出 4 处 **warnings 断言子串假阳性**（断言文案被「标准名称冲突」告警顺手满足）——已全部修掉，并新增守卫 `TestWarningAssertionsAreNotFooledByNames` 静态拦截此类隐患。⑪ **未做/已知债**：知识图谱（仍为调研，建议 A 纯视图不引入图数据库）；约束冲突已按 PG 约束名精确归因（`_common.py` `_extract_constraint_name` / `_conflict_detail` / `_raise_conflict`，11 条唯一约束映射，接入 create/update + json/excel/csv/v2 共 6 处）——**本轮已收口**，此前「编码撞车报成名称已存在」的误导已消除；Excel 空字典债经取证**原记录不成立**（导出侧已有 else 分支，空字典往返无损），但查出真缺陷并已修：`data_standard_parser.py` 曾用 `row[0] is None` 短路，把「只有名称、没填编码」的字典连同字典项**静默丢弃且无告警** —— 新增 `_cell_str()` 统一取值（消掉 `str(None)=="None"` 坑），改为保留实体 + 按名归组 + 告警，与 `_create_v2_children` / `_create_json_children` 既有的 `dict_code or None` 契约对齐；新增 `tests/test_excel_codeless_dict.py` 8 例，变异 5/5 捕获；`update/upsert` 三格式统一返回 201（既有跨格式契约）。详见 `docs/revision_v2_22_3.md`。⑬ **版本回滚快照保真度收口**（图谱调研顺带发现的静默丢数据）：`_delete_standard_children` 会删 `StandardFieldDictLink` 与 S9 关联，但 `_build_standard_snapshot` 压根不记它们、`_restore_from_snapshot` 也不重建 —— **回滚 = 字段-字典绑定全丢 + dict_code/parent_code/S9 次归属/各 description/ordering 丢失**，绑定一丢值域质检（`VALUE_IN_SET` / A5 值域 31010005）整链失效。已修：快照补 `field_dict_links` + `layer_group_table_links`（含次归属）两张边表与全部列；恢复端改为**先建实体后建边**（边依赖 FK），显式边存在时不再走 `_primary_table_link` 占位（避免同对 (layer_group,table) 双主归属撞 `uq_slgt_layer_table`），老快照无边表时按 layer_id 兜底补主归属；顺带删掉两个幽灵字段（`field_code`/`table_type_ext`——模型无此列，写而不恢复=虚假安全感）。新增 `tests/test_version_snapshot_fidelity.py` **9 例**（边 4 + 列 2 + 幽灵 1 + 老快照兼容 2）；变异 **14/14 捕获**——首轮 M14 漏过揪出测试**读 ORM identity map 陈旧对象**的假绿缺陷（回滚用相同 id 重建实体，不清缓存则断言的是「没变过」而非「被还原」，已加 `expire_all()`）。全量 **1718 passed / 100%**（11465 stmts / 0 miss），随机序+固定序均过。⑭ **知识图谱 A 档纯视图落地**（用户已确认，ADR-0003）：不引入图数据库（层级深度硬上限 5、双写面横跨 20+ 写入口、与「PG 唯一真源」方向冲突，重评估触发条件留档 ADR）。后端 `graph.py` 两端点：`GET /{id}/graph` 摘要（nodes+聚合 binds 边+徽章计数，**无明细**——真实 oracle SHGT_SURVEY 255 表/3288 字段/2031 条目，摘要实测 ~70KB vs 明细 1.5MB）+ `GET /{id}/graph/nodes/{id}` 懒加载明细；全局字典按引用纳入、悬挂边不产生、S9 次归属画虚线。前端 `v2GraphModal`：标准卡片 🕸 按钮 → 手写 SVG 三列布局（层组|表|字典，零新依赖遵守内联规矩），点击表/字典懒加载展开，绑定边粗细=字段数。**参照物导入顺带修掉两个真实数据兼容缺陷**：一厅八室五份 JSON 全有同表重复字段名（PLAN 达 216 处 excess）+ 字典内重复条目码（LAND 8/SURVEY 3），原先整单 409 撞 `uq_sf_table_field`/`uq_di_dict_code`；现按既有「告警而非失败」原则**行级重复保首个+告警**（结构级重复：表/字典仍 409 精确归因），计数改记实际创建数（源 1348 字段去重后 1324，不再说谎）。新增 `tests/test_standard_graph_view.py` 9 例 + `tests/test_frontend_graph_ui.py` 19 例 + `tests/test_graph_reference_oracle.py` 8 例（真实数据 oracle，期望值离线预计算自源文件）；图谱端点变异 **10/10 捕获**；`scripts/import_reference_standards.py`（mode=upsert 幂等）已把五份真实标准导入开发库。全量 **1755 passed / 100%**（11591 stmts / 0 miss），随机序+固定序均过。⑮ **最后一条债收口：update/upsert 状态码契约钉死**。取证发现该债从记录时点起就已满足（三端点装饰器均 `status_code=201`，6 格矩阵实测一致）—— 残留的不是行为缺陷而是**契约未被集中钉死**（既有断言散落各场景测试）。新增 `tests/test_import_status_contract.py` **7 例**把矩阵钉在一处（json/excel/csv × update/upsert 命中=201+updated、upsert 未命中=201 新建、update 未命中=404、mode 非法=400 ×3），变异 3/3 捕获（逐格式 201→200 各被对位测试咬住）。设计取舍留档：update 回 201 而非 200 是沿用的既有跨格式契约（导入端点按「创建或替换」语义建模，`ImportResult.updated` 区分结果），改 200 属破坏性契约变更且无用户可见收益。**至此 v2.22.3 §六 债清单全部清零**（知识图谱/约束冲突/Excel 空字典/update-upsert 四条全收口）。全量 **1762 passed / 100%**（11591 stmts / 0 miss），随机序+固定序均过。⑯ **用户实测报告修复：Excel/CSV 导入重复字段名 409 + upsert 500**。上轮参照物兼容只修了 JSON 路径，Excel v2/Excel legacy/CSV archive/CSV legacy 四条路径全没有行级去重；且三个导入端点的 update/upsert 分支没包 IntegrityError —— 结构级冲突直接 **500**（create 分支才有兜底）。修法：① 四路径补齐「同表重复字段名/同字典重复条目码/legacy 表头重名列 → 保首个+告警」（`_create_v2_children`/`_create_flat_children`/`_create_csv_children_legacy`/`_create_csv_children_from_archive`）；② JSON/Excel v2/CSV 三处 update 分支补 `IntegrityError → 409 精确归因`（Excel legacy 不补：Sheet 名天然唯一、目标按名解析、去重后无约束可达，包了即死代码）；③ CSV 归档重复表行维持既有宽容语义（201+告警），字典重名行（不同 code 同名）在 upsert 下 409。新增 `tests/test_import_row_dedup_all_formats.py` **8 例**。**顺带发现新债（未修）**：前端 CSV 导入 `accept='.csv'` 只收散装 CSV（v2.22.2 语义），而 v2.22.3 导出产出 ZIP 归档 —— UI 无法回导自己导出的 CSV 归档（端点也不解压整包 ZIP）。全量 **1778 passed / 100%**（11665 stmts / 0 miss），随机序+固定序均过。⑰ **整包 ZIP 回导缺口（§17 债清零）**：导出产出 ZIP 归档，但 `import/csv` 此前只认拆开的散装 part —— 用户/前端把整个 ZIP 当单个文件传会被当「非 CSV 文件」跳过 → **201 + 0 表的静默空导入**（`test_import_status_contract` 的 CSV 用例此前也因只断言 201+updated 而空转）。修法：后端 `_expand_zip_blobs` 自动解包（`.zip` 后缀或 PK 魔数识别；坏包 400 不静默；basename 展平支持打包文件夹；目录条目与 `.` 开头系统垃圾跳过；与散装 part 重名保先者+告警；解包后移除 ZIP part 本身防污染后续告警）；前端 `V2_IE_FMT.csv.accept='.csv,.zip'` + 拖拽提示「可多选散装 CSV，或直接拖入导出的 ZIP 归档」（`v2IeDropText` 支持 cfg.hint）。新增 `TestImportAcceptsWholeZip` 后端 5 例 + 前端 2 例，变异 3/3 捕获。全量 **1778 passed / 100%**（11665 stmts / 0 miss），随机序+固定序均过。
> **最后更新**（历史）: 2026-09-28 · **v2.22.1 架构优化批次（孤儿模块清除 + pytest 基建现代化）**：> ① **UI 重构**：`app/frontend/index.html` + `app.js` 把「导入/导出/模板 3 主 Tab + 3 子 Tab + 2 个 dropzone」共 **6 个切换按钮**收敛为「**唯一格式选择器（JSON/Excel/CSV）+ ①导入 ②导出 ③模板 三段同框**」；删除 6 个旧函数（`v2IETab`/`v2ImpTab`/`v2ImpJson`/`v2ImpExcel`/`v2ImpCsv`/`v2ExportStd`），新增 `V2_IE_FMT` 三源同构配置（`label`/`accept`/`multiple`/`tpl`）；**后端零改动**。② **mode 暴露**：后端 `VALID_IMPORT_MODES`（`import_export.py:44`，v2.0 起即实现且有测试覆盖）此前**前端从不下发**，恒 `create`；现 UI 暴露 `create`/`upsert`/`update` 三档，JSON 走 Query `import/json?mode=`，Excel/CSV 走 Form `fd.append('mode', mode)`。③ **CSV 导出禁用**：`export_standard`（`import_export.py:902-987`）只有 json/excel 两分支，csv 落 else 抛 400，Query 描述「json/excel/csv」属不实描述；原「📥 CSV」按钮点了必然报错 → 现 `disabled` + `not-allowed` + tooltip + 点击兜底。④ **字典绑定链路查实缺陷（未修，留档）**：Excel 5 Sheet 确含「字典定义」（`import_export.py:829-832` / `data_standard_parser.py:337-361`）可导入字典，但**绑定零覆盖** —— 模板「字段定义」9 列无字典编码列，`_create_v2_children`（`import_export.py:498-615`）**从不创建 `StandardFieldDictLink`**，唯一入口是逐字段手工点（`POST /api/v1/fields/{field_id}/dict-links`，`field_links.py:54`），**无批量绑定端点**；而绑定是质检真实数据源（`rule_generator.py:101-121` → `VALUE_IN_SET` 自动规则；`attribute_checker.py:132-134,377-400` → A5 值域 31010005）→ 导入不绑定则值域质检整链失效；另有 `_common.py:315-322` 使 `update/upsert` 导入**删除已绑定**、导出不带关联列致往返丢失。⚠️ ② 使 `update/upsert` 由不可达变可达，④ 的丢绑定风险同步变可达（UI 已加 tooltip 说明模式语义）。⑤ **知识图谱**：全仓「知识图谱/neo4j/图谱」0 命中；GitHub 调研无现成产品（`neocarta`/`OpenMetadata`/`OpenLineage`/`neo4j-graphrag-python` 各有架构/规模不符），分档建议 A 纯视图（`GET /{id}/graph` + Cytoscape.js，**不引入图数据库**，本仓模型本已是一张图）→ B 图查询 → C 图库，倾向 A 起步。⑥ **三层守卫**：`tests/test_frontend_imp_exp_ui.py`（新建 **30 例**内容契约，含 `node --check` 语法关卡、未定义 CSS 变量守卫、孤儿 DOM id 守卫、重复 DOM id 守卫）+ `scripts/smoke_imp_exp_ui.py`（新建 **28 例** Playwright 真实 DOM 冒烟，`file://` 无需后端）。**4 次变异测试自证有效**（破坏格式枚举/引用未定义变量/错拼 DOM id/撤掉 mode 下发 → 每例被正确用例捕获）。⑦ **过程中修掉 3 处静默失效**：重构残留裸 `}` 致 `app.js` 无法解析（被新语法关卡捕获）；`var(--t3)` **不存在**（主题文本色阶是 `t0` 主→`t1` 次→`t2` 弱）→ 静默回落继承色；`.v2-ie-sec:first-of-type` 会被 fmt bar 的 `div` 抢先匹配 → 改显式 `#v2IeSecImport`。⑧ 全量回归 **1617 passed**（1587 基线 + 30 新增，**0 回归**），覆盖率 **100.00%**（11094 stmts / 0 miss）保持；`node --check app.js` OK。⑨ 附带修正 BASELINE §15 版本锚行号漂移（main.py 第 66 行 → 第 **67** 行）。
> **最后更新**（历史）: 2026-09-28 · **v2.22.1 架构优化批次（孤儿模块清除 + pytest 基建现代化）**：> ① 删除孤儿模块 `app/services/quality/scoring.py`（五维评分，生产零引用；真实路径 `quality_checker._calculate_score_legacy` + `reporter._score_color`），`tests/test_scoring.py`（16 例）一并移除。② pytest.ini 开启 `asyncio_mode=auto`，全仓删除 629 处 `@pytest.mark.asyncio`（git diff 复核 629/0）。③ "45 例漏跑"复核为 AST 脚本误报（类级装饰器），HEAD 基线 0 缺漏。④ conftest `client(test_db)` 审计为有意设计，审慎放弃加固。⑤ 二次修复（架构报告剩余发现）：`_redis_available` 双实现收敛到 `app/core/cache.py:redis_available()` 单一落点（batch/convert 原重复删）；CRS→WKT 兜底对齐（data.py 硬编码 910000→`CUSTOM_SRID_BASE` 常量，spatial.py pyproj 兜底补 `logger.warning`）；**修复 StoredFileBrief `layer_name` 漂移缺陷**（ORM/schema 有 layer_name 列，手工 18 字段构造块漏该字段 → 改 `StoredFileBrief.model_validate(sf)`）；restart.bat 回退端口 45680→45681（45680 为 v2.5 已废弃历史错误端口，BASELINE 端口表同步）；CONTEXT.md 目录计数实测更新（147 端点/28 ORM/32 服务/26 路由/77 测试/16 scripts/31 archive）；pytest.ini 覆盖率注释 41%→实测 98.40%。⑥ 全量回归 **1525 passed**。⑦ 第三批：`format_detection._infer_type` 核实非死代码，与 `rule_engine._infer_type` 语义差异双副本交叉标注（rule_engine docstring 假引用 `quality_checker._infer_field_type` 已改正——该函数不存在）；服务层事务边界评估落 ADR-0002（services 0 commit 为 Unit-of-Work 在 API 层的有意设计，风险端点扫描 0 例），全量回归 1525 passed。⑧ 第四批（覆盖回跌收口）：`logging_setup.py`/`cad_parser.py`/`cad_cache.py`/`cad_preview.py` 四个文件补测收回 **100%**（71 行），全量覆盖 **98.94% → 99.58%**（118→47 miss），新增 32 例（TestLoggingSetup +2、TestCADParserBranchCoverage +5、`tests/test_cad_cache.py` 新建 13 例、TestCADPreviewFallback +12），全量回归 **1557 passed**。⑨ 第五批（覆盖回跌收口·全库 100% 里程碑）：`export_service.py` 34 miss 清零（`tests/test_export_service_branches.py` 新建 20 例：`_build_gdb_schema` 跨行 int64 升级/bool→str/无几何 raise、`_gdb_origin` 负坐标 floor、`_scalar_sql_type` bool/text、Jet4 模板链、MDB 合并类型升级等）；cad 路由 4 miss 清零（`test_cad_routes.py` TestCADBranchCoverage +4：entity_type 过滤、cad_metadata 为空 421、JSON 字符串元数据、预览文件缺失 404）；`data.py` 7 miss 清零（`test_data_cad_cache_fail.py` 新建缓存异常吞掉分支、`test_data_download_export.py` 补 `_resolve_export_wkt` PROJCS 非法回落 + 导出端点 500）；`feature_store.py` 最后 2 miss 清零（`test_feature_store_guards.py` 新建 3 例：ST_AsText 空值跳过、empty 几何跳过、正常路径）。**全库覆盖 100.00%**（11094 stmts / 0 miss），全量回归 **1587 passed**——P7c 之后全库覆盖第二次 100% 里程碑。
> **最后更新**（历史）：2026-09-21 · **v2.21.0 报告→预览定位联动批次（方案A 评估）**：> ① 报告违规样本 → 预览页定位联动（方案A）：`app/frontend/app.js` 新增 `locateReportSample(reportId, sampleIndex)` 与 `locatePvByFeatureIndex(pvId, featureIdx)` 两条定位链路（StoredPreview 坐标/要素索引契约）；`TestReportLocateContract` 契约守卫入库（报告 HTML 段 ↔ 预览定位契约）。
> **最后更新**（历史）：2026-09-20 · **v2.20.0 架构收敛批次（C7/C2/C3/C1/C4 评估）**：> ① C7 切片大小漂移修复：`data.py` 内联 `CHUNK_SIZE_MB=2` vs `settings.file_chunk_size_mb=10`（v2.1 起技术方案权威值）→ 统一到 settings，`expected_max`/413 消息/`MAX_CHUNKS` 注释同步（10MB×500=5GB）；oversize 契约测试更新为 16MB payload。
> ② C2 secret_key 死配置：全仓零引用（认证走 API Key SHA-256 + admin_init_key 引导，无签名密钥），**非安全漏洞**；保留字段以兼容存量 .env（pydantic-settings extra_forbidden 严格模式），标注 v2.20 弃用，`.env.example` 移除该行；欠债见 §17。
> ③ C3 日志轮转：`logging_setup._ensure_file_handler()` 幂等装配 RotatingFileHandler（`log_file=./data/logs/vcs.log`、`log_max_bytes=10MB`、`log_backup_count=5` 可配置），写盘失败优雅降级仅控制台，根治 server_out.log 无限增长。
> ④ C1 评分权重收敛：`quality_checker.WEIGHTS` 定为唯一权威；`score_overall` 列（库中早已存在但从未写入/映射）补 ORM 映射并落库；`GET /quality/{id}`、`GET /quality/{id}/html`、三条 `QualityReportDetail` 构造路径统一经 `quality._overall_from_scores(WEIGHTS)`；前端 app.js 删除 3 处硬编码权重改用后端 `score_overall`；权重归一化 + 前端无硬编码守卫 + GET 0.815 契约测试。
> ⑤ C4 评估：data.py 4 个内联助手（`_guess_mime`/`_is_vector_file`/`_extract_layer_name`/`_ingest_srid`）均**无跨模块调用方**，抽取到 file_utils 属无价值重构，放弃（ADR-0001 路由拆分仍需 test-first 专项）；删除生产死代码 `_extract_layer_name`（连同其 3 个测试与留档引用）。
> 全量 **1537 passed**（v2.19.6 的 1536 基准 +4 新契约测试 −3 死代码测试）。
> **当前版本**: v2.24.0 · Docker 生产加固（Nginx 反代/自签 TLS + Docker secrets + LibreDWG/mdbtools Linux 适配 + Makefile）+ CI 双架构镜像构建发布（详见 `docs/revision_v2_24_0.md`）+ v2.23.0 Windows 离线一键部署子系统（deploy/，详见 `docs/revision_v2_23_0.md`）+ v2.22.3 CSV 导出补齐 + 字段-字典绑定模板往返（详见 `docs/revision_v2_22_3.md`）+ v2.22.2 数据标准导入/导出/模板单框三段重构 + mode 暴露 + 字典绑定链路缺陷定位（详见 `docs/revision_v2_22_2.md`）+ v2.22.1 架构优化（孤儿模块清除 + pytest 基建现代化，详见 `docs/revision_v2_22_1.md`）+ v2.22.0 Lane② data.py 收口批次（p5k2 纯函数迁移 + except-swallow 治理，详见 `docs/revision_v2_22_0.md`）+ v2.21.0 报告→预览定位联动（方案A）· 数据导出 v2.19.0 + 混合几何分组 v2.19.1 + 依赖补全 v2.19.2 + 真实数据导出根因修复 v2.19.3 + MDB Jet4 格式修复 v2.19.4 + File Geodatabase 导出 v2.19.5 + 压缩包自动清理修复 v2.19.6 + 架构收敛批次（C7 切片漂移/C2 死配置弃用/C3 日志轮转/C1 权重收敛）v2.20.0

---

## 1. 项目概览

**矢量数据质检微服务 (Vector Conversion Service)** — 集格式转换、坐标系变换、质量检查、空间数据存储于一体。

### 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 框架 | FastAPI + SQLAlchemy 2.0 async | FastAPI ≥0.115, SA ≥2.0.30 |
| 数据库 | PostgreSQL/PostGIS | PG 15 + PostGIS 3.4 |
| 任务队列 | Celery + Redis | Celery ≥5.4, Redis 7 |
| GIS | Fiona/Shapely/pyproj/geoalchemy2/ogr2ogr | Fiona ≥1.9, Shapely ≥2.0 |
| CAD | ezdxf + Pillow | ezdxf ≥1.4.0, Pillow ≥10.0 |
| MDB 导出 | pyodbc + pywin32（ADOX 建库，仅 win32） | pyodbc ≥5.1.0, pywin32 ≥305 |
| 前端 | 单文件 HTML SPA (Vanilla JS + Leaflet) | - |
| 迁移 | Alembic | ≥1.13 |
| 监控 | Prometheus + Flower | - |

### 访问地址

| 服务 | 地址 |
|------|------|
| 前端 SPA | http://localhost:45679/app/ |
| API 测试台 | http://localhost:45679/app/test.html |
| Swagger | http://localhost:45679/docs |
| ReDoc | http://localhost:45679/redoc |
| Flower | http://localhost:5555 |
| 健康检查 | http://localhost:45679/health |

---

## 2. 目录结构

```
空间质检/
├── app/
│   ├── api/v1/              # 12 个路由模块 (~147 端点, 装饰器静态计数 + 1 WebSocket)
│   │   ├── router.py        # 路由聚合入口
│   │   ├── convert.py       # 格式转换 + 任务管理 (7 endpoints) — v2.14 批量硬删除
│   │   ├── admin.py         # API Key/分组管理 (12 endpoints)
│   │   ├── batch.py         # 批量转换 (5 endpoints)
│   │   ├── quality.py       # 质检报告 (7 endpoints)
│   │   ├── quality_rules.py # 质检规则 CRUD (5 endpoints)
│   │   ├── quality_plans.py # 质检方案 CRUD (6 endpoints) — v2.10
│   │   ├── data.py          # 数据存储 v2.4 (18 endpoints)
│   │   ├── data_standards.py # 数据标准 v1 (5 endpoints) — P5g 100%
│   │   ├── data_standard_v2/  # 数据标准 v2 包 (54 endpoints, 10 子模块)
│   │   │   ├── __init__.py    # 路由聚合 (54 routes, 按注册序导入)
│   │   │   ├── _common.py     # 共享工具函数/常量/重载
│   │   │   ├── standards.py   # 标准 CRUD (6 routes)
│   │   │   ├── layers.py      # 层组 CRUD + move/copy (7 routes)
│   │   │   ├── tables.py      # 数据表 CRUD + attach/detach (8 routes)
│   │   │   ├── fields.py      # 字段 CRUD + reorder (5 routes)
│   │   │   ├── dictionaries.py # 字典 CRUD + 条目 + share (10 routes)
│   │   │   ├── field_links.py # 字段-字典关联 CRUD (4 routes)
│   │   │   ├── versions.py    # 版本对比/回滚/publish (5 routes)
│   │   │   └── import_export.py # 导入/导出/模板 (8 routes) — P5h 100%
│   │   ├── cad/              # CAD 检查 (4 endpoints: info/layers/entities/preview) — v2.18
│   │   │   ├── __init__.py   # 路由聚合 (4 routes, 按注册序导入)
│   │   │   ├── _common.py    # 共享工具: CAD_FORMATS/_load_cad_file/_cad_metadata/_preview_dir
│   │   │   ├── info.py       # 元数据 GET /cad/{id}/info
│   │   │   ├── layers.py     # 图层清单 GET /cad/{id}/layers
│   │   │   ├── entities.py   # 实体分页 GET /cad/{id}/entities
│   │   │   └── preview.py    # 预览图 GET /cad/{id}/preview (svg/png)
│   │   ├── spatial.py       # 空间操作 (5 endpoints) — v2.11 transform_results
│   │   ├── source_data.py   # 源数据管理 (13 endpoints) — P5f 100%
│   │   └── ws.py            # WebSocket 实时推送 (1 endpoint)
│   ├── services/            # 31 个服务模块 (顶层 26 + quality 子包 5)
│   │   ├── quality/         # 质检子包 (5 files)
│   │   │   ├── rule_engine.py    # 规则执行引擎
│   │   │   ├── rule_generator.py # 规则自动生成 — P5i 服务级 100%
│   │   │   ├── attribute_checker.py # 属性检查器 — P5i 服务级 100%
│   │   │   ├── reporter.py       # 报告生成
│   │   │   └── models.py         # 质检数据模型
│   │   ├── format_detection.py   # 格式自动检测
│   │   ├── geometry_repair.py    # 几何修复
│   │   ├── crs_transform.py      # 坐标系变换
│   │   ├── custom_crs.py         # 自定义坐标系 SRID 注册 (v2.10.5)
│   │   ├── spatial_loader.py     # 空间数据入库
│   │   ├── spatial_query.py      # 空间查询
│   │   ├── feature_store.py      # 要素存储管理
│   │   ├── topology_checker.py   # 拓扑检查
│   │   ├── structure_analyzer.py # 结构分析
│   │   ├── attribute_processing.py # 属性处理
│   │   ├── gdb_processor.py      # GDB 文件处理
│   │   ├── archive_extractor.py  # 压缩包解压
│   │   ├── quality_checker.py    # 质检入口
│   │   ├── quality_exporter.py   # 质检报告导出
│   │   ├── script_generation.py  # ogr2ogr 脚本生成
│   │   ├── data_standard_parser.py # 数据标准解析
│   │   ├── progress_tracker.py   # 进度追踪
│   │   ├── dwg_converter.py      # DWG→DXF 转换 (LibreDWG dwg2dxf 集成) — P5j 服务级 100%
│   │   ├── esri_shapebin.py      # PGeo SHAPE BLOB 解码器 (v2.12.1, 类型/flag 校验 + 环方向)
│   │   ├── _odbc.py              # 平台感知 MDB/ODBC 连接助手 (v2.24.0: Windows Access / POSIX MDBTools)
│   │   ├── mdb_processor.py      # MDB 处理 (ESRI Personal Geodatabase, pyodbc; v2.12.1 CRS 提取)
│   │   ├── cad_parser.py         # CAD 解析 (ezdxf, v2.18: 图层树/实体/文本/元数据)
│   │   ├── cad_cache.py          # CAD 快照缓存 (v2.18: 上传时解析落库 + 预览文件)
│   │   ├── cad_preview.py        # CAD 预览渲染 (v2.18: SVG/PNG 自绘, 不依赖 matplotlib)
│   │   └── transform_engine.py   # 统一坐标变换引擎 (v2.13, ResolvedCRS + 域校验 + 失败统计)
│   ├── models/              # 34 个模型类 (映射 32 张表)
│   ├── schemas/             # Pydantic 请求/响应模型
│   ├── core/                # 15 个核心模块
│   │   ├── auth_context.py  # 认证上下文
│   │   ├── security.py      # 安全工具
│   │   ├── cache.py         # 缓存管理
│   │   ├── constants.py     # 常量定义
│   │   ├── db_utils.py      # 数据库工具
│   │   ├── deps.py          # FastAPI 依赖注入
│   │   ├── file_utils.py    # 文件工具
│   │   ├── logging_setup.py # 结构化日志
│   │   ├── rate_limit.py    # 速率限制
│   │   ├── security_middleware.py # 安全响应头
│   │   ├── temp_cleanup.py  # 临时文件清理
│   │   └── trace_middleware.py # 请求追踪
│   ├── workers/             # Celery 任务
│   │   ├── celery_app.py    # Celery 应用配置
│   │   ├── tasks.py         # 核心任务定义
│   │   ├── batch_tasks.py   # 批量任务
│   │   └── beat_schedule.py # 定时任务调度
│   ├── db/
│   │   └── session.py       # 异步数据库会话
│   ├── frontend/            # SPA 前端
│   │   ├── index.html       # 主页面（v2.16 起主脚本外置）
│   │   ├── app.js           # 前端主逻辑（v2.16 抽出，原内联 script 5846→1594 行）
│   │   ├── vendor/leaflet/  # Leaflet 1.9.4 自托管（v2.17 起，css+js+images）
│   │   └── test.html        # API 测试台
│   ├── config.py            # 应用配置 (pydantic-settings)
│   └── main.py              # FastAPI 入口
├── alembic/                 # 数据库迁移
├── scripts/                 # 初始化/调试/迁移/冒烟脚本（含 smoke_convert.py、smoke_imp_exp_ui.py）
├── tools/libredwg/          # LibreDWG v0.14 win64 二进制 (dwg2dxf.exe + 运行时 DLL)
├── tests/                   # pytest 测试套件（含 e2e/ 浏览器级回归）
├── deploy/                  # Windows 离线一键部署子系统 (v2.23.0)
│   ├── offline/             # 离线资源包（联网机由 build_bundle.ps1 生成；manifest.json = SHA-256 校验清单）
│   │   ├── installers/      # 安装包: Python 3.13.3 / PostgreSQL 15.15 / PostGIS 3.6.1 / VC++2019 / ACE2016
│   │   ├── redis/           # Redis 8.10 win64 二进制（redis-server.exe + redis.conf）
│   │   ├── libredwg/        # LibreDWG win64 二进制（dwg2dxf.exe 等）
│   │   ├── wheels/          # Python 离线 wheel（cp313 win_amd64, --only-binary=:all:）
│   │   └── seed/            # 数据标准种子 JSON（一厅八室参照物 SHGT_*.json）
│   ├── scripts/             # 部署阶段脚本 00..99（12 阶段）+ common.ps1 + deploy.ps1 编排器
│   ├── tests/               # deploy 脚本自测（run_tests.ps1, 17 例）
│   ├── build_bundle.ps1     # 联网机离线资源包生成器（写 manifest.json）
│   ├── deploy_offline.bat   # 离线部署入口（UAC 自提升, --from/--dry-run）
│   └── README.md            # 部署/交付/排障说明（中文）
├── docs/                    # 设计文档（revision_history + adr/ADR-0001 + review_v2.16.0 + archive/）
├── data/                    # 运行时数据 (uploads/outputs/quality_reports/previews)
├── docker/                  # 容器构建资产 (v2.24.0)
│   ├── entrypoint.sh        # 通用入口：派生 DATABASE_URL + 装载 secrets + 建数据目录
│   ├── nginx/               # Nginx 反代 (Dockerfile + nginx.conf + conf.d/vcs.conf)
│   └── scripts/             # gen_secrets.sh / gen_certs.sh
├── .github/                 # GitHub Actions CI (v2.24.0)
│   └── workflows/
│       ├── ci.yml           # test / smoke / prod-config（YAML 解析 + bash -n 静态校验）
│       └── docker-publish.yml # 双架构(amd64+arm64)镜像构建 + manifest 合并发布
├── docker-compose.yml       # 容器编排（基线）
├── docker-compose.prod.yml  # 生产覆盖层（Nginx/secrets/限额/日志；v2.24.0）
├── Dockerfile               # 多阶段构建（含 LibreDWG 源码构建阶段）
├── Makefile                 # 部署/证书/密钥 targets (v2.24.0)
├── .env.prod.example        # 生产 env 模板 (v2.24.0)
├── requirements.txt         # Python 依赖
├── alembic.ini              # Alembic 配置
├── restart.bat              # Windows 一键重启
├── restart.sh               # Linux 一键重启
├── deploy_linux.sh          # Linux 部署脚本
└── deploy_windows.bat       # Windows 部署脚本（v2.23.0 起薄封装，转调 deploy\deploy_offline.bat）
```

---

## 3. 数据库表结构 (32 应用物理表)

### 3.1 认证与权限

#### `key_groups` — API Key 分组
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| name | String(128) | UNIQUE, INDEX | 分组名称 |
| description | Text | NULLABLE | 描述 |
| created_at | DateTime(tz) | INDEX | 创建时间 |
| updated_at | DateTime(tz) | | 更新时间 |

#### `api_keys` — API 密钥
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| key_hash | String(128) | UNIQUE | SHA-256 哈希 |
| name | String(128) | | 密钥名称 |
| description | Text | NULLABLE | 描述 |
| is_active | Boolean | DEFAULT True | 是否启用 |
| created_at | DateTime(tz) | INDEX | 创建时间 |
| last_used_at | DateTime(tz) | NULLABLE | 最后使用时间 |
| group_id | String(36) | FK→key_groups, INDEX | 所属分组 |

### 3.2 数据标准体系 (v2)

#### `data_standards` — 数据标准
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| code | String(64) | INDEX | 标准编码 |
| name | String(256) | UNIQUE(api_key_id, name) | 标准名称 |
| description | Text | NULLABLE | 描述 |
| category | String(64) | DEFAULT 'custom' | 分类 |
| status | String(32) | DEFAULT 'draft' | 状态 |
| version | Integer | DEFAULT 1 | 版本号 |
| file_name | String(512) | NULLABLE | 原始文件名 |
| created_at / updated_at | DateTime(tz) | | 时间戳 |

#### `standard_layer_groups` — 图层分组 (递归树)
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| standard_id | String(36) | FK→data_standards, CASCADE, INDEX | 所属标准 |
| parent_id | String(36) | FK→self, CASCADE, INDEX | 父节点 |
| group_name | String(256) | UNIQUE(standard_id, parent_id, group_name) | 分组名 |
| group_code | String(64) | UNIQUE(standard_id, parent_id, group_code) | 分组编码 |
| description | Text | NULLABLE | 描述 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `standard_tables` — 数据表定义
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| layer_id | String(36) | FK→standard_layer_groups, CASCADE, INDEX | 所属图层 |
| standard_id | String(36) | FK→data_standards, CASCADE, INDEX | 所属标准 |
| table_name | String(256) | UNIQUE(layer_id, table_name) | 表名 |
| table_code | String(64) | UNIQUE(layer_id, table_code) | 表编码 |
| table_type | String(32) | DEFAULT 'spatial' | 表类型 |
| geometry_type | String(32) | NULLABLE | 几何类型 |
| description | Text | NULLABLE | 描述 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `standard_layer_group_tables` — 图层-表多对多关联
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| layer_group_id | String(36) | FK, CASCADE, INDEX | 图层分组 |
| table_id | String(36) | FK, CASCADE, INDEX | 数据表 |
| is_primary | Boolean | DEFAULT False | 是否主表 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `standard_fields` — 字段定义
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| table_id | String(36) | FK→standard_tables, CASCADE, INDEX | 所属表 |
| standard_id | String(36) | FK→data_standards, CASCADE, INDEX | 所属标准 |
| field_name | String(256) | UNIQUE(table_id, field_name) | 字段名 |
| field_alias | String(256) | NULLABLE | 别名 |
| data_type | String(32) | DEFAULT 'string' | 数据类型 |
| length | Integer | NULLABLE | 长度 |
| precision | Integer | NULLABLE | 精度 |
| nullable | Boolean | DEFAULT True | 是否可空 |
| is_primary_key | Boolean | DEFAULT False | 是否主键 |
| is_required | Boolean | DEFAULT False | 是否必填 |
| default_value | String(256) | NULLABLE | 默认值 |
| description | Text | NULLABLE | 描述 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `standard_dictionaries` — 数据字典
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| standard_id | String(36) | FK→data_standards, INDEX | 所属标准 |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| dict_name | String(256) | UNIQUE(standard_id, dict_name) | 字典名 |
| dict_code | String(64) | | 字典编码 |
| scope | String(32) | DEFAULT 'standard' | 范围(global/standard) |
| is_system | Boolean | DEFAULT False | 是否系统字典 |

#### `dictionary_items` — 字典条目
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| dict_id | String(36) | FK→standard_dictionaries, CASCADE, INDEX | 所属字典 |
| item_code | String(64) | UNIQUE(dict_id, item_code) | 条目编码 |
| item_name | String(256) | | 条目名称 |
| parent_code | String(64) | NULLABLE | 父级编码(层级) |
| description | Text | NULLABLE | 描述 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `standard_field_dict_links` — 字段-字典关联
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| field_id | String(36) | FK→standard_fields, CASCADE, INDEX | 字段 |
| dict_id | String(36) | FK→standard_dictionaries, CASCADE, INDEX | 字典 |
| filter_condition | String(256) | NULLABLE | 过滤条件 |
| is_primary | Boolean | DEFAULT False | 是否主字典 |
| ordering | Integer | DEFAULT 0 | 排序 |

### 3.3 源数据管理 (旧版, 迁移中)

#### `source_tables` — 源数据表
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| standard_id | String(36) | FK→data_standards | 关联标准 |
| table_name | String(256) | | 表名 |
| table_code | String(64) | | 表编码 |
| geometry_type | String(32) | | 几何类型 |
| source | String(64) | | 数据来源 |
| version | String(32) | | 版本 |
| is_active | Boolean | DEFAULT True | 是否启用 |

#### `source_fields` — 源数据字段
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| table_id | String(36) | FK→source_tables, CASCADE, INDEX | 所属表 |
| field_name | String(256) | | 字段名 |
| field_alias | String(256) | | 别名 |
| data_type | String(32) | DEFAULT 'string' | 数据类型 |
| length | Integer | | 长度 |
| nullable | Boolean | DEFAULT True | 是否可空 |
| is_primary_key | Boolean | DEFAULT False | 是否主键 |
| is_required | Boolean | DEFAULT False | 是否必填 |
| allowed_values | JSON | | 允许值 |

#### `source_dictionaries` — 源数据字典
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| dict_name | String(256) | | 字典名 |
| dict_code | String(64) | | 字典编码 |
| is_system | Boolean | DEFAULT False | 是否系统字典 |

#### `field_dict_links` — 字段-字典关联(旧)
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| field_id | String(36) | FK→source_fields, CASCADE, INDEX | 字段 |
| dict_id | String(36) | FK→source_dictionaries, CASCADE | 字典 |
| filter_condition | String(256) | | 过滤条件 |

#### `standard_versions` — 标准版本历史
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| standard_id | String(36) | FK→data_standards | 所属标准 |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| version_number | Integer | DEFAULT 1 | 版本号 |
| version_label | String(32) | | 版本标签 |
| snapshot_data | JSON | | 快照数据 |
| diff_data | JSON | | 差异数据 |
| change_log | Text | | 变更日志 |
| is_rollback | Boolean | DEFAULT False | 是否回滚 |
| file_data | LargeBinary | | 文件数据 |
| file_name | String(512) | | 文件名 |

#### `data_standard_sheets` — 标准工作表(旧)
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| standard_id | String(36) | FK→data_standards, CASCADE, INDEX | 所属标准 |
| sheet_name | String(256) | | 工作表名 |
| ordering | Integer | DEFAULT 0 | 排序 |

#### `data_standard_fields` — 标准字段(旧)
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| sheet_id | String(36) | FK→data_standard_sheets, CASCADE, INDEX | 所属工作表 |
| field_name | String(256) | | 字段名 |
| field_type | String(32) | DEFAULT 'string' | 字段类型 |
| length | Integer | | 长度 |
| nullable | Boolean | DEFAULT True | 是否可空 |
| allowed_values | JSON | | 允许值 |

### 3.4 文件与空间存储

#### `stored_files` — 文件存储
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| original_name | String(512) | | 原始文件名 |
| content_type | String(256) | DEFAULT 'application/octet-stream' | MIME 类型 |
| file_size | BigInteger | DEFAULT 0 | 文件大小(bytes) |
| file_data | LargeBinary | | 文件内容(BYTEA) |
| file_hash | String(128) | | SHA-256 哈希(去重) |
| is_archive | Boolean | DEFAULT False | 是否压缩包 |
| source_archive | String(512) | | 来源压缩包 |
| chunk_index / total_chunks | BigInteger | | 分片信息 |
| upload_id | String(64) | INDEX | 上传会话ID |
| standard_id | String(36) | FK→data_standards, INDEX | 关联标准 |
| standard_table_id | String(36) | FK→standard_tables | 关联表 |
| data_type | String(20) | INDEX, DEFAULT 'unknown' | 数据类型（geometry/spatial/table/archive/unknown；MDB 上传后为 'spatial'） |
| geometry_type | String(32) | INDEX | 几何类型 |
| file_format | String(32) | INDEX, DEFAULT 'unknown' | 文件格式（geojson/shp/gpkg/kml/dxf/gml/gdb/mdb/obj/csv/xlsx/tab/mif/zip/tar；MDB 入库后转 GeoJSON 存储） |
| detected_crs | String(2048) | | 检测到的CRS |
| detected_crs_srid | Integer | NULLABLE (v2.10.5) | 入库解析出的实际 SRID（自定义坐标系为注册的 9xxxxx） |
| crs_is_custom | Boolean | DEFAULT false (v2.10.5) | CRS 是否为注册的自定义 SRID |
| point_count / line_count / polygon_count | BigInteger | DEFAULT 0 (v2.10.5) | 三维要素计数冗余列（聚合自分表，列表页零 JOIN） |

> 迁移脚本: `alembic/versions/0009_stored_file_crs_srid.py`（v2.10.5）
| layer_count | Integer | DEFAULT 0 | 图层数 |
| feature_count | BigInteger | DEFAULT 0 | 要素数 |
| field_schema | JSONB | | 字段结构 |
| cad_metadata | JSONB | NULLABLE (v2.18) | CAD 解析快照（上传时缓存: format/version/units/layers/entities 摘要） |
| cad_preview_path | String(1024) | NULLABLE (v2.18) | CAD 预览文件相对路径（data/previews/{file_id}.svg/.png） |
| created_at / updated_at | DateTime(tz) | | 时间戳 |

> 迁移脚本: `alembic/versions/0013_cad_enhancement.py`（v2.18, stored_files + stored_file_layers 新增 CAD 列）

#### `stored_file_layers` — 文件-图层聚合 (v2.10.5)
> 真实图层清单事实源：上传/合并/reindex 后由 `feature_store.aggregate_layer_stats()` 从三分区表 GROUP BY 聚合写入（DELETE+INSERT 幂等）

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| stored_file_id | String(36) | FK→stored_files, UNIQUE(stored_file_id,layer_name) | 所属文件 |
| layer_name | String(256) | NOT NULL | 图层名（CAD 取要素属性 Layer，GDB 取 fiona 层名） |
| geometry_type | String(16) | | 图层主导类型（Point/LineString/Polygon/Mixed） |
| point_count / line_count / polygon_count | Integer | DEFAULT 0 | 三维计数 |
| feature_count | Integer | DEFAULT 0 | 要素总数 |
| crs_note | String(256) | NULLABLE | 图层级 CRS 备注 |
| color_index | Integer | NULLABLE (v2.18) | CAD 图层 ACI 颜色索引 |
| color_rgb | String(16) | NULLABLE (v2.18) | 颜色 RGB 十六进制（#RRGGBB 或逗号分隔） |
| linetype | String(64) | NULLABLE (v2.18) | CAD 线型名（如 CONTINUOUS/DASHED） |
| is_visible | Boolean | DEFAULT True (v2.18) | CAD 图层可见性 |
| is_frozen | Boolean | DEFAULT False (v2.18) | CAD 图层冻结状态 |
| api_key_id | String(36) | FK→api_keys, NULLABLE, INDEX | 冗余（回填兼容；超管为 NULL） |
| created_at / updated_at | DateTime(tz) | | 时间戳 |

> 迁移脚本: `alembic/versions/0010_layer_stats_and_qc_scope.py`（v2.10.5）；存量回填 `scripts/backfill_layer_stats.py`

#### `point_features` / `line_features` / `polygon_features` — 空间要素(分区表)
> 三张表结构相同, 按 `stored_file_id` LIST 分区

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK (复合) | 要素ID |
| stored_file_id | String(36) | PK (复合), 分区键 | 所属文件 |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| layer_name | String(256) | | 图层名 |
| source_name | String(512) | INDEX | 来源名 |
| feature_index | Integer | DEFAULT 0 | 要素序号 |
| properties | JSONB | GIN index | 属性(JSON) |
| geometry_type | String(16) | INDEX | 几何类型 |
| geometry | Geometry(GEOMETRY) | GiST index | PostGIS 几何 |
| created_at / updated_at | DateTime(tz) | | 时间戳 |

**约束**: `CHECK: geometry IS NULL OR ST_IsValid(geometry)`

### 3.5 转换任务

#### `conversion_jobs` — 转换任务
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| celery_task_id | String(128) | INDEX | Celery 任务ID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| batch_job_id | String(36) | FK→batch_jobs, INDEX | 批量任务 |
| original_filename | String(512) | | 原始文件名 |
| input_path / output_path | String(1024) | | 输入/输出路径 |
| src_crs / dst_crs | String(256) | | 源/目标坐标系 |
| output_format | String(64) | DEFAULT 'GeoJSON' | 输出格式 |
| select_fields | JSON | | 字段筛选 |
| where_clause | Text | | SQL 条件 |
| repair_strategy | String(32) | DEFAULT 'auto_fix' | 修复策略 |
| script_type | String(32) | DEFAULT 'ogr2ogr' | 脚本类型 |
| target_encoding | String(32) | DEFAULT 'UTF-8' | 目标编码 |
| detected_format / detected_encoding / detected_crs | | | 检测结果 |
| detection_confidence | Float | | 检测置信度 |
| generated_script | Text | | 生成的脚本 |
| feature_count_input / feature_count_output | Integer | | 要素计数 |
| feature_fixed / feature_dropped | Integer | | 修复/丢弃计数 |
| status | String(32) | INDEX, DEFAULT 'pending' | 状态 |
| error_message | Text | | 错误信息 |
| warnings | JSON | | 警告列表 |
| progress | Float | DEFAULT 0.0 | 进度(0-1) |
| current_step | String(64) | | 当前步骤 |
| quality_report_id | String(36) | | 关联质检报告 |
| created_at / started_at / finished_at / updated_at | DateTime(tz) | | 时间戳 |

**状态枚举**: pending → running → success/failed/cancelled

#### `batch_jobs` — 批量任务
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| name | String(256) | | 任务名称 |
| status | String(32) | INDEX, DEFAULT 'pending' | 状态 |
| total_count / success_count / failed_count / cancelled_count | Integer | | 计数 |
| output_format | String(64) | DEFAULT 'GeoJSON' | 输出格式 |
| dst_crs | String(256) | DEFAULT 'EPSG:4490' | 目标CRS |
| error_message | Text | | 错误信息 |
| created_at / started_at / finished_at / updated_at | DateTime(tz) | | 时间戳 |

### 3.6 质检体系

#### `quality_rules` — 质检规则
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| standard_field_id | String(36) | FK→standard_fields, CASCADE | 关联字段 |
| rule_type | String(32) | | 规则类型 |
| rule_name | String(256) | | 规则名称 |
| field_pattern | String(256) | DEFAULT '' | 字段匹配模式 |
| severity | String(16) | DEFAULT 'warning' | 严重程度 |
| params | JSON | | 规则参数 |
| enabled | Boolean | DEFAULT True | 是否启用 |
| is_auto | Boolean | DEFAULT True | 是否自动生成 |

#### `quality_reports` — 质检报告
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| job_id | String(36) | FK→conversion_jobs, INDEX | 关联转换任务 |
| batch_job_id | String(36) | FK→batch_jobs, INDEX | 关联批量任务 |
| api_key_id | String(36) | FK→api_keys, INDEX | 所属密钥 |
| file_name | String(512) | | 文件名 |
| detected_format | String(64) | | 检测格式 |
| total_features / total_fields | Integer | | 要素/字段数 |
| **五维评分** | | | |
| score_completeness | Float | DEFAULT 0.0 | 完整性 |
| score_accuracy | Float | DEFAULT 0.0 | 准确性 |
| score_consistency | Float | DEFAULT 0.0 | 一致性 |
| score_uniqueness | Float | DEFAULT 0.0 | 唯一性 |
| score_topology | Float | DEFAULT 0.0 | 拓扑(及时性) |
| completeness_score | Float | DEFAULT 1.0 | 完整性得分(旧) |
| missing_fields / null_value_counts | JSON | | 缺失统计 |
| critical_fields_status | JSON | | 关键字段状态 |
| issues / constraint_violations / topology_violations | JSON | | 问题列表 |
| data_standard_id | String(36) | FK→data_standards | 关联标准 |
| standard_table_id | String(36) | FK→standard_tables | 关联表 |
| stored_file_id | String(36) | FK→stored_files | 关联文件 |
| checked_layers | JSONB | NULLABLE (v2.10.5) | 质检圈定的图层名列表；NULL=全部图层 |
| attribute_summary / topology_summary / topology_config | JSON | | 摘要 |
| html_report_path / json_report_path | String(1024) | | 报告路径 |
| created_at / updated_at | DateTime(tz) | | 时间戳 |

#### `quality_rule_results` — 规则执行结果
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| report_id | String(36) | FK→quality_reports, CASCADE, INDEX | 所属报告 |
| rule_id | String(36) | FK→quality_rules | 关联规则 |
| rule_type | String(32) | | 规则类型 |
| field_name | String(256) | | 检查字段 |
| passed | Boolean | | 是否通过 |
| severity | String(16) | | 严重程度 |
| total_checked / failed_count | Integer | | 检查/失败计数 |
| detail | JSON | | 详细信息 |

#### `quality_violations` — 违规明细
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| report_id | String(36) | FK→quality_reports, CASCADE, INDEX | 所属报告 |
| rule_result_id | String(36) | FK→quality_rule_results, CASCADE | 关联结果 |
| field_name | String(256) | INDEX | 违规字段 |
| issue_type | String(32) | | 问题类型 |
| severity | String(16) | | 严重程度 |
| feature_index | Integer | | 要素索引 |
| expected / actual | Text | | 期望/实际值 |
| message | Text | | 违规描述 |

#### `quality_daily_snapshots` — 质检日报
| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| api_key_id | String(36) | FK→api_keys, CASCADE, INDEX | 所属密钥 |
| snapshot_date | Date | UNIQUE(api_key_id, snapshot_date) | 快照日期 |
| total_files / checked_files | Integer | | 文件计数 |
| completeness / uniqueness / consistency | Numeric(5,4) | | 维度得分 |
| violations_total | Integer | DEFAULT 0 | 违规总数 |
| score_overall | Numeric(5,4) | | 综合得分 |

### 3.7 实体关系图

```
KeyGroup ──1:N── ApiKey ──1:N── BatchJob ──1:N── ConversionJob
                    │
                    ├──1:N── StoredFile ──1:N── PointFeature
                    │                          LineFeature
                    │                          PolygonFeature
                    │
                    ├──1:N── DataStandard
                    │         ├──1:N── StandardLayerGroup (递归树)
                    │         │         ├──N:M── StandardTable (via StandardLayerGroupTableLink)
                    │         │         └──N:M── StandardTable (多分类)
                    │         ├──1:N── StandardField ──1:N── QualityRule
                    │         │         └──N:M── StandardDictionary (via StandardFieldDictLink)
                    │         ├──1:N── StandardDictionary ──1:N── DictionaryItem
                    │         ├──1:N── StandardVersion
                    │         └──1:N── DataStandardSheet ──1:N── DataStandardField
                    │
                    ├──1:N── QualityReport ──1:N── QualityRuleResult
                    │                      └──1:N── QualityViolation
                    │
                    ├──1:N── QualityDailySnapshot
                    │
                    ├──1:N── SourceTable ──1:N── SourceField
                    │              └──N:M── SourceDictionary (via FieldDictLink)
                    │
                    └──1:N── SourceDictionary
```

---

#### `quality_plans` — 质检方案 (v2.10)

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | String(36) | PK | UUID |
| name | String(128) | UNIQUE, INDEX | 方案名称 |
| description | Text | NULLABLE | 方案描述 |
| attr_enabled | Boolean | NOT NULL, DEFAULT true | 属性检查开关 |
| topo_config | JSON | NULLABLE | 拓扑规则开关+阈值 |
| standard_id | String(36) | NULLABLE | 关联标准 ID |
| extra | JSON | NULLABLE | 扩展字段（预留） |
| created_at | DateTime(tz) | NOT NULL | 创建时间 |
| updated_at | DateTime(tz) | NOT NULL | 更新时间 |

迁移脚本: `alembic/versions/0008_quality_plans.py`

#### `transform_results` — 坐标转换结果 (v2.11)

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|-----------|------|
| id | VARCHAR(36) | PK | UUID 主键 |
| api_key_id | VARCHAR(36) | NULLABLE, FK→api_keys | 归属 Key（管理员虚拟 ID 时为 NULL） |
| source_file_id | VARCHAR(36) | NOT NULL, FK→stored_files | 源文件 |
| source_file_name | VARCHAR(512) | NOT NULL | 源文件名快照 |
| source_crs | VARCHAR(64) | NOT NULL | 源坐标系 |
| target_crs | VARCHAR(64) | NOT NULL | 目标坐标系 |
| source_srid | Integer | NULLABLE | 源 SRID |
| target_srid | Integer | NULLABLE | 目标 SRID |
| feature_count | Integer | NOT NULL, DEFAULT 0 | 转换要素数 |
| output_size | BigInteger | NOT NULL, DEFAULT 0 | 输出 GeoJSON 大小(字节) |
| output_data | BYTEA | NOT NULL | 转换后 GeoJSON 全量存储 |
| created_at | TIMESTAMP | NOW() | 创建时间 |

> 迁移脚本: `alembic/versions/0011_transform_results.py`（v2.11）

#### `spatial_ref_sys` 自定义注册 (PostGIS 内置表, v2.10.5 起使用)
自定义投影坐标系（无 EPSG 编码）由 `services/custom_crs.py` 确定性分配 SRID
`910000~998999`（SHA-256 哈希，跨进程稳定），幂等注册到 PostGIS，
`auth_name='VCS-CUSTOM'`；含假东/假北互换自动纠正（见 5.2）。
存量修复工具：`scripts/repair_feature_srid.py`。
> 注：不计入上方 30 张应用表统计；另有 `alembic_version` 迁移版本表。

---

## 4. API 端点清单 (~147 endpoints, 装饰器静态计数)

### 4.1 格式转换 `/api/v1/convert` (7)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/convert` | 上传文件并提交转换任务 |
| GET | `/jobs/{job_id}` | 查询任务详情 |
| GET | `/jobs` | 列出任务(分页) |
| DELETE | `/jobs/{job_id}` | 取消任务（软删除，置为 CANCELLED） |
| POST | `/jobs/delete` | 批量硬删除任务（仅终态 success/failed/cancelled，运行中拒绝；删除磁盘目录与质检报告）— v2.14 |
| POST | `/detect` | 格式检测(不完整上传) |
| GET | `/capabilities` | 查询支持的格式和CRS |

### 4.2 管理 `/api/v1/admin` (12)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/apikeys` | 创建 API Key |
| GET | `/admin/apikeys` | 列出所有 Key |
| DELETE | `/admin/apikeys/{key_id}` | 吊销 Key |
| PUT | `/admin/apikeys/{key_id}/activate` | 重新启用 Key |
| POST | `/admin/apikeys/{key_id}/regenerate` | 重新生成 Key |
| DELETE | `/admin/apikeys/{key_id}/hard` | 永久删除 Key |
| PUT | `/admin/apikeys/{key_id}/group` | 分配分组 |
| POST | `/admin/groups` | 创建分组 |
| GET | `/admin/groups` | 列出分组 |
| GET | `/admin/groups/{group_id}` | 分组详情 |
| PUT | `/admin/groups/{group_id}` | 更新分组 |
| DELETE | `/admin/groups/{group_id}` | 删除分组 |

### 4.3 批量转换 `/api/v1/batch` (5)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/batch/convert` | 批量上传转换(最多50文件) |
| GET | `/batch/{batch_id}` | 批量任务详情 |
| GET | `/batch` | 列出批量任务 |
| DELETE | `/batch/{batch_id}` | 取消批量任务 |
| GET | `/batch/{batch_id}/jobs` | 列出子任务 |

### 4.4 质检报告 `/api/v1/quality` (7)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/quality/check` | 对已存储文件质检(五维评分) |
| POST | `/quality/check/upload` | 上传文件+质检(旧兼容) |
| GET | `/quality/{report_id}` | 报告详情 |
| GET | `/quality/{report_id}/html` | HTML 报告 |
| GET | `/quality/{report_id}/json` | JSON 报告下载 |
| GET | `/quality` | 列出报告(分页) |
| GET | `/quality/{report_id}/export` | 导出违规(GeoJSON/Excel) |

> v2.10.5: 报告类响应（CheckResponse/Brief/Detail）统一新增 `checked_layers` 字段——本次质检圈定的图层清单（NULL=全部图层）。

### 4.5 质检规则 `/api/v1/quality-rules` (5)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/quality-rules` | 列出规则 |
| POST | `/quality-rules` | 创建规则 |
| PUT | `/quality-rules/{rule_id}` | 更新规则 |
| DELETE | `/quality-rules/{rule_id}` | 删除规则 |
| POST | `/quality-rules/generate/{standard_id}` | 从标准自动生成规则 |

### 4.5a 质检方案 `/api/v1/quality-plans` (6) — v2.10
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/quality-plans` | 查询方案列表 |
| GET | `/quality-plans/{plan_id}` | 查询方案详情 |
| POST | `/quality-plans` | 创建方案 |
| PUT | `/quality-plans/{plan_id}` | 更新方案 |
| DELETE | `/quality-plans/{plan_id}` | 删除方案 |
| POST | `/quality-plans/default` | 初始化默认方案（幂等） |

### 4.5b CAD 检查 `/api/v1/cad` (4) — v2.18
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/cad/{file_id}/info` | CAD 元数据（含缓存状态，未缓存返回 421） |
| GET | `/cad/{file_id}/layers` | CAD 图层清单（名称/颜色/线型/可见性/实体数） |
| GET | `/cad/{file_id}/entities` | CAD 实体分页（layer/entity_type/offset/limit 过滤） |
| GET | `/cad/{file_id}/preview` | CAD 预览图（format=svg/png, width/height 像素，读自磁盘缓存） |

> 设计要点：CAD 快照在**上传提交时**一次性解析缓存（`cad_cache.cache_cad_snapshot`）——因 v2.14 后 `file_data` 被清空（下载返回 410），路由层只读 `stored_files.cad_metadata` 与 `data/previews/`，不解析字节。DWG 先经 `dwg_converter` 转 DXF 再解析，LibreDWG 缺失时跳过缓存不报错。非 dxf/dwg 文件返回 400；文件不存在/无权限 404；未缓存 421。
> 实体缓存上限 `MAX_CACHED_ENTITIES=2000`（超限只收摘要不存实体明细）；预览渲染用自绘 Pillow（ezdxf 1.4.4 无 PillowBackend，不依赖 matplotlib）。

### 4.6 数据存储 `/api/v1/data` (18)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/data/upload` | 上传文件(自动检测+解压+关联标准) |
| GET | `/data/upload/check` | 秒传检查(SHA-256 去重) |
| POST | `/data/upload/chunk` | 分片上传（**v2.20：单片上限=settings.file_chunk_size_mb【10MB】×1.5 余量=15MB，超限 413**；v2.1 起 file_chunk_size_mb 为唯一权威，data.py 内联 2MB 漂移已收敛） |
| GET | `/data/upload/chunk-status` | 分片进度查询 |
| POST | `/data/upload/merge` | 合并分片并入库 |
| DELETE | `/data/upload/chunk` | 取消上传 |
| GET | `/data` | 列出文件(分页,多条件筛选；geometry_type 筛选 v2.10.5 起计数驱动) |
| GET | `/data/stats` | 存储统计（v2.10.5 增 feature_totals{point,line,polygon,total}） |
| GET | `/data/{file_id}/features` | 查询空间要素(GeoJSON) |
| GET | `/data/{file_id}` | 文件元数据（v2.10.5 响应含三维计数） |
| GET | `/data/{file_id}/structure` | 文件结构(图层/字段)；**v2.10.5 重写为 stored_file_layers 真实图层清单**（每层 P/L/G 计数+feature_count 降序；无聚合行的历史数据回退旧伪单层结构） |
| GET | `/data/{file_id}/download` | 下载原始文件 / 导出特征数据。**v2.19.0**：新增 `format` 查询参数（geojson/shp/gpkg/csv/mdb，默认 geojson）；未显式传 format 且留原始字节 → 原样下载；否则聚合三张特征分区表实时导出（ZIP 壳 → 400 提示展开；无原始字节且无特征 → 410）。shp 打包 zip 含 .prj；gpkg 内嵌 CRS；csv utf-8-sig+WKT；mdb 为 ADOX/ACE OLEDB 建的 Access 库（按 layer_name 拆表，几何 GeoJSON 文本+WKT 双 MEMO 列）。**v2.19.2**：导出管线异常兜底（500 友好 JSON + `logger.exception` 留 traceback）。**v2.19.3**：MDB 内联字面量免参数绑定（HYC00/106）、大整数/混型列文本保整（22018）、SHP 中文列名 ASCII 映射（fiona UnicodeDecodeError）。**v2.19.4**：MDB 输出改为 Jet4 / Access 2000 格式（`app/resources/empty_jet4.mdb` 模板复制 + pyodbc 原地写入）——原 ADOX 建库在 ClickToRun 环境被重定向到 ACE 16 生成 accdb 内部结构导致 Access 无法打开。**v2.19.5**：新增 `format=gdb`——File Geodatabase（ArcGIS Pro 原生格式，Pro 不支持个人地理数据库 .mdb；GDAL 3.6+ OpenFileGDB 写，无 arcpy 依赖），返回 `<stem>_epsg_{srid}_gdb.zip`（解压 *.gdb 目录拖入 Pro 即显示图形）；按 layer_name 拆 Feature Class（`lyr_<layer>`，同层混合几何追加 `_<Type>`），CRS 内嵌（crs_wkt），大整数 Integer64 保整，负坐标按 bbox 自动设 `XORIGIN/YORIGIN`（AutoCAD 图纸坐标，默认原点下 "Cannot encode Y value"） |
| PATCH | `/data/{file_id}` | 更新元数据 |
| POST | `/data/batch-classify` | 批量关联标准 |
| DELETE | `/data/{file_id}` | 删除文件+空间分区（**递归**：压缩包壳→子记录+分区一并删） |
| POST | `/data/batch-delete` | 批量删除（同递归语义） |
| POST | `/data/cleanup-archives` | 清理 N 天前的压缩包壳（days=1..30，默认 1）。**v2.19.6 方案 A**：只删 `is_archive=True` 的壳记录，**保留子文件记录与 PostGIS 分区**——先将子记录 `source_archive` 解除关联（提升为独立文件记录）再删壳（自动清理永不 DROP 分区/不丢数据）；显式删除（DELETE/batch-delete/同名覆盖）仍递归删不受影响 |
| POST | `/data/{file_id}/reindex` | 重建空间索引（完成后重跑图层聚合） |
| POST | `/data/{file_id}/quality-check` | 质检(Form)；**v2.10.5 新增 `layers` 参数(JSON 数组)——圈定后所有拓扑规则仅在所选图层内计算，未知图层名 400；报告落 checked_layers**。**v2.20：响应新增 `score_overall`（综合分由后端按 `quality_checker.WEIGHTS` 计算，前端不再自行加权）** |

### 4.7 数据标准 v1 `/api/v1/data-standards` (5)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/data-standards/template` | 下载 Excel 导入模板 |
| POST | `/data-standards/import` | Excel 导入标准 |
| GET | `/data-standards` | 列出标准 |
| GET | `/data-standards/{standard_id}` | 标准详情 |
| DELETE | `/data-standards/{standard_id}` | 删除标准(级联) |

### 4.8 数据标准 v2 `/api/v1/data-standards-v2` (40+)

**标准 CRUD**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/data-standards-v2` | 列出标准(分页) |
| GET | `/data-standards-v2/stats` | 标准汇总统计 v2.15（标准/分类/状态/层组/表/字段/字典/字典项/版本，scope 隔离） |
| POST | `/data-standards-v2` | 创建标准 |
| GET | `/data-standards-v2/{id}` | 标准详情(完整树) |
| PUT | `/data-standards-v2/{id}` | 更新标准 |
| DELETE | `/data-standards-v2/{id}` | 删除标准 |
| GET | `/data-standards-v2/{id}/tables` | 所有表(跨图层) |
| GET | `/data-standards-v2/{id}/qc-summary` | 质检概览(表/字段/字典关联/可用字典) |
| GET | `/data-standards-v2/{id}/graph` | **知识图谱摘要（A 档纯视图）** nodes(standard/group/table/dict)+edges(contains 含 S9 主/次归属、binds 按 (表,字典) 聚合 field_count/primary)+stats；**不含字段/条目明细**（真实最大标准 3288 字段，全给 ~1.5MB）；全局字典按引用纳入，未引用不进图，悬挂边不产生 |
| GET | `/data-standards-v2/{id}/graph/nodes/{node_id}` | 图谱节点展开明细：表→字段列表(含 dict_codes 主绑定在前)；字典→条目(含 parent_code 层级)；层组不支持展开 404；跨标准/未引用全局 404 |

**图层管理**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/data-standards-v2/{id}/layers` | 图层树 |
| POST | `/data-standards-v2/{id}/layers` | 创建图层 |
| PUT | `/layers/{id}` | 更新图层 |
| DELETE | `/layers/{id}` | 删除图层 |
| POST | `/layers/{id}/move` | 移动图层(重新挂载) |
| POST | `/layers/{id}/copy` | 复制图层(递归) |

**表管理**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/layers/{id}/tables` | 图层下的表 |
| POST | `/layers/{id}/tables` | 创建表 |
| GET | `/tables/{id}` | 表详情 |
| PUT | `/tables/{id}` | 更新表 |
| DELETE | `/tables/{id}` | 删除表 |
| POST | `/layers/{id}/tables/{id}/attach` | 挂载表(主/副) |
| DELETE | `/layers/{id}/tables/{id}/attach` | 卸载表 |
| GET | `/tables/{id}/layers` | 表的所有权列表 |

**字段管理**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tables/{id}/fields` | 字段列表 |
| POST | `/tables/{id}/fields` | 添加字段 |
| PUT | `/fields/{id}` | 更新字段 |
| DELETE | `/fields/{id}` | 删除字段 |
| POST | `/tables/{id}/fields/reorder` | 字段排序 |

**字典管理**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/data-standards-v2/{id}/dictionaries` | 标准字典 |
| POST | `/data-standards-v2/{id}/dictionaries` | 创建标准字典 |
| GET | `/dictionaries` | 全局字典 |
| POST | `/dictionaries` | 创建全局字典 |
| GET | `/dictionaries/{id}` | 字典详情 |
| PUT | `/dictionaries/{id}` | 更新字典 |
| DELETE | `/dictionaries/{id}` | 删除字典 |
| POST | `/dictionaries/{id}/items` | 批量添加条目 |
| PUT | `/dictionaries/{id}/items` | 批量更新条目 |
| DELETE | `/dictionaries/{id}/items/{id}` | 删除条目 |
| POST | `/dictionaries/{id}/share` | 提升为全局 |

**字段-字典关联**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/fields/{id}/dict-links` | 查看关联 |
| POST | `/fields/{id}/dict-links` | 创建关联 |
| PUT | `/field-dict-links/{id}` | 更新关联 |
| DELETE | `/field-dict-links/{id}` | 删除关联 |

**版本管理**
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/data-standards-v2/{id}/publish` | 发布(创建版本快照) |
| GET | `/data-standards-v2/{id}/versions` | 版本历史 |
| GET | `/data-standards-v2/{id}/versions/{v1}/diff/{v2}` | 版本对比 |
| POST | `/data-standards-v2/{id}/rollback/{ver}` | 回滚版本 |

**导入/导出/模板**（`import_export.py`；`mode` = `create`（默认）/`upsert`/`update`，JSON 走 Query、Excel/CSV 走 Form）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/data-standards-v2/import/json` | JSON 导入 |
| POST | `/data-standards-v2/import/excel` | Excel 导入（5 Sheet：标准信息/层组/表结构/字段定义/字典定义） |
| POST | `/data-standards-v2/import/csv` | CSV 导入（`files: list`，`name`/`description`/`category`/`mode`）。**整包 ZIP part（.zip 后缀或 PK 魔数）自动解包**（v2.22.3 §六·补七，坏包 400）；解包后 ZIP 内有 `_manifest.json` → 归档路径（按 `roles` 映射读 6 角色，无损重建，含字典与绑定）；无 manifest → **legacy 路径**（每文件一表、字段取首行、类型恒 `string`，v2.22.2 前行为，零破坏）。`标准信息.csv` 的 description/category 作默认值，表单显式值优先 |
| GET | `/data-standards-v2/template/json` | 下载 JSON 空白模板 |
| GET | `/data-standards-v2/template/excel` | 下载 Excel 空白模板（ZIP） |
| GET | `/data-standards-v2/template/csv` | 下载 CSV 归档模板（ZIP）= `_manifest.json` + 6 个角色 CSV（表头 + 一行自洽示例） |
| POST | `/data-standards-v2/{standard_id}/export` | 导出数据标准（`fmt` ∈ `json`/`excel`/`csv`）。**`csv` 为 v2.22.3 补齐**：ZIP **A2-1 归档** = `_manifest.json`（仅格式元信息）+ `标准信息`/`层组`/`数据表`/`字段`/`字典`/`字典项` 6 个角色 CSV，UTF-8 with BOM；**含字典与字段-字典绑定，可无损往返** |
| POST | `/data-standards-v2/{standard_id}/auto-create-table` | 按入库表结构自动生成数据标准（Phase 3） |

> ✅ **v2.22.3 已闭环**：v2.22.2 留档的两条缺口（`import` 3 端点不建 `StandardFieldDictLink`、导出无关联列）均已修复。Excel「字段定义」新增第 10 列「字典编码」、JSON 字段对象新增 `dict_codes`、**CSV 归档含 `字典`/`字典项` 角色 + `字典编码` 列**；`mode=update/upsert` 仍走 `_common.py:315-322` **先删全部再重建**，但模板携带绑定故往返无损。详见 `docs/revision_v2_22_3.md`。

### 4.9 空间操作 `/api/v1/spatial` (5) — v2.11 扩展
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/spatial/{file_id}/transform` | CRS 坐标变换（**v2.11 结果写入 transform_results 表，不再原地修改要素**；**v2.13 接入统一变换引擎**，启用 `resolve_transform_crs` 解析，支持 `Custom:` 前缀/纯数字/中文别名/WKT/proj4，响应新增 `skipped`/`failed`/`warnings` 统计与坐标域校验告警） （bugfix09）转换结果写入时附目标 crs 声明（RFC7946 缺眡即 WGS84） |
| GET | `/spatial/transform-results` | 坐标转换结果列表（分页、按文件筛选） |
| GET | `/spatial/transform-results/{result_id}/download` | 下载转换结果，`format` 三选一 geojson/shp/gpkg（默认 geojson）。下载时对历史结果自动补/纠正目标 CRS（标准 EPSG 用 EPSG URN，自定义 SRID ≥910000 用 VCS-CUSTOM，避免无效 EPSG URN）；shp 打包 zip 且 `.prj` 取 `spatial_ref_sys.srtext` 完整保留自定义投影（shanghaicity 等） |
| DELETE | `/spatial/transform-results/{result_id}` | 删除转换结果 |
| POST | `/spatial/{file_id}/repair` | 几何修复 |

### 4.10 源数据 `/api/v1/source-data` (12)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/source-tables` | 列出源表 |
| GET | `/source-tables/{id}` | 源表详情 |
| POST | `/source-tables` | 创建源表 |
| PUT | `/source-tables/{id}` | 更新源表 |
| DELETE | `/source-tables/{id}` | 删除源表 |
| POST | `/source-tables/{id}/fields` | 添加字段 |
| GET | `/source-dictionaries` | 列出字典 |
| GET | `/source-dictionaries/{id}` | 字典详情 |
| POST | `/source-dictionaries` | 创建字典 |
| POST | `/source-dictionaries/{id}/items` | 批量添加条目 |
| DELETE | `/source-dictionaries/{id}` | 删除字典 |
| POST | `/field-dict-links` | 关联字段-字典 |
| DELETE | `/field-dict-links/{id}` | 解除关联 |

### 4.11 WebSocket `/api/v1/ws` (1)
| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/ws/progress` | 实时任务进度推送(Redis Pub/Sub) |

---

## 5. 服务模块清单

### 5.1 质检子包 `services/quality/`
| 模块 | 职责 |
|------|------|
| `rule_engine.py` | 规则执行引擎 — 加载规则、逐条检查、汇总结果 |
| `rule_generator.py` | 规则自动生成 — 从数据标准字段推导质检规则 |
| `attribute_checker.py` | 属性检查 — 空值/类型/枚举/长度/格式校验 |
| `reporter.py` | 报告生成 — HTML/JSON 报告输出 |
| `models.py` | 质检数据模型 — Pydantic schema |

> **v2.22.1 移除**：`scoring.py`（五维评分孤儿模块）删除——生产零引用，真实评分路径为 `quality_checker._calculate_score_legacy` 单维通用评分 + `reporter._score_color`，16 例专属测试一并移除。

### 5.2 核心服务 `services/`
| 模块 | 职责 |
|------|------|
| `format_detection.py` | 格式自动检测 — GDB/SHP/GeoJSON/KML/DXF/DWG/DGN/MDB 识别（MDB 通过 Magic Bytes `Standard Jet DB` + 扩展名识别，分类为空间格式；DWG 要素解析经 dwg_converter 转换，DGN V7 直接解析、V8 缺 ODA 驱动时优雅降级；**v2.13-bugfix SHP CRS 检测：`_extract_crs` 依赖同目录同名 `.prj`，ZIP 解压链路由 `data.py._detect_and_populate` 的 `companion_contents` 参数把 `.dbf/.shx/.prj/.cpg` 等一并写入临时目录，避免 `.prj` 缺失致 `detected_crs=None` → SRID 回退 4326**） |
| `geometry_repair.py` | 几何修复 — buffer(0) + make_valid |
| `crs_transform.py` | 坐标系变换 — pyproj 投影转换 |
| `custom_crs.py` (v2.10.5) | 自定义坐标系 SRID 解析/注册 — 无 EPSG 编码的投影坐标系确定性分配 SRID 910000~998999（SHA-256，跨进程稳定）并幂等注册 spatial_ref_sys；含假东/假北互换检测纠正（PostGIS 优先读 srtext，故 WKT/proj4 双修正 + 旧行自愈）、坐标域合理性校验、入库 SRID 防线（地理坐标系下坐标超 ±180/±90 告警） |
| `export_service.py` (v2.19) | 转换结果 + 特征数据导出 — 基于 fiona(GDAL/OGR) 实时将 GeoJSON 导出为 GeoJSON / Shapefile(zip，含 .prj WKT + .dbf UTF-8) / GeoPackage（内嵌 CRS）/ **MDB** / **CSV** / **File Geodatabase**；v2.19.0 新增 `export_mdb`（win32com+ADOX.Catalog/ACE OLEDB 12.0 建空库 → pyodbc 写表，按 `layer_name` 拆表、`_safe_table_name`/`_safe_column_name` 前缀规避保留字、几何 GeoJSON 文本+MEMO 双列存、Access TEXT 上限 255 故长文本用 MEMO）与 `export_csv`（utf-8-sig + WKT + 属性并集列）；`_safe_stem` 清洗中文文件名（保留 CJK）；同步库在事件循环外线程池执行。**v2.19.5** 新增 `export_gdb`：OpenFileGDB(GDAL 3.6+, fiona 1.10.1) 纯 Python 创建 .gdb 目录打包 zip，按 layer_name 拆 Feature Class、同层混合几何追加 `_<Type>` 后缀、`layer_name` 表维度列剔除、CRS crs_wkt 内嵌、`_build_gdb_schema` int→int64(>32bit 升级) + `TARGET_ARCGIS_VERSION=ARCGIS_PRO_3_2_OR_LATER` 保整、`CHECK_WITH_INVERT_PROJ=False` 规避投影多边形写入域校验误报、`_gdb_origin` 按 bbox 自动计算 `XORIGIN/YORIGIN` 支持负坐标图纸数据（否则 "Cannot encode Y value"）；旧注释「GDB 不可行（仅 OpenFileGDB 只读）」已过时（GDAL 3.6+ 支持写 FileGDB） |
| `spatial_loader.py` | 空间数据入库 — 要素分表写入 PostGIS；格式路由按扩展名（未知格式走 Fiona 嗅探，不猜测默认格式）；DWG 路径含 CAD 闭合多段线→面语义还原（`_cad_lines_to_polygons`） |
| `spatial_query.py` | 空间查询 — GeoJSON 输出 |
| `feature_store.py` | 要素存储管理 — 分区表操作 |
| `topology_checker.py` | 拓扑检查 — 自相交/重叠/间隙/极大面/极小面检测(v2.10.1 移除 api_key_id 过滤，仅用 stored_file_id；**v2.10.3 面积/距离/长度返回真实米制，前端 km² 输入 ×1e6 转 m²，修正默认阈值 max_area=1e10 / min_area=0.01**；**v2.10.4 改 `ST_Transform(geom,4326)::geography` 为平面投影 `ST_Transform(geom,4549)`（CGCS2000/3度GK，米制），彻底规避 geography 球面面积 `area < 0.0` 致 T09 面裂隙查询抛 InternalServerError 并级联拖垮整次质检 → 500；T03/T04/T06/T07/T08/T09 增加 try/except + db.rollback 隔离，单规则异常不再中断整次检查**；**v2.10.5 米制量算策略重构：硬编码 4549 替换为 `MetricStrategy` 运行期自适应 —— ①原生 SRID 为米制投影 → 原生量算零变换(mode=native)；②地理坐标系 → 按数据质心动选 UTM 带 EPSG:326xx/327xx(mode=utm)；③无法判定 → 兜底 4549(mode=legacy)。修复上海地方坐标系文件误标 4326 时 "transform: Invalid coordinate (2049)" 拖垮 T03/T04/T05/T06/T07/T08/T09 的问题；T05 微短线顺带修复原用度数长度对比米制阈值的单位错配**；**v2.10.5 图层圈定：`run_topology_checks(layer_names=...)` 全部 14 个查询点贯通（9 处 ORM 附加 where + 5 处原生 SQL `_sql_clause` + ANY(:layers)），圈定后所有规则（含 T6-T9 空间关系类）仅在所选图层要素集内计算；None/空=全部图层**；**v2.10.5附7 planar 平面量算模式：无 CRS 的 CAD 本地平面坐标（如 DWG X∈[2.7万],Y∈[-1900]）被盲标 4326 后单点选带产出荒谬 UTM 带（EPSG:32760）致全量 ST_Transform 报 Invalid coordinate 拖垮 T03/T04/T06/T09 —— 新增 mode=planar（零变换直接量算）；`_utm_strategy_for_data` 改全量 bbox 度域校验（任一角 |lon|>180 或 |lat|>90 → planar + 告警）；legacy(4549) 路径全部退役改 planar（对 SRID=0 数据 transform 本就必炸）**) |
| `api/v1/quality.py` / `api/v1/data.py` | 质检 API 端点。**v2.10.4 修复 `MissingGreenlet` → 500**：`run_quality_check` 运行期间 AsyncSession 会过期 `sf`(StoredFile) 属性，原代码在构建报告时访问 `sf.original_name` 触发同步懒加载（异步会话禁止同步 IO）→ 500；改为加载 `sf` 后立即缓存 `file_name`，上传端点 `db.expire_all()` 前同样缓存文件名 |
| `structure_analyzer.py` | 结构分析 — 图层/字段结构解析 |
| `attribute_processing.py` | 属性处理 — 类型转换/编码处理 |
| `gdb_processor.py` | GDB 处理 — ESRI Personal GDB 解析 |
| `archive_extractor.py` | 压缩包解压 — ZIP/RAR/7z |
| `quality_checker.py` | 质检入口 — 统一质检调度(v2.10.1 移除 api_key_id 传递；**v2.10.5 透传 layer_names 图层圈定至 run_topology_checks**) |
| `quality_exporter.py` | 报告导出 — GeoJSON/Excel 违规导出 |
| `script_generation.py` | 脚本生成 — ogr2ogr 命令行生成 |
| `data_standard_parser.py` | 标准解析 — Excel/JSON 标准导入解析 |
| `progress_tracker.py` | 进度追踪 — WebSocket 进度推送 |
| `obj_parser.py` | OBJ 解析 — 3D 模型解析 |
| `dwg_converter.py` (v2.10.5) | DWG→DXF 转换 — 调用 LibreDWG dwg2dxf（`tools/libredwg/`，120s 超时），供 spatial_loader 实现 DWG 要素提取；工具缺失/转换失败优雅降级为空 |
| `esri_shapebin.py` (v2.12.1) | PGeo SHAPE BLOB 解码器 — 解析 `SHPT_GENERAL*` 二进制几何为 GeoJSON；类型/flag 校验拒绝 WKB 伪装体（`0x00000101`），WKB/WKB 字符串直通；多边形环方向 shoelace 判定（CW=外环/CCW=空洞），2 环 → Polygon+holes 而非 MultiPolygon |
| `mdb_processor.py` (v2.12) | MDB 处理 — ESRI Personal Geodatabase 读取（pyodbc + MS Access Driver），提取空间表为 GeoJSON features；支持 WKB/WKT/shapebin(PGeo) 几何存储格式；**v2.12.1 新增 `_load_crs_map()`**：读取 `GDB_GeomColumns`（TableName→SRID）× `GDB_SpatialRefs`（SRID→SRTEXT 的 ESRI WKT），`detected_crs` 随 stored_file 入库，使 GK 米制坐标避免被误存为 WGS84 |
| `feature_store.py` | 要素存储管理 — 分区写入/预览查询；**v2.10.5 新增 `aggregate_layer_stats()`**：三分区表 GROUP BY 实时聚合 → stored_file_layers DELETE+INSERT 幂等落表 + update() 刷新文件级三维计数（绕过身份映射）；上传/合并/reindex 四路径挂接，全部 `begin_nested()` SAVEPOINT 隔离，聚合失败不拖垮主事务；**v2.10.6 `transform_features_by_file` / `repair_features_by_file` 事务重构**：移除内部 `commit()`（由调用方统一管理事务），移除 `api_key_id` 过滤（文件级权限已在 API 端校验），修复与后续元数据更新的事务冲突；**v2.13 `transform_features_by_file` 写回路径收敛到统一引擎**（`resolve_transform_crs` + `transform_geometry`），移除重复裸 pyproj `_transform_coords`/`_transform_xy` 死代码，坐标域失败计入 `failed` 不写回损坏几何 |
| `spatial_loader.py` | 空间数据入库；**v2.10.5 新增 `_promote_cad_layers()`**：DXF/DWG 要素属性 Layer 提升为 feature.layer_name（fiona 对 CAD 恒报 'entities'）；DXF 直传路径补挂 `_cad_lines_to_polygons`（修复闭合多段线不转面缺陷，与 DWG→DXF 链路同语义） |
| `transform_engine.py` (v2.13) | **统一坐标变换引擎** — 汇总此前分散的变换逻辑，提供单一入口：`resolve_transform_crs()` 统一解析 CRS（复用 `custom_crs.resolve_ingest_srid_async` 权威链路，返回 `ResolvedCRS`，`srid` 供落库审计、`pyproj_crs` 供 pyproj 变换，自定义投影坐标系用 WKT 原文使 pyproj 直接可解析）；`transform_geometry` 变换前后坐标域校验（识别投影误标地理源、目标越界）+ **inf/NaN 拒绝**（结果含非有限值返回 None 计入 failed，杜绝 `[Infinity, Infinity]` 损坏几何）+ 失败统计；GeometryCollection 子几何正确递归；`TransformStats`（total/transformed/failed/skipped/warnings）；`transform_feature_dict/list` 批量接口；`transform_db_features` 跨三表读取+变换+统计。修复「手动输入坐标系无效」根因（旧 `spatial.py` 用 `resolve_crs` 无法解析 `Custom:` 前缀与自定义投影系，静默回退 WGS84） （v2.13.0-bugfix02）自定义 SRID 时从 spatial_ref_sys 复用注册(纠正后) 的 proj4text 作为 pyproj 定义（解决上海城建 假东/假北互换、变换漂移到太平洋的 bug）|
| `cad_parser.py` (v2.18) | **CAD 解析** — ezdxf 读取 DXF：图层树（LAYER 表 + 实体计数统计）、实体明细（handle/type/layer/端点/长度/文本/块名，含 LWPOLYLINE 展开、ARC/CIRCLE 弧长周长、TEXT/MTEXT 内容、INSERT 块引用）、文本内容（TextEntry 文本+坐标+图层）、元数据（DWG/DXF 版本名映射 AC1032→R2018、单位、图块/标注/填充存在性）；`aci_to_rgb()` ACI→RGB、`_SKIP_LAYERS={"0","Defpoints"}` 过滤；超限实体只统计不存储 |
| `cad_preview.py` (v2.18) | **CAD 预览渲染** — `CADPreviewGenerator`：`generate_svg`（XML 文本手写，bbox 自动缩放 + 图层配色）、`generate_png`/`generate_thumbnail`（自绘 Pillow `_render_pillow`，直接绘制 LINE/LWPOLYLINE/CIRCLE/ARC/TEXT/MTEXT/ELLIPSE，**不用 ezdxf 官方 PillowBackend**——v1.4.4 无该模块，亦不依赖 matplotlib）；无效/空图 fallback 占位图 |
| `cad_cache.py` (v2.18) | **CAD 快照缓存** — 上传提交时 `cache_cad_snapshot(sf, content, filename)` 在线程池内一次解析：元数据+图层+实体写入 `sf.cad_metadata`(JSONB)，预生成 `data/previews/{file_id}.svg/.png` 写入 `sf.cad_preview_path`；DWG 先经 `dwg_converter.dwg_to_dxf`；`MAX_CACHED_ENTITIES=2000` 上限；解析失败仅日志不阻断上传；被 `data.py` 上传管线在 `_detect_and_populate` 后调用 |

---

## 6. 脚本清单

| 脚本 | 用途 | 使用场景 |
|------|------|----------|
| `init_db.py` | 数据库初始化 | 首次部署/重建 |
| `create_quality_tables.py` | 创建质检相关表 | 手动建表 |
| `create_source_tables.py` | 创建源数据表 | 手动建表 |
| `check_db.py` | 数据库状态检查 | 诊断 |
| `check_schema_cols.py` | Schema 列检查 | 诊断 |
| `migrate_data_standard_v2_2.py` | v2.2 数据标准迁移 | 版本升级 |
| `migrate_auth_patterns.py` | 认证模式迁移 | 版本升级 |
| `fix_gdb_metadata.py` | GDB 元数据修复 | 数据修复 |
| `cleanup_gdb.py` | GDB 清理 | 数据清理 |
| `cleanup_orphan_data.py` | 孤儿数据清理 | 数据清理 |
| `repair_feature_srid.py` (v2.10.5) | 存量要素 SRID 修复 — 对比源文件 CRS 与要素实际 SRID，重标误标数据 + 补全元数据 + 注册自定义坐标系；默认 dry-run，`--apply` 执行，`--file-id` 过滤 | 数据修复 |
| `backfill_layer_stats.py` (v2.10.5) | 「图层 × 几何类型」统计存量回填 — 从三分区表聚合重建 stored_file_layers + 文件级三维计数；幂等可重复执行；默认 dry-run，`--apply` 执行，`--file-id` 过滤 | 数据回填 |
| `create_test_db.py` (2026-09-01) | 测试库初始化 — 幂等创建 `vcs_test` + 启用 PostGIS（asyncpg + sync URL，`--url` 可覆盖） | 测试环境准备 |
| `smoke_imp_exp_ui.py` (v2.22.3) | 数据标准导入/导出弹窗真实 DOM 冒烟 — Playwright + Chromium 以 `file://` 加载 `index.html`，**无需后端**；31 例校验单框三段、格式按钮高亮、`accept`/`multiple` 随格式切换、JSON 框与 dropzone 互斥、**CSV 导出已启用（ZIP 文案 + tooltip 说明归档含 `_manifest.json` 与字典绑定）**、无 `pageerror` | 前端回归（改动 `index.html`/`app.js` 后必跑） |
| `verify_csv_archive.py` (v2.22.3) | CSV 归档往返**人工校验**脚本 — 独立库 `vcs_verify`（自动建/删 + 启用 PostGIS，**不碰 `vcs`/`vcs_test`**，可反复跑）；打印 ZIP 全部成员（BOM 状态 + 解码后内容）与回导后的层组/字段/字典/绑定，用于肉眼确认交付物形态；测试跑的是断言，这个跑的是「给人看」 | 人工验收 |
| `mutation_check.py` (v2.22.3) | **变异测试** harness — 对 `import_export.py` 的关键契约逐条注入语义变异（必需角色 400、名称不覆盖、空字典存活、重复编码 diagnostics、未知字典告警、BOM 剥离、legacy 降级、`standard` 角色错位/不消费、重复层组码跳过等共 11 条），跑 `tests/test_imp_exp_dict_roundtrip.py` 验证「测试真的会红」；每次运行先验基线 PASS、逐条变异、结束自动还原并复验基线。当前 **11/11 全部捕获，0 存活** | 回归深度验证（改 `import_export.py` 后必跑） |
| `deploy/build_bundle.ps1` (v2.23.0) | **联网机离线资源包生成器** — 收集 Python 3.13.3 / PG 15.15 / PostGIS 3.6.1 / VC++2019 / ACE2016 安装包、复制 `Redis8/` 与 `tools/libredwg/` 二进制、`pip download` 全量 wheel（`--only-binary=:all:`，`-SkipWheels` 可跳过）、复制种子 JSON，最后为 `offline/` 下每个文件写 SHA-256 + 体量到 `manifest.json`（幂等可重复跑） | 交付前在联网机执行一次 |
| `deploy/deploy_offline.bat` (v2.23.0) | **离线部署入口** — 检测到非管理员时 UAC 自提升（`Start-Process -Verb RunAs`）；转调 `deploy\scripts\deploy.ps1`；支持 `--from N`（幂等续跑，如 `--from 40` 重建 venv / `--from 50` 重建库）与 `--dry-run`（只校验不真装）；退出码透传（0=SUCCESS） | 目标机安装/续跑/干跑 |
| `deploy/scripts/*.ps1` (v2.23.0) | **十二阶段部署脚本** — `00_preflight`(manifest SHA-256 全量校验+管理员)/`10_install_python`/`20_install_postgres`/`21_install_postgis`/`22_install_ace`(**非关键** WARN 不中断)/`25_install_vcredist`(**非关键**)/`30_setup_redis`/`40_setup_venv`(离线 wheel 安装)/`50_init_database`(建库+PostGIS+alembic head)/`60_seed_and_key`(写 .env + ADMIN_INIT_KEY + 种子导入, 凭证落 `deploy\.install_state.json`)/`90_start_services`(uvicorn 45679 + celery + flower 5555)/`99_verify`(健康检查+打印凭证)；`deploy.ps1` 编排器（`--from/--only/--dry-run`，失败分级，日志落 `deploy/logs/`） | 目标机部署 |

---

## 7. Workers (Celery 任务)

| 模块 | 职责 |
|------|------|
| `celery_app.py` | Celery 应用配置, Broker/Backend 连接 |
| `tasks.py` | 核心任务 — 单文件转换、质检执行 |
| `batch_tasks.py` | 批量任务 — 批量转换编排 |
| `beat_schedule.py` | 定时任务 — 临时文件清理、日报生成 |

---

## 8. 核心模块 `core/`

| 模块 | 职责 |
|------|------|
| `auth_context.py` | 认证上下文 — API Key 验证 |
| `security.py` | 安全工具 — SHA-256 哈希、密钥生成 |
| `cache.py` | 缓存管理 — Redis 缓存策略 |
| `constants.py` | 常量定义 — 全局常量 |
| `db_utils.py` | 数据库工具 — 连接管理 |
| `deps.py` | FastAPI 依赖注入 — 会话/认证 |
| `file_utils.py` | 文件工具 — 路径/大小计算 |
| `logging_setup.py` | 结构化日志 — structlog 配置 |
| `rate_limit.py` | 速率限制 — slowapi 配置 |
| `security_middleware.py` | 安全响应头 — CSP/HSTS 等 |
| `temp_cleanup.py` | 临时文件清理 — 定期清理 |
| `trace_middleware.py` | 请求追踪 — TraceID 注入 |

---

## 9. 配置项

### 环境变量 (.env)
```bash
# 数据库
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/vcs_db
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/vcs_db

# 连接池（异步引擎：主请求 + 子任务；同步引擎：GDB 读取串行化）
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=15
DB_POOL_RECYCLE=1800
DB_POOL_PRE_PING=true
DB_POOL_TIMEOUT=30
MAX_CONCURRENT_DB_TASKS=5

# Redis / Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# 安全
SECRET_KEY=change-me-in-production   # 【v2.20 弃用】全仓零引用，勿再配置；存量 .env 可保留（字段仅兼容）或删除
# 引导密钥（v2.15.1 起强制）：必须为强随机值；缺失/弱口令时应用拒绝启动
ADMIN_INIT_KEY=<replace-with-strong-random-key>

# 日志（v2.20 新增，可配置）
LOG_FILE=./data/logs/vcs.log   # 空串=仅控制台不写盘
LOG_MAX_BYTES=10485760         # 单文件轮转阈值（10MB）
LOG_BACKUP_COUNT=5             # 滚动保留份数

# 应用
MAX_UPLOAD_SIZE_MB=500
MAX_DATA_STORE_SIZE_MB=200
MAX_BATCH_SIZE=50
SANDBOX_LIMIT=1000
# 数据目录（config.py 硬编码默认值, 未暴露 env）:
#   upload_dir="./data/uploads"  output_dir="./data/outputs"
#   CAD 预览目录 = upload_dir 父目录下的 previews/（v2.18, 如 ./data/previews）
```

### Docker Compose 服务
| 服务 | 端口 | 说明 |
|------|------|------|
| postgres | 5432 | PostgreSQL/PostGIS |
| redis | 6379 | Redis 7 |
| api | 45679→8000 | FastAPI 主服务 |
| worker | - | Celery Worker (可水平扩展) |
| flower | 5555 | Celery 监控 |
| beat | - | 定时任务调度 |
| migrate | - | 一次性迁移 Job |

---

## 10. docs/ 设计文档体系

> `docs/` 采用**精简主目录 + archive 归档**结构。  
> 历史详细文档已归档至 `docs/archive/`，主目录仅保留汇总和最终交付物。

### 主目录结构（2026-09-01 清理后 · 2026-09-29 复核）

```
docs/
├── revision_history.md      # 版本修订记录汇总（最新追加在最前）
├── revision_v2_19_[0-6].md  # v2.19.x 修订记录（7 个）
├── revision_v2_20_0.md      # v2.20.0 修订记录
├── revision_v2_21_0.md      # v2.21.0 修订记录
├── revision_v2_22_0.md      # v2.22.0 修订记录
├── revision_v2_22_1.md      # v2.22.1 修订记录
├── revision_v2_22_3.md      # v2.22.3 修订记录（CSV 导出补齐 + 字段-字典绑定模板往返）
├── revision_v2_22_2.md      # v2.22.2 修订记录（导入/导出/模板单框三段重构 + 字典绑定链路留档）
├── review_v2.16.0.md        # v2.16.0 审查报告
├── qc_redesign_v5.html      # 最终质检页设计原型（v2.6 实现依据）
├── stage_report_2026-09-01.md # 项目审查报告 + 清理日志 + 优化建议（2026-09-01）
└── archive/                  # 历史归档（30 个文件）
    ├── revision_v2_*.md      # 各版本详细修订记录（12 个，均为 v2.22.0 之前）
    ├── technical_spec_*.md   # 技术方案（4 个）
    ├── test_manual_*.md      # 测试手册（8 个）
    ├── plan_v2_8.md          # 版本规划
    ├── proposal_*.md         # 功能提案
    ├── *-refactor-plan.md    # 重构方案（2 个）
    ├── data-ingestion.md     # 数据入库方案
    ├── environment-notes.md  # 环境说明
    └── legacy_root/          # 2026-09-01 由根目录归档的旧版文档（8 个）
        ├── overview.md  DEMO.html  VCS_项目进度汇报.html
        ├── VCS_技术方案.md  VCS_数据标准存储重构方案.md  VCS_质检工作台优化方案.md
        └── VCS_项目修订全纪录.md  VCS_优化实施记录.md
```

> **v2.22.3 续**：主目录 revision 文档新增 `revision_v2_22_3.md`，主目录 v2.19.0 起的修订记录现为 **16 个**；`archive/` 仍为 30 个文件（`archive/revision_v2_*` 的 12 个全部早于 v2.22.0，未变）。
>
> **v2.22.2 复核修正**：原清单称 revision 文档「均归档至 `archive/`（12 个）」且版本停在 v2.15.0，与实际不符 ——
> v2.19.0 起的修订记录（15 个）均留在**主目录**，`archive/revision_v2_*` 的 12 个全部早于 v2.22.0。归档数为 30（原写 38）。

### 文档规则

| 规则 | 说明 |
|------|------|
| 历史不改 | 已归档的文件不再修改，仅追加 |
| 汇总更新 | 每次发版在 `revision_history.md` 追加新段落 |
| 原型保留 | 仅保留最终版 HTML 原型（`qc_redesign_v5.html`） |
| 新版本发布 | 在 `revision_history.md` 追加 + 原文件归档至 `archive/` |

### 新版本发布时 docs/ 操作

```bash
# 1. 在 revision_history.md 追加新版本段落（必写）
# 2. 如有单独详细记录，写入 docs/revision_v{X}_{Y}.md 后归档至 archive/
# 3. 技术方案 / 测试手册（仅重大功能时）写入后归档至 archive/
```

---

## 11. 部署脚本

### 一键启动

| 脚本 | 平台 | 功能 |
|------|------|------|
| `restart.bat` | Windows | 杀旧进程 → venv → 依赖 → 迁移 → 启动 uvicorn |
| `restart.sh` | Windows(Git Bash) | 同 restart.bat 的 Bash 版本 |
| `deploy_windows.bat` | Windows | 完整部署: 检查 PG/Redis → venv → 依赖 → 迁移 → 启动 uvicorn + celery + flower |
| `deploy_linux.sh` | Linux | 完整部署: apt 安装 PG/Redis/GDAL → venv → 依赖 → 迁移 → 启动全部服务 |
| `start_server.bat` | Windows | 最简启动: 直接 python -m uvicorn (端口 45678) |

### 部署流程对比

```
restart.bat (快速重启)          deploy_windows.bat (完整部署)
─────────────────────          ──────────────────────────
[1/6] venv 检查                [1/8] PostgreSQL 服务检查
[2/6] 依赖检查                 [2/8] Redis 服务检查
[3/6] 杀旧进程                 [3/8] venv 检查
[4/6] 数据目录                 [4/8] 依赖检查
[5/6] Alembic 迁移             [5/8] 杀旧进程
[6/6] 启动 uvicorn             [6/8] 数据目录 + 迁移
                               [7/8] 启动 uvicorn + celery + flower
                               [8/8] 健康验证
```

### 关键端口

| 端口 | 服务 | 备注 |
|------|------|------|
| 45679 | FastAPI (主) | 对外统一端口 |
| 45678 | FastAPI (备用) | start_server.bat 用 |
| 45681 | FastAPI (备用) | restart.bat 回退（45680 为历史错误端口，v2.5 已废弃） |
| 5555 | Flower | Celery 监控 |
| 5432 | PostgreSQL | 数据库 |
| 6379 | Redis | 缓存/消息队列 |

### 日志文件

| 文件 | 内容 |
|------|------|
| `server_out.log` | uvicorn stdout |
| `server_err.log` | uvicorn stderr |
| `worker_out.log` | celery worker stdout |
| `worker_err.log` | celery worker stderr |
| `flower_out.log` | flower stdout |
| `flower_err.log` | flower stderr |

---

## 12. Docker / 容器化

### Dockerfile (多阶段构建)

```
base (python:3.11-slim)
├── 系统依赖: GDAL, GEOS, PROJ, libpq
├── Python 依赖: requirements.txt
├── 项目代码
└── 非 root 用户 appuser
    │
    ├── api  → uvicorn (端口 8000, 4 workers)
    ├── worker → celery worker (concurrency=4)
    └── flower → celery flower (端口 5555)
```

### Docker Compose 服务

| 服务 | 镜像/构建 | 端口 | 依赖 |
|------|-----------|------|------|
| postgres | postgis/postgis:15-3.4-alpine | 5432 | - |
| redis | redis:7-alpine | 6379 | - |
| migrate | base + alembic upgrade | - | postgres |
| api | api target | 45679→8000 | migrate, redis |
| worker | worker target (×2 replicas) | - | migrate, redis |
| flower | flower target | 5555 | redis |
| beat | base + celery beat | - | migrate, redis |

### Docker 网络

```
vcs_net (bridge)
├── postgres ←── api, worker, beat, migrate
├── redis    ←── api, worker, flower, beat
└── api      ←── 外部 (45679)
```

### Docker 数据卷

| 卷名 | 挂载点 | 用途 |
|------|--------|------|
| pg_data | /var/lib/postgresql/data | PostgreSQL 数据 |
| redis_data | /data | Redis 持久化 |
| vector_uploads | /data/uploads | 上传文件 |
| vector_outputs | /data/outputs | 转换输出 |

---

## 13. 测试

### 测试结构

```
tests/
├── conftest.py                       # 共享测试基础设施（engine/test_db/client/api_key_header）
├── test_api.py                       # API 集成测试 + 服务单元测试（vcs_test 库，34 用例）
├── test_format_detection_mdb.py      # MDB 格式检测单元测试
├── test_mdb_processor.py             # MDB processor 单元测试
├── test_upload_mdb.py                # MDB 上传流程测试
├── test_crs_detection_zip.py         # ZIP CRS 探测
├── test_esri_shapebin.py             # PGeo SHAPE BLOB 解码器
├── test_feature_store_transform.py   # 要素存储变换
├── test_format_normalization.py      # 几何类型归一化单元测试
├── test_transfer_engine.py / test_export_service.py / test_transform_result_crs.py  # 变换/导出
├── test_cad_parser.py                # CAD 解析服务（v2.18: 图层/实体/文本/元数据，19 用例；v2.22.1: +5 分支覆盖）
├── test_cad_preview.py               # CAD 预览渲染（v2.18: SVG/PNG/缩略图/fallback，19 用例；v2.22.1: +12 降级/bbox/渲染分支）
├── test_cad_cache.py                 # CAD 快照缓存（v2.22.1: 13 用例 — 异步入口/构建链路/预览生成 覆盖 cad_cache 70%→100%）
├── test_cad_routes.py                # CAD 路由集成（v2.18: info/layers/entities/preview + 400/404/421，14 用例，session 级 fixture 规避 5/min 上传限流；v2.22.1 批五: TestCADBranchCoverage +4 → 18 用例）
├── test_export_service_branches.py   # export_service 低频分支收口（v2.22.1 批五: 20 用例 — _build_gdb_schema 跨行 int64/bool→str/MDB 合并类型升级/Jet4 模板链等，使 export_service 34 miss → 0）
├── test_data_cad_cache_fail.py       # CAD 快照缓存失败吞异常分支（v2.22.1 批五: 1 用例 — data.py 上传流 cache_cad_snapshot 异常不阻断）
├── test_feature_store_guards.py      # load_file_features_geojson 守卫分支（v2.22.1 批五: 3 用例 — ST_AsText 空值跳过/empty 几何跳过/正常路径，全库最后 2 miss 清零）
├── test_imp_exp_dict_roundtrip.py    # 字段-字典绑定模板往返（v2.22.3: 21 用例 — Excel 第 10 列/JSON dict_codes/CSV ZIP 导出/往返保绑定/质检联动）
├── test_frontend_imp_exp_ui.py       # 导入导出弹窗内容契约（v2.22.2: 30 用例 — 含 node --check 语法关卡、孤儿/重复 DOM id、CSV 导出已启用）
├── fixtures/cad/                     # CAD 测试夹具（v2.18: simple.dxf/complex.dxf + 生成脚本；v2.18.1: 新增真实 DWG real_sample.dwg 回归锚）
└── integration/
    ├── test_mdb_integration.py       # MDB 集成（无测试文件时自动跳过）
    └── (fixtures/mdb/ 测试数据目录)
```

`tests/conftest.py`（2026-09-01 新增）承载共享 `_test_engine`/`_TestSession`/`override_get_db`/
`test_db`/`client`/`api_key_header`：`test_db`（session 级，非 autouse）在 yield 前设置
`app.dependency_overrides[get_db]` + `create_all`，yield 后 `drop_all` + 清除 override——
消除 test_api.py 此前模块顶层 import 副作用，integration 测试不受污染。

### 测试数据库

- **独立测试库**: `vcs_test` (不污染生产库 `vcs_db`)
- **需要 PostGIS**: JSONB / PostGIS Geometry / LIST 分区表
- **初始化**: `scripts/create_test_db.py`（幂等：建库 + 启用 PostGIS，需 sync DB 可用）

### 测试分类

| 测试类 | 类型 | 覆盖内容 |
|--------|------|----------|
| `TestHealth` | 集成 | 健康检查端点 |
| `TestAuth` | 集成 | API Key 认证 (缺失/无效/有效) |
| `TestAdmin` | 集成 | API Key 创建/列表/权限 |
| `TestConversion` | 集成 | 格式转换提交/检测/安全过滤 |
| `TestQuality` | 集成 | 质检完整/不完整文件 |
| `TestSecurity` | 单元 | 哈希/SQL注入过滤/关键字段保留 |
| `TestFormatDetection` | 单元 | GeoJSON/未知格式/文件不存在 |
| `TestCRSTransform` | 单元 | EPSG解析/中文别名/空值 |
| `TestQualityChecker` | 单元 | 完整/缺失关键字段 |
| `test_format_normalization` | 单元 | 3D前缀/后缀/Multi归一化/边界 |
| `test_cad_parser` (v2.18) | 单元 | 图层树/AC→R版本映射/ACI颜色/文本提取/实体分页/超限截断 |
| `test_cad_preview` (v2.18) | 单元 | SVG/PNG/缩略图生成/bbox缩放/空图fallback |
| `test_cad_routes` (v2.18) | 集成 | 上传后快照缓存/四路由响应/400非CAD/404不存在/421未缓存/鉴权 |
| `test_cad_real_dwg` (v2.18.1) | 回归 | 真实 DWG→LibreDWG dwg2dxf→DXF→CADParser→SVG/PNG 全链（fixture: real_sample.dwg，4 用例） |

### 运行测试

```bash
# 需要先启动 PostgreSQL + Redis
.venv\Scripts\python.exe -m pytest tests/ -v                      # 详细输出（全量 1537 passed, v2.20.0）
.venv\Scripts\python.exe -m pytest tests/ -v --cov=app           # 覆盖率
.venv\Scripts\python.exe -m pytest tests/ -v --cov=app --cov-report=xml  # XML 报告
```

> 2026-09-01 已修复预存测试缺陷：conftest 共享 fixture 消除 override 副作用
> （全量 144 passed）；`critical_fields_status` 由 `quality_checker._analyze_critical_fields_legacy`
> 填充（缺失→present=False）；`TestQuality` 2 用例端点改为 `/api/v1/quality/check/upload`。

### 测试配置

- **Celery 同步模式**: `task_always_eager=True` (不依赖 Redis/Worker)
- **异步测试**: pytest-asyncio
- **loop scope**: `pytest.ini` 统一 `asyncio_default_test_loop_scope=session` / `asyncio_default_fixture_loop_scope=session`（修复 Windows ProactorEventLoop 下模块级 async engine "Event loop is closed"）
- **Fixtures**: `tests/conftest.py` 提供共享 `test_db`（session 级自动建表/销毁 + override 设置/清除）、`client`（httpx AsyncClient）、`api_key_header`（真实入库的 API Key）

---

## 14. CI/CD

### GitHub Actions

**文件**: `.github/workflows/ci.yml`

**触发条件**:
- `push` → main, develop
- `pull_request` → main

### CI 流程

```
┌─────────────────────────────────────────────────┐
│  Job: test (ubuntu-latest)                      │
│                                                 │
│  Services:                                      │
│  ├── postgres (postgis/postgis:16-3.4)          │
│  └── redis (redis:7-alpine)                     │
│                                                 │
│  Steps:                                         │
│  [1] checkout@v4                                │
│  [2] setup-python@v5 (3.13, pip cache)          │
│  [3] pip install requirements.txt + test deps   │
│  [4] bandit security scan (continue-on-error)   │
│  [5] pytest + coverage                          │
│  [6] codecov upload (push only)                 │
└─────────────────────────────────────────────────┘
```

### CI 依赖

| 依赖 | 用途 |
|------|------|
| pytest | 测试框架 |
| pytest-asyncio | 异步测试支持 |
| pytest-cov | 覆盖率收集 |
| bandit | Python 安全扫描 |

### CI 环境变量

```yaml
DATABASE_URL: postgresql+asyncpg://vcs:testpass@localhost:5432/vcs_test
DATABASE_URL_SYNC: postgresql://vcs:testpass@localhost:5432/vcs_test
REDIS_URL: redis://localhost:6379/0
ADMIN_INIT_KEY: <strong-random>   # 注意变量名是 ADMIN_INIT_KEY（非 API_KEY_INIT）
DEBUG: "true"
```

### 覆盖率基线（v2.15.1 建立，v2.16.0 路由级测试 + P0 服务层测试补足）

> **⚠️ P5c 测量修正（重要）**: 新增 `.coveragerc` 启用 `[run] concurrency = greenlet,thread`。
> 此前 coverage 未追踪 greenlet 切换，**async 路由 handler 的执行体不被统计** → 路由/服务层的
> async 代码覆盖率被系统性低估（实测同一 handler 测试 quality.py 18% → 28%）。
> 修正后全量数值整体上移，与旧数值**不可直接比较**（见下方各版本记录均标注测量口径）。

- **当前基线（v2.22.1）**: **100.00%** 总覆盖率（`pytest --cov=app --cov-config=.coveragerc`，**1587 例通过**；11094 stmts / 0 miss）。P7c（v2.17.0）曾达 100%，此后 v2.18 CAD + v2.19 导出新增代码致使回跌（v2.20.0 实测 98.94% / 118 miss）；v2.22.1 第四批收回 `logging_setup.py`/`cad_parser.py`/`cad_cache.py`/`cad_preview.py`（71 行），第五批清零 `export_service.py`(34)/`cad 路由`(4)/`data.py`(7)/`feature_store.py`(2) → **全库 100%（第二次里程碑）**；P7 技术债闭环，见 §17「覆盖回跌」行
- **门槛**: `--cov-fail-under=25`（写入 `pytest.ini` addopts，本地与 CI 一致，低于即失败；历史基线 73% 见下方 v2.16.0 记录）
- **v2.16.0 路由级测试成果**:
  - 新增 `tests/test_data_routes.py`（15 例）：`data.py` 查询/CRUD/统计/下载/批量/删除/范围隔离
  - 新增 `tests/test_data_download_export.py`（16 例）：**v2.19.0 数据导出全链路**：`export_service.py` export_mdb/export_csv 单元测试（layer 拆表、字段类型、CSV 头+WKT）；端点 `?format=geojson/shp/gpkg/csv/mdb` 集成测试（聚合特征→导出→二进制断言）；向后兼容原始字节下载；ZIP 壳 400；无特征 410；非法格式 400；404；自定义 SRID PROJCS PRJ 文件写入；detected_crs 非法/无 PROJCS 兜底；pyproj fallback 异常路径
  - 新增 `tests/test_standard_v2_routes.py`（17 例）：标准/层组/表/字段/字典/字典项/字段-字典关联/版本/发布/导入/导出/模板
  - **`data_standard_v2.py` 14% → 32%**（达标，已拆包为 `data_standard_v2/` 包 10 子模块）
- **P0 服务层测试成果**（消灭 0% 文件）:
  - 新增 `tests/test_rule_engine.py`（34 例）：**`rule_engine.py` 0% → 100%**
  - 新增 `tests/test_spatial_query.py`（14 例）：**`spatial_query.py` 0% → 100%**
  - 新增 `tests/test_ws_guard.py`（3 例）：**`ws.py` 14% → 25%**
  - 新增 `tests/test_ws_flow.py`（14 例，含 P2a 新增 8 例）：**`ws.py` 25% → 96%**
  - 新增 `tests/test_schemas_ws.py`（5 例）：**`schemas/ws.py` 0% → 100%**
- **P2a ws.py 覆盖提升**（2026-09-04）:
  - P2a 新增 8 例覆盖 auth exception、Redis unavailable、batch_job_id channel、no-channels error、heartbeat、message forwarding exception、WebSocketDisconnect、generic exception → **`ws.py` 80% → 96%**
- **P2b data.py upload 路由级测试**（2026-09-04）:
  - 新增 `tests/test_data_upload.py`（9 例）：CSV/GeoJSON/ZIP 上传 + 去重 + auto_create_standard + layer_group 关联 → **`data.py` 18% → 25%**
- **P2c data.py upload 沙箱测试 + ws.py 异常分支**（2026-09-04）:
  - 新增 `tests/test_data_upload_p2c.py`（18 例）：真实 GeoJSON 上传 + geometry mismatch + standard table auto-pick + upload check + chunk upload + overwrite-by-name + archive with vector + reindex → **`data.py` 25% → 34%**
  - `ws.py` 新增 2 例覆盖 exceptions 异常分支 → **100%**（Redis loop exception + heartbeat send exception）
- **P3 静默吞异常审计**（2026-09-04）:
  - 审计 `app/` 70 处 `except Exception`，其中 **11 处静默吞异常（无日志）全部修复**：
    - `core/cache.py` GET/SET、`core/deps.py` apikey 缓存、`attribute_processing.py` CSV、`crs_transform.py` CRS 变换、`feature_store.py` CRS+修复、`spatial.py` CRS 注入、`ws.py` 消息转发、`topology_checker.py` irregularity、`transform_engine.py` proj4+变换
  - 新增 logger 到 5 个文件（deps/attribute_processing/crs_transform/feature_store/transform_engine）
  - 其余 59 处为 SAFE（已重抛/已记录）或 ACCEPTABLE（优雅降级）
- **P4 纯 Python 0%/低覆盖文件全清**（2026-09-07）:
  - 新增 `tests/test_obj_parser.py`（12 例）：**`obj_parser.py` 0% → 100%**（OBJ 解析/三种面索引格式/默认对象/畸形行跳过/字节输入/tempfile 清理）
  - 新增 `tests/test_scoring.py`（16 例）：**`quality/scoring.py` 0% → 100%**（clamp/加权总分/时限 30 天上限/自定义权重/颜色阈值）
  - 新增 `tests/test_geometry_repair.py`（22 例）：**`geometry_repair.py` 18% → 100%**（4 策略/自相交自动修复/纳秒异常回退/环方向右手定则/要素计数比对）
  - 新增 `tests/test_progress_tracker.py`（12 例）：**`progress_tracker.py` 19% → 94%**（仅剩 19-22 真实 Redis 连接行；发布/批量/订阅/GeneratorExit 清理全覆盖）
  - 关键洞察：shapely 2.x 中 `buffer(0)` 极少失败、`make_valid` 在 NaN 坐标时抛 GEOSException → 89-99/105-107 防御分支用 monkeypatch 确定性覆盖；`_ring_is_clockwise` 用 (Δx)(y2+y1) 面积公式，>0 即顺时针
- **P5 topology_checker 100% + T2 死代码 bug 修复**（2026-09-07）:
  - 新增 `tests/test_topology_checker.py`（55 例）：**`topology_checker.py` 13% → 100%**（340 语句全清）
  - 覆盖：米制量算策略（native/planar/UTM 选带/南半球/越界钳制）、`_make_result`/`_summarize`、T1/T2/T3/T4/T5/T6/T7/T8/T9/T10/T11/T12/T13 全规则、单规则 SQL 异常回滚、图层圈定（`=ANY` 原生 SQL + `IN` ORM）、config=None 默认、无效 geometry_type
  - **修复死代码 bug（T2）**：`run_topology_checks` 自相交循环守卫 `if gtype not in active_rules and gtype not in [r.rule_code for r in results]` 恒为 True（gtype 为 "line"/"polygon"，永不在规则名集合中）→ T2 查询从不执行、自相交规则为死代码。修正为 `if gtype not in check_models: continue`。TDD 红用例先行证明，修复后 0→2 条结果。
  - 测试技巧：`FakeTopologyDB` 按 SQL 子串分流（handler 顺序即优先级），SQLAlchemy ORM 查询用 `literal_binds` 编译成 SQL 字符串后匹配；T8/T9 需将 `ST_Intersection`/`ST_UNION` matcher 置于 `ST_Length`/`ST_IsEmpty` 之前（避免子串误匹配）；凹面凸度样例用 `CROSS_WKT`（shapely 实算凸度 0.28 < 0.3）
- **P5b 服务/workers 全覆盖（v2.16.0 持续推进，2026-09-07）**:
  - 新增 `tests/test_script_generation.py`（45 例）：**`script_generation.py` 20% → 99%**（239/265 为结构性不可达防御分支留档）；**修复潜在 bug**：`generate_fiona_script` 带 `select_fields` 时 `NameError: json is not defined`（模块缺 `import json`）
  - 新增 `tests/test_quality_exporter.py`（23 例）：**`quality_exporter.py` 8% → 100%**（GeoJSON 三出口/汇总 Feature/samples 去重/几何批量查询/异常降级/Excel 导出；`json.loads` 对 bytes 抛 `UnicodeDecodeError` 不在捕获集 — 用 `json.loads(123)` 走 TypeError 分支）
  - 新增 `tests/test_batch_tasks.py`（7 例）：**`batch_tasks.py` 15% → 100%**（`orchestrate_batch` 全分支：SUCCESS/PARTIAL/FAILED/找不到批量/apply_async 或 get 异常；注意 `create_engine`/`sessionmaker`/`publish_batch_update` 均为函数内导入 → 必须 patch `sqlalchemy.*` 与 `progress_tracker.*` 而非模块属性）
  - 新增 `tests/test_tasks_worker.py`（27 例）：**`tasks.py` 25% → 100%**（5 步流水线/6 分支/低置信度警告/dbf 缺失/GeoJSON 修复/沙箱失败/重试语义——`self.request.retries` 用 `push_request(retries=99)` 控制；`self.retry` 用 `patch.object(T.run_conversion, 'retry')` stub）
  - 新增 `tests/test_beat_schedule.py`（11 例）：**`celery_app.py` 74% → 100%**（kombu/RedisBackend 协议补丁桩函数验证 + 导入失败 except 分支用 `sys.modules[..]=None` 触发）；**`beat_schedule.py` 32% → 95%**（43-44 内层 except 仅 stat 竞态可达留档）；**修复真实 bug**：Python 3.12 `Path.is_dir()` 对 EACCES 重新抛出（仅吞 ENOENT/ENOTDIR/EBADF），原代码未包裹 `is_dir()`，权限受限目录会让整个清理任务崩溃 → 外层加 `try/except OSError: continue`
  - 全量回归：**434 passed**（4 例 pyodbc 预存失败除外）
- **P5c quality.py 路由级 99% + 覆盖率测量修正**（v2.16.0 持续推进，2026-09-07）:
  - 📐 **测量修正**：新增 `.coveragerc`（`[run] concurrency = greenlet,thread`）。实测：同一 handler 测试下 quality.py 18%→28%，证明此前 async 路由 handler 未被统计。修正后 **data.py 34%→59%（真值）**、全量 57%→68%。
  - 新增 `tests/test_quality_routes.py`（27 例）：**`quality.py` 27% → 99%**（269 语句，仅剩 108-109 S4 放行/245 上传空文件名/840 批量删空 3 行：前者需 LIST 分区造数，后两者 FastAPI 请求门禁不可达——已留档）
  - 覆盖端点：`POST /quality/check`（有要素 200/越权 404/无要素 400 拒检/topology_config 自定义容差解析）、`POST /quality/check/upload`（新 orchestrator 200、orchestrator 异常回退→标准不可达 404、旧 check_quality 回退 200、坏 topology JSON 400）、详情（违规样本派生/严重度分布/违规总数）、HTML 即时生成（全零分红色阈值/pass 行跳过/404）、JSON 下载（附件头/无路径 404）、列表（倒序/分页）、导出（GeoJSON/Excel/404）、单删/batch-delete/delete-all
  - 测试要点：vcs_test 真实 PostGIS + dependency_overrides；打桩隔离服务（`run_quality_check`/`export_violations_geojson`/`export_violations_excel` 用 AsyncMock/桩函数）；测试 Key 必须 `commit`（`authenticated` 依赖走独立会话，仅 flush 不可见）
  - 全量回归：**530 passed**（4 例 pyodbc 预存失败除外）
- **P5d batch.py 路由级 100%**（v2.16.0 持续推进，2026-09-07）:
  - 新增 `tests/test_batch_routes.py`（17 例）：**`batch.py` 30% → 100%**（106 语句全清）
  - 覆盖端点：`POST /batch/convert`（Redis 可用→`apply_async` 并行提交 202、Redis 不可用→降级进程内 `apply` + `max_retries` 保存/恢复、apply 抛异常内部捕获、超 `max_batch_size` 413）、详情（200/404/越权 404）、列表（倒序/分页）、取消（RUNNING+celery_task_id→`revoke`；非 RUNNING/无 task_id→不 revoke；404）、子任务列表（有/空）、`_redis_available` 探测（ping True/Exception/import 失败 → False）
  - 测试要点：批次提交写真实磁盘需 `patch.object(settings, "upload_dir", tmp)`；`Task.apply(args=…)` 中 `args` 是**关键字参数**（mock 记录在 `.kwargs["args"]`）;`_make_child` 必须先 `flush()` 生成 batch.id 再挂 `batch_job_id`（否则 FK= None）;`db_session` 在 `expire_on_commit=False` 下缓存旧状态,路由用独立会话写入后须 `refresh` 再断言;`revoke` 打桩 `patch("app.workers.celery_app.celery_app", new=MagicMock())`
  - 全量回归：**547 passed**（4 例 pyodbc 预存失败除外），总覆盖率 68.24% → 68.99%
- **P5e spatial.py 路由级 100%**（v2.16.0 持续推进，2026-09-07）:
  - 新增 `tests/test_spatial_routes.py`（30 例）：**`spatial.py` 29% → 100%**（188 语句全清）
  - 覆盖端点：`GET /spatial/crs/catalog`（8 大分组真实 spatial_ref_sys 驱动、zone 命名、自定义坐标系 resolve 异常回退静态 srid、**兜底"其他"分类可达性**——临时改脏 32653 srtext 触发、finally 恢复）、`POST /spatial/{file_id}/transform`（成功→TransformResult 落库 + output_data 注入 EPSG/VCS-CUSTOM crs member、自定义 SRID → `urn:ogc:def:crs:VCS-CUSTOM::{srid}`、源 CRS 解析失败→警告降级、无要素→400 拒检 + failed>0 明细、404、越权 404、虚拟管理密钥→api_key_id 置 NULL）、`_inject_crs_member` 单元（空 payload/已有 std crs 直通/坏 URN 矫正/坏 JSON 返回原值）、列表（total/无 output_data/分页/范围隔离）、下载（geojson 附件头 + 文件名、shp/gpkg 经 `_run_sync` → 桩同步函数验证 target_wkt 来自 spatial_ref_sys、416 自定义无行→wkt None 跳过 pyproj、未知 srid pyproj 异常→wkt None、坏 format 400、404/410）、删除（204 后 count=0、404、越权 404）、修复接口（repair ok/404）
  - **修复静默失效（第 3 处）**：`_categorize` 的 WGS UTM 分支用了 `[NS]` 正则匹配**已小写化**的 srtext → 永不匹配 → 全部 UTM 条目落入"其他"组、`WGS 84 UTM` 分组恒空。修正为 `[ns]` + 显示 `ns.upper()`。TDD 红用例（断言 32651 → `WGS 84 UTM` 组）先行暴露，修复后 8 大分组全部正确。
  - 覆盖缺口留档：**无常留缺口**（本轮实现 100%，含防御兜底可达性证明）
  - 测试要点：`_categorize` 为嵌套函数不可直接 import → 靠真实 `spatial_ref_sys` 查询驱动全部分支；catalog 未打桩时会真实 `resolve_transform_crs` **把内置自定义 SRID 注册入库** → 断言自定义 wkt 时须动态挑一个不在库的 `>=910000` srid；下载端点的 shp/gpkg 桩在 `_run_sync` executor 内被**同步调用** → 用 `MagicMock(return_value=bytes)` 而非 AsyncMock；删除断言用 `select(func.count())` 直读库避开 identity map 缓存
  - 全量回归：**577 passed**（4 例 pyodbc 预存失败除外），总覆盖率 68.99% → **70.92%**
- **P5f source_data.py 路由级 100%**（v2.16.0 持续推进，2026-09-07）:
  - 新增 `tests/test_source_data_routes.py`（40 例）：**`source_data.py` 27% → 100%**（144 语句全清）
  - 覆盖端点：源数据表（列表含 field_count 子查询/详情 fetch 级联目录/创建含字段/更新逐字段/删除级联字段/404/越权 404 隔离/add-field 201/404）、字典（列表含 item_count/搜索/详情 items 展开/越权 403 隔离/创建含条目/批量 add-items/删除级联条目/404）、字段-字典关联（link 201/field 404、v2 遮蔽事实固化、legacy 函数级直达、404）
  - **修复静默失效（第 4 处，半迁移遗留）**：字典端点仍引用旧模型 `SourceDictionary`。探针实测 `selectinload(SourceDictionary.items)` 直报 `AttributeError`（模型无 items 关系）、list item_count 恒 0（`DictionaryItem.dict_id` 外键指向新表 `standard_dictionaries`）。按模型注释"迁移后将被 StandardDictionary 替代"迁移字典端点到 `StandardDictionary`（schema 名保留 `SourceDictionary*`）
  - **配套静默失效（TDD 红用例驱动发现，3 处一并修复）**：① `FieldDictLink.dict_id` 外键仍指向旧表 `source_dictionaries` → 修复后字典在 `standard_dictionaries`，关联必外键违约 → 改指 `standard_dictionaries.id`；② `add_source_field` 返回未加载 `dict_links` 的对象 → 201 响应序列化 `MissingGreenlet` → 改为 commit 后重查 + `selectinload`（与 get/update 一致）；③ `DELETE /field-dict-links/{link_id}` 被 data_standard_v2 同名路由**遮蔽**（v2 先注册）→ HTTP 层永远到达不了旧 `unlink_field_dict`，该 legacy 实现以**函数级直调**测试覆盖其行，并用 mask 测试固化"HTTP 打到 v2 语义"的 API 事实（勿把 v2 表现为旧删除）
  - 测试要点：`test_db` 为 session 级共享库 → 所有 count/列表断言**不得全表计数**，必须按本测试自建 id 过滤（`where(table_id/dict_id/id …)`），列表断言 `>=` 而非 `==`；被遮蔽路由无法以 HTTP 达 → 直接 `await unlink_field_dict(link_id, db, api_key)` 覆盖行；共用 helper `_create_key/_make_table/_make_field/_make_dict/_make_item/_link_setup`
  - 覆盖缺口留档：**无常留缺口**（本轮实现 100%）
  - 全量回归：**617 passed**（4 例 pyodbc 预存失败除外），总覆盖率 **71.95%**
- **P5g data_standards.py 路由级 100%**（v2.16.0 持续推进，2026-09-07）:
  - 新增 `tests/test_data_standards_routes.py`（18 例）：**`data_standards.py` 30% → 100%**（93 语句全清）
  - 覆盖端点：模板下载（真实模板 200 + content-type、缺失 404）、Excel 导入（真实 xlsx 端到端 6 表落库：DataStandard/Sheet/Field + SourceTable/SourceField + StandardVersion 且 file_data=原始字节、name 缺省取文件名 stem/description 可空、非 .xlsx 400、`../`/`/`/`\` 三种非法文件名 400、>10MB 413、解析异常 400、无有效字段 400）、列表（分页 page_size 门禁 422、他人数据不可见、brief 字段含 sheet_count/file_name）、详情（sheet 字段顺序、404、越权与不存在同 404 不漏信息）、删除（标准/sheet/field/version 级联清零 + SourceTable 保留且 FK 置空、404、越权不删）
  - **修复静默失效（第 5 处）**：`POST /data-standards/import` 以 `DataStandard(..., sheet_count=...)` 构造，但模型无 `sheet_count` 列 → **每次导入必 500 `TypeError`**，列表 `sheet_count` 亦恒 0。探针实测逮住，模型补 `sheet_count: Integer NOT NULL DEFAULT 0` 列（生产库需 `ALTER TABLE data_standards ADD COLUMN sheet_count integer NOT NULL DEFAULT 0`；vcs_test 由 create_all 重建已生效）
  - 测试要点：真实 openpyxl 生成内存 xlsx 走完整解析链路（服务层不 mock，仅解析失败/空结果两分支 patch）；`settings.upload_dir` patch 到临时目录避免污染提交目录；`StandardVersion.file_data` 为 LargeBinary，断言原字节等价
  - 覆盖缺口留档：**无常留缺口**（本轮实现 100%）
  - 全量回归：**635 passed**（4 例 pyodbc 预存失败除外），总覆盖率 71.95% → **73.05%**
- **P5h import_export.py 路由级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_import_export_routes.py`（37 例）：**`import_export.py` 38% → 100%**（448 语句全清）
  - 覆盖端点：JSON 导入（update 按 name/按 code 匹配 + 版本快照落库、upsert 缺省即 create、404、bad mode 400、children 递归建子层组/递归计数、字典 items 含 parent_code）、Excel 导入（v2 四 Sheet create 含子层组挂载 + update + 409；edge 模板命中 fallback「未分组」组/表类型越界转 attribute/字段表编码缺失跳过/scope=global/字典项空名回退码；旧扁平 create/update/409；bad mode/bad ext/.xls/无有效 sheet 三 400）、CSV 导入（多文件 + 非 CSV 跳过警告、update 版本流、409、空文件警告、纯跳过无表）、模板下载（excel xlsx 魔数、csv zip 魔数 + 归档内文件）、导出（excel 全树含空字典项 sheet 分支、404、越权同 404、不支持的 format 400）、auto-create-table（ok 默认层组名取文件名 stem + field_count、文件不存在 404、无 field_schema 400、服务层 ValueError→400、越权 404）、实现层直调（`_import_version_snapshot` 的 extra_diff 合并分支、`_content_disposition` 纯中文/ASCII 两分支，经 `AuthContext` 而非裸 ApiKey——route 依赖实际返回 AuthContext 而源码标注 ApiKey，直调须按运行时语义构造）
  - **静默失效：本轮未探出新坏点**（探针覆盖 v2 模板导入/更新、扁平、CSV、模板、导出、auto-create 全部 200/400 预期语义）
  - 测试要点：v2 excel 用真实 openpyxl 四 Sheet（标准信息/层组定义/数据表定义/字段定义/字典定义）驱动 `parse_standard_excel_v2`，edge 模板一次性命中 `_create_v2_children` 全部边角；`.xls`/坏扩展名/空模板在文件体校验与解析阶段拦截，未触达 DB 写入
  - 覆盖缺口留档：**无常留缺口**（本轮实现 100%）
  - 全量回归：**676 passed**（4 例 pyodbc 预存失败除外），总覆盖率 73.76% → **77.27%**
- **P5i 服务级 100% ×2**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_rule_generator.py`（8 例）：**`rule_generator.py` 18% → 100%**（34 语句全清）
  - 新增 `tests/test_attribute_checker.py`（13 例）：**`attribute_checker.py` 28% → 100%**（185 语句全清）
  - 测试要点：`load_standard_tables` 顶层过滤直接杜绝父子层组下的**重复收集**（`parent_id is not None → continue`）；checker 仅接收 `StandardTable`，从 `data_standard` 模型取标准名
  - 全量回归：**697 passed**，总覆盖率 **78.83%**
- **P5j dwg_converter.py 服务级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_dwg_converter.py`（12 例）：**`dwg_converter.py` 0% → 100%**（44 语句全清）
  - 测试要点：DWG→DXF 需 LibreDWG 32 位 DLL（`cairosvg` 亦 32 位冲突），monkeypatch `subprocess.run`/`tempfile` 全分支：找不到 exe/dwg2dxf 命令失败（stderr 提取）/成功/非 0 退出码
  - 全量回归：**709 passed**，总覆盖率 **79.27%**
- **P5k data.py 82%：chunk/merge/cancel + CRUD/quality 端点**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_data_chunk_edges.py`（26 例，P5k-1）：chunk over-max(501→400)/oversize(4MB→413)/meta 追加去重/status ready（merge 默认 200 非 201）；merge 不完整(400)/缺切片(400)/超 archive 或单文件限制(413)/带 standard_id(200+名称回填)/agg 失败 SAVEPOINT 隔离(200+warning)/子任务失败 warning(200)/rmtree 异常吞掉(200)；cancel 清理 + 未知会话；upload：读循环 413/single 413/auto_create 已有标准/无几何/zip fallback 第一表/解压 errors 不中断/MDB 全链路 + 5 个 MDB 边角（extract 异常 400、子文件 standard 回填、无子任务、父 CRS 回填、子任务失败 warning）
  - 新增 `tests/test_data_crud_quality.py`（34 例，P5k-2）：列表过滤（data_type/file_format/search/geometry_type 计数列命中 + 字符串兜底/standard none+指定/日期范围）；features 真实分区行/structure 404+多图层 fields 清空/download 404；PATCH standard_id 回查名称 + table_id + 名称直写（**1687 standard_id 清空分支删除**：FK `data_standards` 约束下空串写库必违，属不可达分支，留档）；batch-classify 带 standard/delete 404+递归删子/batch-delete 多条/cleanup 旧档案/reindex agg 失败不阻断；quality 端点 8 例（404/400×5/happy 落库+JSON 写盘/表单 standard 回退）；upload 残余：xlsx 真实 schema 解析+损坏兜底/detect 异常兜底/清理 rmtree+os.remove 异常吞/auto_create GEOM_MAP+无几何；**v2.19.6 +2：cleanup-archives 只删壳保留子数据（真实上传 zip→回拨 created_at→cleanup→壳删/子记录保留 source_archive 置空/分区 count=1）+ 显式 DELETE 仍递归删分区回归**
  - **修复生产 bug（from/to 日期过滤）**：`list_files` 原 `created_at >= "YYYY-MM-DD"` 字符串比较 → PG 报 `timestamp with time zone >= character varying`，日期过滤从上线起从未生效（恰好是 1391/1393 行未覆盖的根因）。新增 `_parse_date_bound`（`fromisoformat` + UTC aware，to 侧钳至 23:59:59）修复，TDD 红用例先行
  - 测试要点：`merge` 端点无 status_code → **200**（非 201）；CSV 检测 geometry_type="table" → 452-468 一致性校验 400，fallback 484 只能走 **zip 主记录**（is_archive=True、geometry_type=None 跳过校验）；子任务 loader 用 `AsyncSessionLocal` → patch `app.api.v1.data.AsyncSessionLocal` 到 `_TestSession`；**reindex 与 loader 真实执行存在事件循环竞争（P2c 已记录）→ 一律 mock `_load_all_features_to_spatial_async`，本文件仅测 agg 失败分支**；独立会话写入后本会话 identity-map 缓存旧对象 → 断言用 `populate_existing=True`/`refresh`；测试写 UTF-8 只用 Edit/Write 工具（PowerShell `WriteAllText` 曾致中文乱码损坏整文件）
  - 全量回归：**767 passed**，总覆盖率 **82.21%**；`data.py` 59%→72%（P5k-1）→**82%**（P5k-2，287→181 miss）
- **P5k-3 data.py 82%→98%：GDB/SHP/MDB 加载链路 + archive/merge 全分支**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_data_p5k3.py`（27 例）：upload ZIP 全类型（GDB 组 + 伴生无主 SHP detail + tables + CSV/GeoJSON gather + 201）；upload_file 定位修正（647-768 属 **upload_file**、1143-1312 属 **merge_chunks**，两分支语义不同：merge 对无主 SHP/ignored 不产 detail）；GDB loader 函数级（`_load_gdb_layer_async` to_thread 转发 / `_sync_load_gdb_layer` happy+crs_warn+except，`SyncSessionLocal`/`gdb_read_lock` mock）；`_handle_gdb_group` no_layers+均摊大小+happy（**留档 800-804：gdb 组必有 sub_task，else 永不达**）；提取 loader 直调（`_load_extracted_features_async` happy/crs_warn/非矢量/except、`_load_shp_group_features_async` happy/crs_warn/except）；收尾 8 例：upload SHP 主文件（677-704）/非压缩 agg 失败告警/merge SHP+矢量+纯表格（无 sub_tasks 分支）/reindex 403 无权/`_handle_gdb_group` 无 gdb_path 清理兜底/MDB loader 空 features+crs_warn+except/全量 loader crs_warn
  - 边界修复：autouse 限流 fixture 防御式（`app.state.limiter` 仅在 client fixture 触发 lifespan 后存在）；`ApiKey` 无 `owner_id` 列（构造移除）；`gdb_read_lock` 需真 context manager（`SimpleNamespace` 非 CM，happy 挂）；session 级 DB 复用 → 计数断言一律 **before/after 差分**；zip 内伴生纯 SHP（无 .shp 主）归类无 detail
  - 留档（smoke 不可达/死代码）：133-139 `_extract_layer_name` 死代码、274-277 `_parse_table_schema` 最外层 except、331 空文件名 400（422 先拦）、416 auto_create 无检测几何（detected=None 与 field_schema 非空互斥）、800-804/1704 结构性不可达、2129 伴生 dict 永无 .shp、2170-2171 rmtree ignore_errors 兜底
  - 全量回归：**795 passed**，总覆盖率 **84.07%**；`data.py` 82%→**98%**（181→20 miss，全部留档）
- **P5l gdb_processor.py + spatial_loader.py 服务级 100% ×2**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_service_gdb_spatial.py`（71 例）：**`gdb_processor.py` 27% → 100%**（95 语句全清）；**`spatial_loader.py` 33% → 100%**（187 语句全清）——P5k 把 data.py 加载链路压到 98% 时全程 monkeypatch 这两个服务本体，本轮补全安全网真空
  - 测试要点：gdb_read_lock（进程级互斥锁惰性创建 + 异常下锁释放）+ 真 context 管理器；prepare_gdb 两图层 happy/无扩展/unrepairable empty/rmtree 异常吞掉；read_layer_features 生成器 happy + 损坏 GDB ScanError 向上传播；fake fiona 体系（`_FakeFiona/_Src/_Feat/_FeatSrc`）双打开路径（path 实参 / fields 列表含 "geometry"）
  - `_force_2d` Z 降维全形态；`geojson_to_wkt_status` 3D→Multi、`3D MultiPolygon`→Polygon 归一、TIN 降维（shapely 缓冲后须 verify 平铺 4 点环）、自交 make_valid 修复、解析失败 ok=False；`_cad_lines_to_polygons` 哨兵类驱动 invalid ring 修复 + MultiPolygon 部件 expand + 退化 0 面积环丢弃 + 失败保底保留原要素；`extract_features_from_file/_bytes/_geojson/_obj/_dwg` 全管道（真实 GeoJSON bytes / OBJ parser fake / dwg_converter fake）
  - **绑定修倒数例**：`_is_closed_ring` 至少 4 点；open 线输出类型仍是 MultiLineString（非 LineString）；spatial_loader 函数内 `import fiona` → 模块属性不存在，patch 须落在顶层 `fiona.listlayers/open`；`shapely` 2.1 自交环构造不抛异常但面积恒 0（invalid 分支须哨兵类驱动，真实几何走不进）；`normalize_stored_geometry_type('3D MultiPolygon')=='Polygon'`（Multi 归一语义）
  - 全量回归：**866 passed**（+71），总覆盖率 **85.99%**；`gdb_processor.py`/`spatial_loader.py` 双双 **100%**
- **P5m feature_store.py 服务级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_feature_store_p5m.py`（33 例）：**`feature_store.py` 53% → 100%**（198 语句全清）——数据管道三服务（extract/加载 → 服务本体 → 写库）至此全收
  - 测试要点：`_FakeSyncSession`（sync execute 直返）盖 `_ensure_partition_sync`/`drop_partition_sync`/`write_features_batch_sync` 函数体（幂等跳过 + 建分区 + 单引号转义 + ensured set 去重 + flush_every）；`_FakeDB`（async execute 支持 await）盖 async 分区/写入/聚合/查询（CREATE/DETACH/DROP 语句产出断言）；`_write_feature_common` 3D 复合归一 + skipped_no_geometry/skipped_empty；`aggregate_layer_stats` 图层×几何类型聚合（mixed 推断/默认图层/先删后插/冗余计数回写）；`query_features_by_file` feature_index 排序 + GeoJSON 解析 + 分页；`transform_features_by_file` 真实 shapely + monkeypatch transform_engine（happy/无 WKT/empty/None/异常）；`repair_features_by_file` 真实 bowtie buffer(0) + make_valid 兜底
  - 关键绑定：async 函数须 `await db.execute`、sync 函数 `session.execute` 非 await → 分两套 fake（同名冲突曾致递归 await）；`aggregate_layer_stats` 写 `stored_file_layers` 依赖 `stored_files` FK 行 → 冒烟测试须先插 StoredFile + 取真实 api_key
  - 真实 PostGIS 冒烟（vcs_test）：write_features_batch → count/query → aggregate → repair → drop_partition 全链路 SQL 语法成立
  - 全量回归：**899 passed**（+33），总覆盖率 **86.91%**；`feature_store.py` **100%**
- **P5n attribute_processing.py 服务级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_attribute_processing_p5n.py`（25 例）：**`attribute_processing.py` 38% → 100%**（152 语句全清）——数据管道四服务（属性处理 → 加载 → 解析 → 写库）至此全 100%
  - 测试要点：`_process_dbf` 真实 dBase III 二进制夹具（版本/日期/nrec/hdrlen/reclen + 32B 字段描述符 + 定长记录），钉死 C/N/F/I/L/D 各类型解码、删除标记 0x2A 跳过、尾部空格剥离、GBK 中文解码、数字/整型/逻辑/日期各类型；CSV DictReader 编码检测 + 字段过滤 + critical 列追加 + 读取失败 except；GeoJSON 无 features/空 properties 跳过/字段过滤；`_ensure_critical_fields` 大小写不敏感追加（YWBH 命中后不再重复加变体）
  - 关键绑定：`chardet` 是**函数内** import → patch 顶层 `chardet.detect`（模块属性不存在，`AttributeError`）；critical 字段仅在**源存在该列时**出现在输出（YWBH 是追加到 select_fields 而非凭空加列）；DBF 字段终止 94 行 raw[0]=0x0D→break vs **96 行 name 解码空→continue** 是两条不同分支（空名块须用空格填充使 raw[0] 非终止才走 continue）
  - 全量回归：**924 passed**（+25），总覆盖率 **87.84%**；`attribute_processing.py` **100%**
- **P5o crs_transform.py 服务级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_crs_transform_p5o.py`（22 例）：**`crs_transform.py` 52% → 100%**（60 语句全清）
  - 测试要点：resolve_crs 全分支（空串→4326/中文别名/大小写不敏感/EPSG: 前缀/非法 EPSG 回落/纯数字 isdigit/别名表外的纯数字/pyproj to_epsg 命中+proj4/to_epsg None→wkt+proj4/pyproj 异常→GEOGCS 前缀保留 WKT/未知→4326）；extract_crs_from_prj（读文件/空 WKT→None/文件缺失→None/read 抛错→None）；crs_to_srid（None 空串/别名/可解析 EPSG/不可解析回落 4326）；transform_coords（4326→3857 真实 pyproj 投影/pyproj 异常回退原坐标）
  - 关键绑定：`pyproj`/`open` 分别是**函数内 import 与 builtins** → patch 顶层 `pyproj.CRS`/`pyproj.Transformer` + `builtins.open`；别名表含 "4490"/"4326" → 测试纯数字 isdigit 分支须用**不在别名表**的数字（如 4646），否则被别名先截走
  - 全量回归：**946 passed**（+22），总覆盖率 **88.13%**；`crs_transform.py` **100%**
- **P5p core/cache.py 服务级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_cache_p5p.py`（14 例）：**`core/cache.py` 46% → 100%**（53 语句全清）
  - 测试要点：get_redis（ping 成功复用/ping 失败复位→None/aioredis None→None/from_url 成功缓存客户端/from_url 抛错→None）；cached 装饰器全分支（r None 直跑/get 命中反序列化/get 失败→执行→set/set 失败只记日志/happy 无缓存→set）；_make_key（确定性/参数不同键不同/kwargs 顺序不敏感）；**模块重载模拟 `redis.asyncio` ImportError → aioredis=None**
  - **源码缺陷修复**：模块加载期 `aioredis.Redis` 注解求值，redis 缺失时 `AttributeError` 击穿优雅降级 → 新增 `from __future__ import annotations` 延迟注解求值，`import app.core.cache` 在无 redis 环境可正常导入、get_redis 返回 None 走直跑兜底
  - 全量回归：**960 passed**（+14），总覆盖率 **88.40%**；`core/cache.py` **100%**
- **P5q quality_plans.py 路由级 100%**（v2.16.0 持续推进，2026-09-08）:
  - 新增 `tests/test_quality_plans_p5q.py`（14 例）：**`app/api/v1/quality_plans.py` 44% → 100%**（81 语句全清）
  - 覆盖 6 端点全分支：list（空/有数据）；get（正常/404）；create（201 默认值/最小化/409 重名）；update（404/400 空 body/409 重名/局部更新不改变未传字段）；delete（404/正常+验证已删）；default（首次创建含拓扑规则集/二次幂等返回已存在/未重复建行）
  - 关键绑定：真实 vcs_test PostgreSQL integration（conftest test_db + autouse 模块级 + client + api_key_header）；**URL 前缀是 `/api/v1/quality-plans`**（router 挂载在 `/api/v1` 下，裸 `/quality-plans` 会 404）；pytest-asyncio STRICT → 14 例全部 `@pytest.mark.asyncio`
  - 全量回归：**974 passed**（+14），总覆盖率 **88.85%**；`quality_plans.py` **100%**
- **P5r admin.py 路由级收官 100%**（v2.16.0，2026-09-08）:
  - 新增 `tests/test_admin_p5r.py`（28 例）：**`app/api/v1/admin.py` 39% → 100%**（127 语句全清）
  - 覆盖 9 组端点全分支：_verify_admin 403；create key（正常/带 group/group 404）；list（含 group_name）；revoke（404/软删 is_active=False）；activate（404/正常）；regenerate（404/哈希变更）；hard delete（404/真删）；set key group（key 404/group 404/分配/移出置 None）；group create（正常/409 重名）；list groups（子查询 member_count 只计活跃）；get group（404/成员列表）；update group（404/409 排自身重名/name+desc/仅 desc）；delete group（404/组内 key group_id 置 None）
  - 关键绑定：真实 PostGIS integration + `admin_init_header`（X-Admin-Init-Key）；**pytest-asyncio STRICT 28 例全标记**；`expire_on_commit=False` 陷阱——路由更新后复用同一 `db_session` 读到过期对象 → 断言前置 `db_session.expire_all()` 反致 async MissingGreenlet → 改开**独立 `_TestSession()`** 读验证
  - 全量回归：**1002 passed**（+28），总覆盖率 **89.61%**；`admin.py` **100%**
- **P5 系列全目标完成 🎉**（2026-09-08）: 覆盖目标清单（ADR-0001 §"routes with low coverage"）全部清零——服务层 8 服务 100%（rule_generator/attribute_checker/dwg_converter/gdb_processor/spatial_loader/feature_store/attribute_processing/crs_transform）+ core/cache + 路由 quality_plans/admin；`data.py` 98%。全量 **1002 passed / 89.61%**（P5i 起点 697 → P5r 终点 1002，+305 例）
- **P6a quality_checker.py 服务级 100%**（v2.16.0，2026-09-09）:

  - `tests/test_quality_checker_p6a.py`（19 例）：五维评分公式（综合/完整性/唯一性/值域/拓扑）、编排守卫、violation 样本双来源（半结构化 legacy + 结构化或空）、legacy check_quality 兼容。全量 **1021 passed / 90.09%**
- **P6b custom_crs.py 服务级 100%**（v2.16.0，2026-09-09）:

  - `tests/test_custom_crs_p6b.py`（55 例）：srid 解析/EDS 注册/自愈（非法 proj4/坏 WKT 清洗回退）、换盐、真实分区表 extent CRS 计算。全量 **1076 passed / 91.04%**
- **P6c transform_engine.py 服务级 100%**（v2.16.0，2026-09-09）:

  - `tests/test_transform_engine_p6c.py`（31 例）：统一解析（EPSG/URN/proj4/WKT/Custom）、Custom 展开、proj4 回填、变换异常捕获（`_BoomTransformer` 桩）、NULL geometry/超域米制坐标失败路径、真实分区 `transform_db_features`。全量 **1107 passed / 91.75%**
- **P6d format_detection.py 服务级 100%**（v2.16.0，2026-09-09）:

  - `tests/test_format_detection_p6d.py`（99 例）：置信度 0.1/0.6/0.85/0.95 分级、magic 全签名（SHP/DBF/TAB/MID/S57/SXF/Jet 等）、DBF/KML/OBJ/CSV/XLSX schema 解析、类型推断兜底；全部 tmp_path 真实文件。全量 **1206 passed / 93.27%**
- **P6e 小尾巴轮 13 文件服务级 100%**（v2.16.0，2026-09-09）:

  - `tests/test_tail_archive_p6e.py`（30 例）：`archive_extractor.py`（zip 文件名 GBK 回退/安全校验/BOM/目录穿越）+ `esri_shapebin.py`（NULL/截断/ring 过滤/MultiPolygon/`__main__` fake pyodbc runpy 含表异常）
  - `tests/test_tail_data_p6e.py`（27 例）：`data_standard_parser.py`（v1/v2 表头跳过/类型告警/空字典码/load_workbook 异常）+ `structure_analyzer.py` + `standard_dictionary.py`/`standard_field_dict_link.py`/`standard_layer_group.py`
  - `tests/test_tail_misc_p6e.py`（24 例）：`export_service.py`（bool/float schema/null fid）`mdb_processor.py`（fake pyodbc 全流程含系统表/bytes 属性）`progress_tracker.py`（fake redis 成功路径）`script_generation.py` `quality/reporter.py`（低分红/未知 severity 灰）`workers/beat_schedule.py`（stat EACCES 跳过）
  - **死代码留档 2 处**（`# pragma: no cover`）：`script_generation.py` 239-242（`ast.BoolOp` 语法上恒为 and/or，&/| 是 BinOp → 防御分支不可达）；`script_generation.py` 265（`_to_safe` 已拒绝全部 Call/Attribute/Subscript，for-walk 永不到达）；`standard_field_dict_link.py` 重复 `dict_name` 属性删除（第一份死代码，保留 try/except 兜底版）
  - 全量回归：**1296 passed / 94.60%**；本轮 13 目标文件 **100%**（1179 语句全清）
- **P7a core 骨架层全覆盖（v2.17.0，2026-09-10）**:

  - `tests/test_core_tail_p7a.py`（60 例）：`core/temp_cleanup.py`（AsyncSession on_startup/max_age 过期/Error 注入）+ `core/db_utils.py`（restart_sequence/ensure_missing_table）+ `core/config.py`（弱口令拒绝/密钥归一化/异常类型）+ `core/logging.py`（文件轮转异常注入）+ `core/file_utils.py`（非法文件名拒绝/目录穿越）+ `core/rate_limit.py`（自研限流因子/redis 缺失降级）+ `core/security_middleware.py`（URI 白名单/限流探测）+ `core/auth_context.py` + `core/deps.py`（admin key 认证/可重入）+ `db/session.py`（aux 引擎连接参透传）+ `main.py`（静默限流中间件 URI 探测）+ `models/standard_field_dict_link.py` + `standard_category.py` + `stored_file.py` + `data_standard.py`（后端闭包）
  - 修复 2 处：`core/cache.py` 模块重载父属性漂移（`@cache.cached` 与 `AsyncLRUCache` 分属不同模块实例）；redis 缺失时注解降级崩溃（`redis.asyncio` 不存在 → `getattr` 守卫）
  - 全量回归：**1356 passed / 95.39%**

- **P7b data_standard_v2 全覆盖收官（v2.17.0，2026-09-10）**:

  - `tests/test_data_standard_v2_*_p7b.py` 系列（103 例）：layers / versions / tables / dictionaries / quality_rules.py / fields / field_links / standards 全 100%（子模块 11 文件 + quality_rules.py）
  - 3 处顺序稳健修复：`qc_summary` 全局字典断言改 any()；stats 全局计数放宽为 ≥ + 键集合；source-dictionaries 列表过滤 `is_system`（列表中混入全局系统字典）
  - 全量回归：**1432 passed / 99.08%**

- **P7c 全服务工厂 100% 覆盖收官（v2.17.0，2026-09-10）**:

  - `tests/test_convert_p7c.py`（16 例）：convert.py 64%→100%（Redis 降级双路径/取消/硬删除全分支/`_rm_tree` 四态）；落定 2 个通用不可达模式 pragma：multipart 无 filename part 由 FastAPI 422 先行、security_middleware content-length 413 预检先于 handler
  - `tests/test_p7c_tail.py`（7 例）：data.py 98%→100%（`_extract_layer_name` 双分支 / `_populate_table_schema` 坏 xlsx 兜底 / SHP 伴生 .shp） + quality.py 99%→100%（S4：feature_count=0 但分表实际有要素放行）；pragma 化结构性不可达 8 处（331/416/800-804/1704/2170-2171 + quality 245/840）
  - 全量回归 ×2：**1455 passed / 100.00%**（10120 stmts, 0 miss, 114 文件全绿）— **全代码库 100% 覆盖里程碑**
- **策略**: 门槛设低（25%）以先锁定 CI 回归能力，后续分区拉高。

### 全链路冒烟

- **脚本**: `scripts/smoke_convert.py`（健康检查→任务提交→轮询→终态→清理；`--admin-key` / `VCS_SMOKE_ADMIN_KEY` 传参）
- **CI**: `.github/workflows/ci.yml` 新增 `smoke` job（`docker compose up --build --wait` 起 api+worker+postgres+redis 后跑脚本）
- **语义**: 任务进入终态（success/failed/cancelled）即视为链路打通；`pending` 卡死算失败。转换执行成败取决于环境 GDAL/worker 是否可用。

### 安全扫描

- **工具**: Bandit
- **配置**: `-r app/ -ll -x tests/` (仅 app 目录, 低级别以上)
- **输出**: JSON 报告 (`bandit-report.json`)
- **策略**: `continue-on-error: true` (不阻断 CI)

---

## 15. 版本管理

### 当前版本

- **app/main.py**: 2.24.0
- **CONTEXT.md**: v2.24.0
- **deploy_linux.sh**: v2.23.0（Linux 部署面未随 v2.24.0 变更）
- **deploy_windows.bat**: 退役（无版本标题；薄封装转调 `deploy\deploy_offline.bat`，离线部署入口见 `deploy/deploy_offline.bat`）
- **restart.bat**: v2.23.0（标题版本号）

### 版本追踪位置

| 文件 | 版本位置 | 当前格式 |
|------|----------|----------|
| `CONTEXT.md` | 第 4 行 | `> **版本**: vX.Y.Z`（第 5 行为更新日期） |
| `deploy_linux.sh` | 第 27 行 | `VCS vX.Y.Z One-Click Deploy (Linux)` |
| `deploy_windows.bat` | 无版本标题（v2.23.0 起薄封装：`call deploy\deploy_offline.bat`） |
| `restart.bat` | 第 2 行 | `title VCS vX.Y.Z Restart`（第 20 行 echo 同步） |
| `app/main.py` | 第 67 行 | `version="X.Y.Z"` |

### 版本升级清单

当发布新版本时, 需同步更新:

1. [ ] `CONTEXT.md` — 版本号
2. [ ] `BASELINE.md` — 顶部版本号 + 最后更新日期
3. [ ] `deploy_linux.sh` — 标题版本号
4. [ ] `deploy_windows.bat` — 标题版本号
5. [ ] `restart.bat` — 标题版本号
6. [ ] `app/main.py` — FastAPI version 字段
7. [ ] `docs/revision_history.md` — 追加当前版本修订条目
8. [ ] 如有数据库变更 — `alembic` 迁移脚本
9. [ ] 如有新功能 — 更新 BASELINE.md 对应章节

> **v2.22.2 发布执行记录**：上述 1–9 项**全部完成，0 项数据库变更**（纯前端 + 文档）。实际触及：
> ① `CONTEXT.md` ② `BASELINE.md`（顶部/§2/§4.8/§6/§10/§15/§17）③ `deploy_linux.sh` ④ `deploy_windows.bat`
> ⑤ `restart.bat` ⑥ `app/main.py` ⑦ `docs/revision_history.md` + 新建 `docs/revision_v2_22_2.md`
> ⑨ 新增脚本 `scripts/smoke_imp_exp_ui.py` + 新增测试 `tests/test_frontend_imp_exp_ui.py`。

> **v2.23.0 发布执行记录**：新增 `deploy/` Windows 离线一键部署子系统，**0 项数据库变更**。实际触及：
> ① `CONTEXT.md` ② `BASELINE.md`（顶部/§2/§6/§15）③ `deploy_linux.sh`（未改，本版仅 Windows 部署面）④ `deploy_windows.bat`（**退役**为薄封装转调 `deploy\deploy_offline.bat`，移除版本标题）
> ⑤ `restart.bat`（未改）⑥ `app/main.py`（version → 2.23.0）⑦ 新建 `docs/revision_v2_23_0.md` + 新建 `deploy/README.md`。

> **v2.24.0 发布执行记录**：Docker 生产加固，**0 项数据库变更**。实际触及：
> ① `CONTEXT.md` ② `BASELINE.md`（顶部/§2/§5/§6/§15）③ `app/main.py`（version → 2.24.0）
> ④ 新增 `app/services/_odbc.py`（平台感知 MDB/ODBC）⑤ 改 `mdb_processor.py`/`esri_shapebin.py`/`export_service.py`/`dwg_converter.py`
> ⑥ 新增 `docker/entrypoint.sh`、`docker/scripts/gen_{secrets,certs}.sh`、`docker/nginx/*`、`docker-compose.prod.yml`、`Makefile`、`.env.prod.example`、`.gitattributes`
> ⑦ 改 `Dockerfile`（LibreDWG 源码构建 + mdbtools + Flower/Beat 修复）、`docker-compose.yml`（卷路径/beat/去硬编码连接串）、`.dockerignore`
> ⑧ 新增测试 `tests/test_odbc_platform.py`、`tests/test_export_mdb_platform.py`；`tests/test_dwg_converter.py`/`test_export_service_branches.py`/`test_data_download_export.py` 平台无关化
> ⑨ 新建 `docs/revision_v2_24_0.md`。`deploy_linux.sh`/`restart.bat`/`deploy_windows.bat` 未改。

---

## 17. 技术债追踪

> 理由、权衡与拆分路线详见 `docs/adr/ADR-0001-route-split-needs-tests-first.md`。

| 债务 | 描述 | 路线 |
|------|------|------|
| 巨型路由文件 | ~~`api/v1/data_standard_v2.py`(3645 行)`~~ → **已拆包** `api/v1/data_standard_v2/` (10 子模块)；`api/v1/data.py`(2357 行) | ~~先补路由级测试~~ → data_standard_v2 已按子模块拆分并绿测试通过；`data.py` 需先建 GDAL 沙箱夹具再拆 upload 小节 |
| 路由级覆盖空白 | ~~`data_standard_v2.py` 已 32%~~ → **已完成拆包**（子模块各 24%-57%）；~~`data.py` 18%~~ → **98%（P5k 完成：P5c 测量修正后 59% → P5k-1 72% → P5k-2 82% → P5k-3 98%，剩 20 行全部留档）**；~~`quality.py` 27%~~ → **99%（P5c，剩 3 行不可达留档）**；~~`batch.py` 30%~~ → **100%（P5d）**；~~`spatial.py` 29%~~ → **100%（P5e）**；~~`source_data.py` 27%~~ → **100%（P5f）**；~~`data_standards.py` 30%~~ → **100%（P5g）**；~~`import_export.py` 38%~~ → **100%（P5h）**；~~`rule_generator.py` 18%~~ → **100%（P5i）**；~~`attribute_checker.py` 28%~~ → **100%（P5i）**；~~`dwg_converter.py` 0%~~ → **100%（P5j，monkeypatch subprocess/tempfile 全分支，12 例）**；~~`gdb_processor.py` 27%~~ → **100%（P5l）**；~~`spatial_loader.py` 33%~~ → **100%（P5l）**；~~`feature_store.py` 53%~~ → **100%（P5m）**；~~`attribute_processing.py` 38%~~ → **100%（P5n）**；~~`crs_transform.py` 52%~~ → **100%（P5o）**；~~`core/cache.py` 46%~~ → **100%（P5p，含 redis 缺失注解崩溃修复）**；~~`quality_plans.py` 44%~~ → **100%（P5q）**；~~`admin.py` 39%~~ → **100%（P5r）** | ~~data_standard_v2 按子模块拆分~~ → **P1 完成**；P5i 已收服务级：`rule_generator.py`+`attribute_checker.py` 100%；P5j 已收 `dwg_converter.py` 100%；**P5k 全部完成**（P5k-1 chunk/merge/cancel → P5k-2 CRUD/quality → P5k-3 GDB/SHP/MDB 加载链路，共 85 例，`data.py` 98%、20 行全留档，**795 passed / 84.07%**）；**P5l 已收服务本体 ×2**：`gdb_processor.py`+`spatial_loader.py` 双双 **100%**（71 例纯服务直调冷启动、fake fiona + 哨兵几何类全分支，**866 passed / 85.99%**）；**P5m 已收 `feature_store.py` 100%**（33 例，**899 passed / 86.91%**）；**P5n 已收 `attribute_processing.py` 100%**（25 例真实 DBF/CSV/GeoJSON 字节，**924 passed / 87.84%**）；**P5o 已收 `crs_transform.py` 100%**（22 例，**946 passed / 88.13%**）；**P5p 已收 `core/cache.py` 100%**（14 例，**960 passed / 88.40%**）；**P5q 已收 `quality_plans.py` 100%**（14 例真实 PostGIS integration，**974 passed / 88.85%**）；**P5r 已收 `admin.py` 100%**（28 例管理后台全端点，**1002 passed / 89.61%**）——**P5 全目标完成：服务级 8 服务 + core/cache + 路由 2 全 100%**（覆盖目标清单全清零）；`data.py` 拆分决策留待下阶段评估（upload/merge 各 ~1300 行仍是大文件，但已全分支 98%，拆分风险低收益低，暂缓）；**P6 已收 13 服务/模型 100%**（P6a quality_checker → P6b custom_crs → P6c transform_engine → P6d format_detection → P6e 小尾巴轮 archive_extractor/data_standard_parser/esri_shapebin/export_service/mdb_processor/progress_tracker/script_generation/structure_analyzer/reporter/beat_schedule/3 模型，**1296 passed / 94.60%**） |
| 半迁移残留（source_data 字典/关联） | v2.1「迁移后将被 StandardDictionary 替代」遗留 3 处：① 字典端点旧模型（本轮已迁 `StandardDictionary`）；② ~~`FieldDictLink.dict_id` FK 由 `source_dictionaries.id` 改指 `standard_dictionaries.id`~~ → **v2.17.1 已闭合**：新增迁移 `0012_field_dict_links_fk`（实查 pg_constraint 约束名、幂等），空库全链验证 upgrade/downgrade/重入通过；生产库升级执行 `alembic upgrade head` 即可；③ `DELETE /field-dict-links/{link_id}` 同名路由被 v2 遮蔽（历史行为，测试已固化） | 生产库执行 `alembic upgrade head`（将应用 0012）；后续可清理旧 `SourceDictionary` 模型与旧表 `source_dictionaries`（前端 v2 字典流已走 v2 接口） |
| `DataStandard.sheet_count` 列缺失恢复 | P5g 修复前 `POST /data-standards/import` 必 500（模型无该列）；已补 `sheet_count: Integer NOT NULL DEFAULT 0` | 生产库升级时执行 `ALTER TABLE data_standards ADD COLUMN sheet_count integer NOT NULL DEFAULT 0`（vcs_test 由 create_all 重建已生效） |
| `attribute_checker.load_standard_tables` 重复收集 | P5i 修复前：`DataStandard.layer_groups` 关系返回标准**全部**层组，而 `_collect` 又递归 children 从每个层组起步 → 父子层级标准下**每张表被收集两次**，同名检查结果（A2/A3/A7 等）增倍；已加顶层过滤（`parent_id is not None → continue`，child 组由顶层递归覆盖）。`A1` 用 set 去重曾掩盖症状 | 无需数据迁移；P5i 起单表仅产出单份检查记录 |
| 前端 CDN 依赖 | ~~`index.html` 依赖 unpkg Leaflet（css+js）~~ → **v2.17 完成自托管**：Leaflet 1.9.4 dist 落地 `app/frontend/vendor/leaflet/`（css+js+images），index.html 改本地 `/app/vendor/leaflet/` 引用，零 CDN 请求（Playwright 验证 L v1.9.4 + 0 网络错误） | **已完成（v2.17.0）**；如后续新增第三方前端依赖，沿用 vendor/ 自托管先例 |
| 批量 `except Exception` | 全库 100+ 处吞异常 | 区分业务错误码与 5xx 完整 traceback；best-effort 用 `contextlib.suppress` |
| 第三方二进制运行时不入库 | `tools/libredwg/`（~35MB DWGTools DLL/EXE）被 .gitignore 排除，为 DWG 转换运行依赖 | 已建 `tools/README.md` 说明获取来源；部署时按 README vendor 或镜像 apt 安装 `libredwg-utils`（登记） |
| WebSocket 剩余 Redis pubsub 未盖 | ~~`ws.py` 80%~~ → **已完成 100%**（`tests/test_ws_flow.py` 与 `test_ws_guard.py` 用 fake WebSocket + monkeypatch 隔离 DB/Redis，覆盖握手/订阅/转发/WebSocketDisconnect/守卫全分支；全量覆盖报告中 ws.py 110 stmts 0 miss） | **已完成（v2.17 前已闭环）**；剩余真实 Redis pubsub 集成验证依赖环境（可选） |
| secret_key 死配置（v2.20 弃用保留） | **C2**：`settings.secret_key` 全仓零引用（认证走 API Key SHA-256 + admin_init_key，无签名密钥），`"change-me-in-production"` 默认值非风险源；因 Settings `extra_forbidden` 严格模式，删除字段会让带 `SECRET_KEY` 的存量 .env 启动失败，故保留字段仅作兼容 | **v2.20 已标注弃用**（config.py 注释 + `.env.example`/§9 移除）；彻底删除需先清全部存量 .env 的 `SECRET_KEY` 行（或改 extra 策略），再移除字段 |
| 覆盖回跌（v2.18/v2.19 新代码） | P7c（v2.17.0）全库 100% 后新增功能代码未配套全覆盖。**v2.22.1 第四批收口 71 行**：`logging_setup.py`(3)、`cad_parser.py`(10)、`cad_cache.py`(24)、`cad_preview.py`(34) 四个文件全部 **100%**（`test_core_a_p7a.py` TestLoggingSetup +2、`test_cad_parser.py` TestCADParserBranchCoverage +5、新增 `test_cad_cache.py` 13 例、`test_cad_preview.py` TestCADPreviewFallback +12）；**第五批全量清零（47 → 0 miss）**：`export_service.py` 34（新建 `test_export_service_branches.py` 20 例）、cad 路由 4（`test_cad_routes.py` TestCADBranchCoverage +4）、`data.py` 7（`test_data_cad_cache_fail.py` 新建 + `test_data_download_export.py` +2）、`feature_store.py` 2（新建 `test_feature_store_guards.py` 3 例）→ **全库覆盖 100.00%**（11094 stmts / 0 miss，**1587 passed**），P7 技术债闭环（第二次全库 100% 里程碑） | **已完成（v2.22.1 第五批）** |
| ~~**字段-字典绑定不可随模板往返**（v2.22.2 查实）~~ | ① Excel 5 Sheet 确含「字典定义」可导入字典，但绑定零覆盖 —— 模板「字段定义」9 列无字典编码列、`V2FieldDef` 无 dict 字段、`_create_v2_children` 从不创建 `StandardFieldDictLink`（JSON 导入同样）；无批量绑定端点。② 绑定是质检真实数据源：`rule_generator.py:101-121` → `VALUE_IN_SET`，`attribute_checker.py:132-134,377-400` → A5 值域 31010005 —— 导入完不绑定则值域质检整链失效。③ `_common.py:315-322` `_delete_standard_children` 先删全部关联再重建。④ 导出 Excel/JSON 不带关联列 → 往返丢失 | **✅ 已闭环（v2.22.3）**：A 方案「字段定义」增第 10 列「字典编码」（多值分隔 `,，;；`/空白，保序去重，首个编码 `is_primary=True`，未知编码跳过并告警）；JSON 字段对象同构 `dict_codes`；导出补关联列/键；导入侧 `pending_links` 待字典建完再统一建链；旧 9 列模板兼容。③ 保持全删重建（模板为权威），不改精确清理 |
| ~~**导出 CSV 端点缺失 + Query 描述不实**（v2.22.2 查实）~~ | `export_standard` 原本只有 `json` / `excel` 两个分支，`fmt=csv` 落入 `else` 抛 400，而端点 Query 描述写「json/excel/csv」属不实描述；v2.22.2 曾在 UI 显式禁用该按钮 | **✅ 已闭环（v2.22.3）**：实现 `csv` 分支为 **A2-1 ZIP 归档** = `_manifest.json`（仅格式元信息）+ 6 个角色 CSV（`标准信息`/`层组`/`数据表`/`字段`/`字典`/`字典项`），UTF-8 with BOM；层组编码/表编码/字典编码为连接键，行顺序即 ordering；`Content-Disposition` 置 `.zip`，前端扩展名映射 `csv→zip`。Query 描述转为**如实**。**CSV 现已支持字典与绑定无损往返**（初版曾取舍为「不含字典」，因「一表一文件」装不下字典 + 空字典 + 字段类型；改为规范化角色 CSV 后该限制消失） |
| ~~CSV 创建路径 `IntegrityError` 误报~~ | `import_standard_csv` / `import_standard_excel` / `_import_from_v2` 的 create 分支 `except IntegrityError` 一律回 409「标准名称已存在」。实际任何约束冲突（层组名唯一、表名唯一、字典编码唯一）都会被说成名称冲突，错误信息误导排查 | **已修**（`e6d6a6a`）。`_common.py` 新增 11 条约束名 → 中文实体名映射，`_extract_constraint_name()` 从 asyncpg `diag.constraint_name` 取真实约束，`_raise_conflict()` 按约束归因；接入 `standards.py` create/update 与 `import_export.py` 的 JSON/Excel/CSV/v2 四条路径 |
| ~~Excel 空字典丢失~~ | 原记录「`download_excel_template` 用 `if d.items:` 过滤导致空字典丢失」**经取证不成立**：导出侧已有 `else` 分支，空字典往返无损（实测 EMPTY 字典导出→回导 201 完整）。沿此线追出的**真缺陷**在解析侧：`data_standard_parser.py` 解析「字典定义」Sheet 时用 `row[0] is None` 短路，**只有名称、没填编码的字典连同字典项被静默丢弃且无任何告警** | **已修**。改用新增的 `_cell_str()` 统一取值（消掉 `str(None)=="None"` 坑），编码名称皆空才跳过；无编码字典保留实体 + 告警「缺少字典编码…无法参与字段绑定」。与 `_create_v2_children` / `_create_json_children` 既有的 `dict_code or None` + 仅在有编码时注册绑定契约对齐 |
| 前端 CSV 导入收不了归档 ZIP（v2.22.3 用户实测批次发现） | 导入 UI `V2_IE_FMT.csv.accept` 只收散装 CSV（v2.22.2 legacy 语义），而 v2.22.3 导出产出 ZIP 归档；端点也不解压整包 ZIP part → UI 导出的归档无法经 UI 回导 | **已修**（⑯·⑰）：后端 `_expand_zip_blobs` 整包解包（`.zip`/PK 魔数识别、坏包 400、basename 展平、目录与系统垃圾跳过、重名保先者+告警）+ 前端 `accept='.csv,.zip'` 与拖拽提示 |

---

## 16. 维护指南

### 如何更新本文档

1. **新增 API 端点**: 在第 4 节对应模块表格中添加行
2. **新增/修改数据库表**: 在第 3 节对应分类中更新
3. **新增服务模块**: 在第 5 节添加条目
4. **新增脚本**: 在第 6 节添加条目
5. **新增/修改部署脚本**: 在第 11 节更新
6. **修改 Docker 配置**: 在第 12 节更新
7. **新增测试**: 在第 13 节更新
8. **修改 CI 流程**: 在第 14 节更新
9. **修改配置项**: 在第 9 节更新
10. **发版时**: 在 `docs/` 追加 revision_vX_Y.md (见第 10 节模板)
11. **重大功能**: 在 `docs/` 追加 technical_spec + test_manual
12. **更新完成后**: 修改顶部「最后更新」日期

### 变更追踪

每次功能变更时, 在本文档顶部更新:
- 版本号
- 最后更新日期
- 如有重大架构变更, 更新对应章节

---

*本文档由 codebase-onboarding 生成, 作为项目基准线。所有后续变更应同步更新。*
