# VCS v2.8 工作规划（剩余 14 项问题）

## 版本信息

- **规划版本**: v2.8-plan
- **日期**: 2026-08-04
- **范围**: ERROR.txt 剩余 14 项未处理问题 → 收敛为 4 阶段 12 步
- **前置**: v2.7 已完成（18 项修复 + restart.bat 修复 + 文档更新）
- **关联文档**: `docs/revision_v2_7.md`（上轮修订记录）、`ERROR.txt`（问题清单源）

---

## 1. 背景与范围

v2.7 已修复 ERROR.txt 中 18 项。调研（4 个探索代理，2026-08-04）确认剩余 14 项中：
- 部分描述已过时（版本对比后端已返回明细、批量删除已存在）→ 降级为「增强」
- 部分同根因可合并（CRS 问题横跨预览+转换）→ 合并改造
- 部分存在前置依赖（看板趋势依赖死表打通）→ 排定依赖顺序

据此收敛为 **12 个施工步骤、4 个阶段**。

## 2. 剩余问题 → 施工步骤映射

| ERROR.txt 条目 | 步骤 | 类型 |
|----------------|------|------|
| 四-2 PostGIS 入库不稳定 | S1 | 修复 |
| 六-1 预览 CRS 偏移 + 六-2 转换 SRID 错标 | S2 | 修复（合并根因） |
| 四-3 Multipatch/OBJ 3D 解析 | S3 | 功能 |
| 四-5 数据-标准-质检联动 | S4 | 修复 |
| 五-3 报告可视化 + 五-4 HTML 报告 | S5 | 增强 |
| 二-1/7 Excel 模板缺字典 Sheet + 回环破损 | S6 | 修复 |
| 二-3 标准回传更新 | S7 | 功能 |
| 三-1 版本对比明细（描述过时） | S8 | 增强 |
| 二-4 层组多分类 + 移动/复制缺陷 | S9 | 功能+修复（**方案 A 已定**） |
| 七-3 任务看板 | S10 | 功能 |
| 八-4 批量操作 | S11 | 功能 |
| 一-3/4 Nginx 代理 + 一键部署 | S12 | 部署 |

---

## 3. 阶段总览与依赖图

```
阶段 A 数据链路稳定性     阶段 B 质检联动与报告      阶段 C 标准模块闭环      阶段 D 运营与部署
S1 入库稳定性 ────────► S4 质检联动 ────────► S6 Excel回环 ──► S10 任务看板
S2 CRS元数据链路     ──► S5 报告可视化 ───► S7 回传更新 ──► S11 批量操作
S3 Multipatch/OBJ              │            S8 版本对比增强     S12 部署方案
                               └──────────► S9 层组多分类（方案 A）
```

**依赖关系**：
- S6 → S7（Excel 回环修好才能做回传更新）
- S8/S9 同文件（`data_standard_v2.py`）建议顺序做，S9 放最后
- S2 与 S1 同动 `data.py`/`spatial_loader.py` 需顺序
- S10 依赖「死表 `quality_daily_snapshots` 打通写入」

---

## 4. 阶段 A — 数据链路稳定性

### S1 · PostGIS 入库稳定性修复（四-2）｜最高优先级 ✅ 已完成（2026-08-04，见 `docs/revision_v2_8.md`）
**根因**（3 叠加）：
1. `data.py:550` `asyncio.gather` 共享同一 AsyncSession → SQLAlchemy 并发不安全
2. 单巨型事务 + `ST_IsValid` CHECK 约束 → 一条非法几何整体回滚
3. `geojson_to_wkt` 吞异常返回 EMPTY，无日志无感知

**改法**：子任务独立 session｜每 200-500 条分批提交 + 非法几何置 NULL 记录 feature_index｜WKT 转换移入 `to_thread`｜`make_valid` 兜底。

**验收**：大 SHP 含非法几何可入库不整体回滚；并发子任务无 `PendingRollbackError`；日志有逐条失败记录。

> ✅ **S1 验收通过（2026-08-04）**：单文件（bowtie 自相交 → MultiPolygon 修复入库、3D 降维）与 ZIP 并发（3 子文件独立会话、detail 回填与真实入库数一致）均经 Postgres 实测通过；`geojson_to_wkt_status` / `write_features_batch` / `_collect_sub_task_results` 单元验证通过。

### S2 · CRS 元数据链路（六-1 + 六-2 共同根因）✅ 已完成（2026-08-04，见 `docs/revision_v2_8.md`）
**根因**：入库 SRID 硬编码 4326 不感知源 CRS（北京54/西安80 图上百米偏移）；批量转换写回 srid=4326 错标米制结果；`transform_coords` 与批量转换 `always_xy` 行为分裂。

**改法**：`stored_files` 记录 `source_crs`｜预览查询端 `ST_Transform(geometry, 4326)` 出 GeoJSON｜转换写回 SRID 按目标 CRS 映射｜统一 `always_xy=True`。

**验收**：上传 4490/4547 文件预览位置正确；转换后 SRID 与目标 CRS 一致。

> ✅ **S2 验收通过（2026-08-04）**：三表 geometry 列去除 SRID typmod（迁移 0005）；入库按 detected_crs 写源 SRID（4547 投影上传 DB SRID=4547，预览 ST_Transform→4326 输出经纬度正确）；转换端点 4326→4547 后 detected_crs 更新且 SRID=4547、预览转回误差 <0.02°；repair 保留原 SRID；4326 路径回归通过。附带修复 `_transform_coords` tuple 不可变赋值 bug（原转换恒失败）与 `transform_coords` always_xy。

### S3 · Multipatch/OBJ 3D 解析（四-3）✅ 已完成（2026-08-04，见 `docs/revision_v2_8.md`）
**现状**：shapely 不支持 MultiPatch → 几何恒 EMPTY；`_guess_format` 缺 `.obj` 映射；3D 检测与 2D 存储断链。

**改法（方案 B 降维）**：Multipatch 按面语义降维入库（shell 外环 → Polygon + Z 存 `properties.z_values`），`geometry_type` 归一化对齐标准表；补 `.obj` 映射走 `obj_parser.py`。

**验收**：Multipatch SHP 入库几何非空且可预览；OBJ 文件要素数 > 0。

> ✅ **S3 验收通过（2026-08-04）**：`geojson_to_wkt_status` 对 MultiPatch/PolyhedralSurface/TIN 降维为 2D MultiPolygon（单元验证通过，结构对齐 fiona 输出：每 surface 首环为外环）；`_guess_format` 补 `.obj` → OBJ 分支走 `obj_parser.py`（每对象质心 Point + vertex/face 统计属性）；`write_feature` 3D 类型归一化（MultiPatch/PolyhedralSurface→MultiPolygon、TIN→Polygon）保证分表路由正确；非压缩包上传回填实际写入数（原 feature_count 停留检测值，OBJ 时为面数）；真实端到端：OBJ 上传 feature_count=2（实际写入要素数）、geometry_type=Point、预览 2 要素 2D 坐标、DB 2 条几何非空（SRID=4326、object_name 保留）。附注：本机无 GDAL 可写 MultiPatch 样本、PGeo 驱动无法读 .mdb，MultiPatch 采用单元级验证（降维路径与 fiona 输出结构一致）。

---

## 5. 阶段 B — 质检联动与报告

### S4 · 数据-标准-质检联动（四-5）✅ 已完成（2026-08-05，见 `docs/revision_v2_8.md`）
**主断点**：`doQcStored` 标准取自手动下拉不读文件 `standard_id`；Multipatch/OBJ 解析失败 → `feature_count=0` → 400 拦截恰堵死联动；`standard_table_id` 全程未打通。

**改法**：文件切换自动回填 `qcStd2` + 带 `standard_table_id`｜`feature_count=0` 区分「解析失败/真无要素」提示并引导 reindex（端点已存在）｜上传 link 分支同步回填 `standard_table_id`｜列表 page_size 放宽。

**验收**：上传关联标准 → 质检页自动带标准 → 报告 `standard_id` 一致。

> ✅ **S4 验收通过（2026-08-05）**：`run_quality_check`/`run_attribute_checks`/`load_standard_tables` 全链透传 `standard_table_id`（单表模式：A1 跳过图层名缺失误报、逐表检查改用文件全部要素）；上传 link 分支按「几何类型匹配」回填 `standard_table_id`（响应体/秒传/切片合并三路回填）；`quality_check_stored_file` 支持 `feature_count=0` 时查空间分表放行；`page_size` 上限放宽（data 100→200、quality 50→200）；前端 `loadStoredSel` 100→200、文件切换自动回填标准（`applyFileStandard`）、`doQcStored` 携带 `standard_table_id`、feature_count=0 引导提示。端到端 17/17 通过：上传关联层组 → 响应+详情双回填 table_id → 不带标准/表的质检正确继承关联标准执行字段级检查（6 项，A1 pass 不误报缺失图层）→ 几何不匹配上传 400 拦截。S2 回归 4/4、S3 OBJ 回归通过。

### S5 · 质检报告可视化（五-3 + 五-4）✅ 已完成（2026-08-05，见 `docs/revision_v2_8.md`）
**现状**：后端 `violation_samples`/拓扑 `pairs` 已生成但 `QualityReportDetail`（`schemas/quality.py:150-181`）无字段透传。

**改法**：schema 补透传字段｜前端详情面板加五维卡片 + 严重度分布 + 违规样本明细表｜HTML 报告模板化（内联 CSS + SVG + `@media print`，零 CDN）｜可选：违规要素点击跳预览定位。

**验收**：报告详情可见违规样本（feature_index/值）；HTML 报告可打印为 PDF。

> ✅ **S5 验收通过（2026-08-05）**：`QualityReportDetail` 新增 `attr_results`/`topo_results`/`total_violations`/`violation_samples`/`severity_dist`（默认值，兼容存量报告）；`GET /quality/{id}` 改为手工构建详情——从 `constraint_violations`/`topology_violations` 推导违规样本（attr 取 `samples`、topo 取 `feature_indices`，各前 5 条，总量前 100）、汇总违规总数与严重度分布；`GET /quality/{id}/html` 模板化重写——五维 SVG 环形仪表（含综合评分大环）+ 严重度分布条 + 违规样本明细表（要素索引/值着色）+ `@media print` 打印样式，全部内联零 CDN；前端 `toggleQcDetail` 详情面板新增五维评分卡片行 + 严重度分布条 + 违规样本明细表（跨列）。**顺带修复 A4 真实 bug**：pandas 2.x 字符串列 dtype 为 `str` 而非 `object`，原 `if col.dtype == object` 导致空字符串不触发必填违规；统一用 isna+空串掩码统计，且样本从 df 行序映射回 `feature_index`（此前永远取不到）。端到端 28/28 通过（违规报告详情字段齐全、HTML 五维/严重度/样本/print、无违规报告兼容）；S4 回归 17/17、S2 回归 4/4、前端 `node --check` 通过。

---

## 6. 阶段 C — 标准模块闭环

### S6 · Excel 模板补字典 Sheet + 导入导出对齐（二-1/7）✅ 已完成（2026-08-05，见 `docs/revision_v2_8.md`）
**现状**：导出→导入回环已破损——「层组」Sheet 第 3 列导出 `parent_id`(UUID) 解析器按「父级编码」读；「数据表」列错位；「字段」第 1 列导出表名解析器读表编码；无字典 Sheet。

**改法**：导出列与模板/解析器完全对齐 + 补「字典」Sheet + 模板文件同步补字典 sheet。

**验收**：export→import 回环结构相等（含层组层级与字典）。

> ✅ **S6 验收通过（2026-08-05）**：`export_standard` Excel 分支与模板/解析器完全对齐（标准信息/层组定义/数据表定义/字段定义/字典定义五 Sheet；层组父级导出 `group_code` 编码、表/字段第 1 列导出编码、字段补 `field_alias`、「是」布尔）；`download_excel_template` 新增「字典定义」Sheet（PT_TYPE 示例）；`parse_standard_excel_v2` 新增字典 Sheet 解析（`V2DictDef` 加 `description`，按 dict_code 聚合展开字典项）与字段 `field_alias`（中文名列）；`_import_from_v2` 新增字典导入（含空字典保行）+ 填充 `dictionaries_count`。**顺带修复 3 个真实 bug**：① `standard_dictionaries.api_key_id` 外键——超级管理员为虚拟 ID（`00000000-…` 不在 `api_keys` 表），创建/导入字典时 `flush` 恒失败被误报为「名称已存在 409」→ `AuthContext` 新增 `owner_id` 属性（超管返回 None，可安全写入外键列），三处字典插入改用之，并给 IntegrityError 兜底加真实异常日志；② `DataStandardDetail.dictionaries` 字段名与模型属性 `standard_dictionaries` 不匹配导致恒为空 → `validation_alias` 映射；`layer_groups` 关系返回全部层组（含子层组）致树顶层重复渲染、JSON 导出重复 → 详情 schema 顶层只留根层组（`model_validator`）、JSON 导出同理过滤；③ 导出 `Content-Disposition` 文件名含中文触发 latin-1 `UnicodeEncodeError` 500 → RFC 5987 `filename*` 转义（`_content_disposition`）。端到端 40/40 通过（导出五 Sheet 列位、父级编码引用、字段必填/主键/长度/中文名回环、字典含项与空字典回环、层级还原、模板含字典 Sheet）；S4 回归 17/17、S5 回归 28/28。

### S7 · 标准回传更新（二-3）✅ 已完成（2026-08-05，见 `docs/revision_v2_8.md`）
**现状**：三格式导入永远新建，同 name 撞唯一约束 500；无按 code 匹配；标准模型无 `code` 字段。

**改法**：导入端点加 `mode=create|update|upsert`｜更新复用「删子记录按快照重建」（`_delete_standard_children`/`_restore_from_snapshot` 已存在）｜联动版本快照｜回环测试。

**验收**：修改导出文件 → 回传 → 目标标准更新且版本快照+1。

> ✅ **S7 验收通过（2026-08-05）**：`DataStandard` 新增 `code` 列 + `uq_ds_api_code`（api_key_id, code）唯一约束（迁移 `0006_add_standard_code`）；三个导入端点（JSON/Excel/CSV）统一支持 `mode=create|update|upsert`，`_resolve_import_target` 按 code→name 匹配目标（`scope_clause` 数据范围内，超管可匹配任意），update 未命中 404、命中则「删子→重建→版本快照+1」；`_import_version_snapshot` 复用发布链路写 `StandardVersion`（`api_key.owner_id`）并 diff 上版本；快照构建改用 `populate_existing=True` 并移除全部 4 处 `db.expire_all()`（async 下 bulk 删除后 identity map 脏读 + `expire_all` 触发 MissingGreenlet 500 的根因，JSON/Excel 扁平/Excel v2/CSV 四更新路径均修复）；JSON/Excel 导出与模板补「标准编码」；create/update 撞唯一约束 409。端到端 37/37 通过（create 带 code、同名 409、update 按 code 重建 version+1、未命中 404、upsert 新建/命中更新、Excel 导出→改字段定义加 h_grade→回传 5 字段+中文名回环、版本历史 2 条 + diff 含 h_grade、POST 带 code 与 code 冲突 409）。

### S8 · 版本对比明细增强（三-1，增强）✅ 已完成（2026-08-05，见 `docs/revision_v2_8.md`）
**现状**：`_deep_diff`（`data_standard_v2.py:1271-1321`）按列表下标对比产生整段漂移噪音；路径不可读；统计口径为叶子路径数。

**改法**：list 分支按 `table_code`/`field_name` 键对齐消除漂移｜前端层级树折叠渲染 + 中文名｜实体级统计（表数/字段数）｜变更摘要行。

**验收**：新增 1 字段不产生级联 modified 噪音；diff 展示按层组→表→字段分组。

> ✅ **S8 验收通过（2026-08-05）**：`_deep_diff` 列表分支按 `_LIST_KEY_CHAIN` 自然键对齐（layer_groups/children→`group_code`，tables→`table_code`，fields→`field_name`，dictionaries→`dict_name`，items→`item_code`，逐级回退 id），路径可读（`layer_groups/BG/tables/CTRL_PT/fields/elevation`），`_IGNORED_KEYS={"id","ordering"}` 剔除重建 UUID/排序噪音，`_compute_diff` 剔 `standard/version` 噪音；`VersionDiff` 新增 `stats`（`_diff_entity_stats` 最内层实体去重）+ `summary`（`_diff_summary` 中文摘要）+ `labels`（`_collect_diff_labels` 自然键→中文名，快照补 `field_alias`）；前端 `v2ShowDiff` 改为实体级统计徽标 + 变色摘要行 + `_renderDiffTree` 层级折叠树（层组→表→字段/字典→字典项分组，中文名优先、编码灰显，修改显 旧→新，≤6 子节点默认展开），删旧表格渲染。端到端 27/27（publish 基线→update 插入字段/改长度/加字典项/改几何→diff 无下标路径/无级联噪音/路径可读/几何修改正确/standard 噪音剔除/stats+summary 正确；再 update 加表→整表新增路径可读/原表无噪音/stats 对）+ 单元 10/10 + S7 回归 37/37 + Playwright UI 12/12（层级树中文名「基础地理要素/控制点/高程/点类型/全站仪」+ 灰显编码 + 10→12 + 折叠节点）。**顺带修复前端真实 bug**：`_renderDiffTree` 收尾 `return render(root)` 传错容器（root 自身非 children）→ 任何 diff 必抛 `Cannot convert undefined or null to object`（`Object.entries(undefined)`），改 `render(root.children)`。

### S9 · 层组多分类 + 移动/复制修复（二-4）｜**方案 A 已定：表多分类** ✅ 已完成（2026-08-06）
详见第 8 节详细设计（本轮决策核心）。验收见 `docs/revision_v2_8.md` S9 节（e2e 49/49、导入探针 7/7、前端 Playwright 21/21、回归全绿）。

---

## 7. 阶段 D — 运营与部署

### S10 · 任务看板（七-3）
**现状**：`quality_daily_snapshots` 死表无人写入；README 声称 WebSocket 实际未实现（仅 6s 轮询）；无 `/jobs/stats` 聚合。

**改法**：`/jobs/stats` 聚合 + beat 任务写每日快照 + `/quality/trends`｜前端手写 SVG 图（状态占比/近 7 日趋势/格式分布/批量任务卡）。

**验收**：看板 3+ 图表可渲染；快照表有数据。

### S11 · 批量操作（八-4）
**现状**：数据页已有批量删除+批量关联；缺批量下载/批量质检/密钥批量/批量任务 SPA 页。

**改法（成本排序）**：密钥批量吊销（checkbox 列 + `POST /admin/apikeys/batch-revoke`）→ 批量下载（zipfile 流式）→ 批量质检（`POST /quality/batch-check` 走 Celery）→ 批量任务 SPA 页（后端 `batch*` 已齐全）。

**验收**：密钥多选可批量吊销；多文件可打包下载；批量质检返回报告列表。

### S12 · 部署方案（一-3/4）
**现状**：无 nginx 配置；Docker 部署需手工 `cp .env` + `compose up`；无一键脚本。

**改法**：compose 新增 `nginx:alpine`（反代 api:8000、`client_max_body_size 500m`、WS 头兼容、公网仅暴露 80/443）｜`install.bat`/`deploy.sh` 一键部署｜部署文档（端口/备份/升级/证书）。

**验收**：compose up 后经 nginx 访问正常；install 脚本一条命令起全套。

---

## 8. S9 详细设计 — 方案 A（表多分类）已定

### 8.1 决策记录

> **决策**：采用**方案 A（表多分类）**——新增 `standard_layer_group_tables` 多对多关联表，数据表可挂到多个层组分类下；层组保持单父树不变。
> **不采用**：方案 B（层组多父 DAG 化）——改动面 10+ 处、树语义破坏、前端复杂度爆炸，ROI 为负，除非出现明确「层组多父」业务用例再单独立项。
> **前提**：方案 A 必须先修 8.4 节 B5（delete-orphan 误删共享表守卫），否则多分类反而引入数据丢失风险。

### 8.2 现状盘点（决定改造面的事实）

当前严格单父树，全部下游深度耦合：

| 依赖方 | 耦合方式 |
|--------|----------|
| `StandardLayerGroup`（`models/standard_layer_group.py`） | `parent_id` 单值（35-39）+ `children` `delete-orphan` 级联（60-64）+ 唯一约束 `(std,parent_id,group_name/code)`（20-21）+ `depth` 沿父链（71-79）+ `MAX_LAYER_DEPTH=5` |
| `StandardTable`（`models/standard_table.py`） | `layer_id` **单值**外键（26-29）+ 唯一约束 `(layer_id, table_name/code)`（19-20） |
| `list_layers` | `data_standard_v2.py:257-263` 只查 `parent_id IS NULL` 根 + 递归 children |
| `_get_layer_depth` / `_copy_layer_recursive` | `:1389-1402` / `:1405-1459` 沿父链 / 递归复制 |
| `_build_standard_snapshot` / `_restore_from_snapshot` | `:1462-1519` / `:1576-1620` 递归（内嵌 `parent_id`+`children`） |
| `attribute_checker.load_standard_tables` | `:94-143` 递归 `_collect(layer) → layer.tables` |
| Excel/JSON 导出导入 | `:2286-2299` 三层嵌套 / `:1903-1916` group_code 挂载 |
| 前端 `_renderLayerTree`/`_flatLayers`/移动复制弹窗 | `index.html:3123-3335` 递归缩进 + 单选下拉 |

### 8.3 方案 A 优缺点

**优点**
| 项 | 说明 |
|----|------|
| 改动面小 | 层组模型、树查询、深度、级联、快照、导入导出全部不动；只加关联表 + API + 前端标签 |
| 符合 GIS 业务直觉 | 行业惯例是「要素表 → 主分类」；多分类 = 跨专业复用（如「道路中心线」同时挂「道路」「市政管线」） |
| 下游零冲击 | `attribute_checker` 沿主层组遍历不受影响；质检/版本/导出默认只认主归属 |
| 迁移简单 | 一张新表 + FK，现有数据零迁移成本，可灰度上线 |
| 可回退 | 关联表可只读禁用，随时回到纯单父树 |

**缺点**
| 项 | 说明 |
|----|------|
| 语义只解决一半 | 若用户要「层组本身多父」则方案 A 不满足（已明确暂不需要） |
| delete-orphan 冲突 | 必须先改删除语义：非主层组摘挂≠删表；只有删主层组才级联删表 |
| 唯一约束粒度错位 | `(layer_id, table_name)` 是主层组粒度，多挂后前端同一 `table_id` 多处出现，需标注「主」 |
| 复制不补全 | `_copy_layer_recursive` 只复制主表，多挂关系需显式复制 |
| 快照缺口 | 版本快照是树内嵌结构，多挂关系加独立数组或明确不进版本 |

### 8.4 必修 bug（独立于方案，先做）

| # | Bug | 修复 | 成本 |
|---|-----|------|------|
| B1 | 跨标准移动假功能：前端传 `new_standard_id` 被 Pydantic 静默丢弃（schema `LayerGroupMove` 无此字段） | **砍掉前端跨标准移动入口**，改为「复制到其他标准」（`_copy_layer_recursive` 已支持） | 低 |
| B2 | move 防环不完整：只挡自环（`:375`）+ 深度，未挡移动到子孙下成环 | 沿 `new_parent` 链向上，遇 `layer_id` 即 400 | 低 |
| B3 | move 不校验同标准：`new_parent_id` 可指向其他 standard 的层组 | 校验 `new_parent.standard_id == layer.standard_id` | 低 |
| B4 | 树渲染表计数恒 0：前端用 `l.table_count \|\| l.tables_count`（`index.html:3144`），schema 无此字段 | schema 加 `table_count` 或前端改 `tables.length` | 极低 |
| B5 | **删除共享表守卫**（方案 A 前提）：`LayerGroup.tables` 是 `delete-orphan`，多分类后删任一层组会物理删除共享表 | 删除层组前检查其表是否被其他层组引用：有则拒绝（提示先摘挂）或仅摘挂；只有主层组删除才级联删表 | 中 |

### 8.5 风险矩阵

| 风险 | 等级 | 缓解 |
|------|------|------|
| 误删共享表（delete-orphan） | 🔴 高 | 先修 B5 引用计数守卫 |
| S6-S8 基线回归 | 🟠 中 | S9 排在 S6-S8 之后；每子步回归对应测试 |
| 复制/导入不补全多挂关系 | 🟡 中 | 显式复制逻辑 + export→import 回环测试 |
| 版本对比语义缺口 | 🟡 中 | 明确「多挂不进版本快照」或加独立数组，文档说明 |
| 前端多挂展示重复 ID | 🟡 中 | 标注「主」归属 + 去重渲染 |
| 本机无自动化回归环境 | 🟡 中 | 每子步手测 + 测试手册留痕 |

### 8.6 落地步骤（子步顺序）

1. **B1-B4 必修 bug**（无争议，先做，可独立提交）
2. **新增关联表** `standard_layer_group_tables(group_id, table_id, is_primary, ordering)` + Alembic 迁移 + ORM 关系（`StandardTable.multi_layers` / `StandardLayerGroup.multi_tables`）
3. **B5 删除守卫**：`delete_layer` 前置检查共享引用
4. **多挂 API**：`POST /layers/{layer_id}/tables/{table_id}/attach`（挂载，可带 is_primary）/ `DELETE`（摘挂）/ `GET /tables/{table_id}/layers`（查归属）
5. **快照扩展**：`_build_standard_snapshot` 加 `table_multi_links` 独立数组（或明确不进版本并在文档声明）
6. **复制补全**：`_copy_layer_recursive` 复制多挂关系
7. **前端**：层组树多挂标签（⚠ 标注「主/次」）+ 归属面板；`_flatLayers` 不受影响（层组仍单父）
8. **回归**：S6/S7/S8 相关用例 + 新建多分类测试用例（挂载/摘挂/删除守卫/复制/回环）

### 8.7 验收标准

- [ ] 一张表可挂到多个层组；非主层组摘挂不删表
- [ ] 删除仍被其他层组引用的表所属层组 → 400 拒绝并提示
- [ ] 复制层组后多挂关系完整保留
- [ ] move 跨标准被禁止（或仅提示复制）；移动到子孙节点被 400 拦截
- [ ] 层组树显示表计数正确（修复 B4）
- [ ] 现有标准管理 CRUD / 质检 / 版本对比回归通过

---

## 9. 执行顺序与并行性建议

| 阶段 | 步骤 | 建议 |
|------|------|------|
| A | S1→S2→S3 | 顺序（同文件）✅ 已完成 |
| B | S4→S5 | 顺序 |
| C | S6→S7→S8→S9 | **S6 是 S7 前置；S9 必须排 S6-S8 之后** |
| D | S10→S11，S12 独立 | S12 可随时并行 |

单人本地项目建议按 A→B→C→D 顺序推进，每步沿用「修复 + 回归验证 + 文档（revision/test_manual）」惯例。并行仅在 S9/S10/S12 后端独立模块间可行。

> **维护说明**：本规划为 v2.8 目标；每完成一步在 `VCS_项目修订全纪录.md` 登记 Phase 16 时间线并更新本文件状态。
