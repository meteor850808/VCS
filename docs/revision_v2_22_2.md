# v2.22.2 修订记录

> **版本**: v2.22.2
> **日期**: 2026-09-29
> **批次**: 数据标准 — 导入/导出/模板单框三段重构 + mode 暴露 + 字典绑定链路缺陷定位
> **契约守卫**: `tests/test_frontend_imp_exp_ui.py`（30 例）+ `scripts/smoke_imp_exp_ui.py`（28 例 Playwright 真实 DOM）· 全量回归 **1617 passed**，覆盖率 **100.00%** 保持

---

## 零、本批次的两个前置发现（功能点确认，未修）

本批次起点是三个功能点核查，前两个查实为**真实缺陷**，本批次只修 UI 层（见下），链路缺陷留档待议。

### 发现 1：字典能导入，但「绑定」不能导入

- **支持导入** ✅：Excel 模板为 5 Sheet，第 5 个即「字典定义」（`import_export.py:829-832`），解析 `data_standard_parser.py:337-361`，落库 `import_export.py:590-612`。
- **绑定零覆盖** ❌：模板「字段定义」9 列无字典编码列；`V2FieldDef`（`data_standard_parser.py:177-190`）无 dict 字段；`_create_v2_children`（`import_export.py:498-615`）**从未创建 `StandardFieldDictLink`**。JSON 导入同样（`_create_json_children:178-214`）；CSV 导入无字典概念。
- **唯一入口是逐字段手工点**：字段编辑弹窗「📖 字典关联」（`index.html:1501-1508` + `app.js:3613-3707`）或 `POST /api/v1/fields/{field_id}/dict-links`（`field_links.py:54`）。**无批量绑定端点**。
- **危害链（已验证）**：绑定是质检的真实数据源 ——
  - `rule_generator.py:101-121`：dict_links → `VALUE_IN_SET` 自动规则；
  - `attribute_checker.py:132-134, 377-400`：A5 属性值域控制 `31010005`。
  即「导入完不绑定 → 自动生成规则拿不到值域 → 值域质检整条链路失效」。
- **附带丢绑定**：
  - `_common.py:315-322` `_delete_standard_children` 先删全部 `standard_field_dict_links` 再重建 → `mode=update/upsert` 导入后已手工绑定**全部消失**（本批次已把该路径暴露到 UI，见变更三，此风险由「不可达」变为「可达」）；
  - 导出 Excel/JSON 不带关联列（`import_export.py:1001-1030` / `966-974`）→ 导入→导出→再导入往返丢失。

> **待决**：是否在「字段定义」Sheet 增加「字典编码」列 + 导出补关联列，使绑定可随模板往返。

### 发现 2：导出 CSV 按钮永远 400

`export_standard`（`import_export.py:902-987`）只有 `json` / `excel` 两个分支，`csv` 落入 `else` 抛 400；而 Query 描述却写「导出格式: json/excel/csv」，属**不实描述**。原 UI 的「📥 CSV」按钮点了必然报错。
本批次按「后端零改动」约束，在 UI 上显式禁用并说明（见变更二），后端能力缺口留档。

### 发现 3：无「数据标准 → 知识图谱」功能

全仓 `知识图谱|knowledge.?graph|neo4j|图谱` → **0 命中**。GitHub 调研结论与分档建议见 §四。

---

## 一、设计：6 个切换按钮 → 1 组选择器 + 3 个动作段

### 变更前

```
[⬆ 导入] [⬇ 导出] [📄 模板]        ← 3 主 Tab
  [JSON] [Excel] [CSV]             ← 3 子 Tab
  + Excel dropzone + CSV dropzone  ← 2 个
```
6 个切换按钮、2 个 dropzone、纵向 5 段式堆叠。

### 变更后

```
格式  [JSON] [Excel] [CSV]        ← 唯一选择器，三段共用
┌ ① 导入 ┐  标准名称 + 模式 + 单一 dropzone
┌ ② 导出 ┐  当前选中 + 单一按钮（文案跟随格式）
┌ ③ 模板 ┐  单一按钮（文案跟随格式）
```

核心判断：三个 Tab 的真实差异只是**格式**，动作只有三种。故以「格式」为唯一正交轴。

### 变更明细

| 项 | 前 | 后 |
|----|----|----|
| 切换按钮 | 6 | 3（格式选择器） |
| dropzone | 2 | 1（`accept`/`multiple` 随格式动态切） |
| 弹窗内 `file input` | 2 | 1 |
| 导入模式 | 隐式恒 `create` | 显式 `create`/`upsert`/`update` |
| 后端改动 | — | **零** |
| 前端净变化 | — | `index.html` −123/+~90 行、`app.js` −~90/+~140 行 |

### 关键实现决策

1. **格式配置表 `V2_IE_FMT`**（`app.js`）：`{label, accept, multiple, tpl}` 三源同构，格式切换只改这一个对象。CSV 导入端点是 `files: list`，故 `multiple` 必须随格式切，否则多文件 CSV 静默只传一个。
2. **JSON 用文本框替代 dropzone**：`v2IeFmt('json')` 时 `v2IeJsonBox` 显 / `v2IeDropZone` 隐，保持「一个输入区」不变式。
3. **`inp.value = ''` 复位**：切格式时清空已选文件，避免 `.csv` 残留在 Excel 导入里被提交。
4. **拖拽按 `accept` 过滤**（`v2IeDropText`/`ondrop`）：非 `.xlsx` 文件被拒并 toast，不静默丢弃。
5. **删除 `v2IETab` / `v2ImpTab` / `v2ImpJson` / `v2ImpExcel` / `v2ImpCsv` / `v2ExportStd`** 共 6 个函数，`v2ShowImpExp` 保留（工具栏入口 `onclick="v2ShowImpExp()"` 不动，避免断入口）。

---

## 二、附带修复：两处静默失效

| 项 | 前 | 后 |
|----|----|----|
| `mode` 参数 | 后端 `VALID_IMPORT_MODES`（`import_export.py:44`）已实现且有测试覆盖，**前端从不下发**，恒 `create` | UI 暴露三档下拉；JSON 走 Query（`import/json?mode=`），Excel/CSV 走 Form（`fd.append('mode', mode)`） |
| CSV 导出 | 按钮可点 → 必然 400 | `disabled` + `not-allowed` + tooltip + 点击兜底 toast |

> ⚠️ **风险披露**：`update`/`upsert` 路径变为可达后，发现 1 的「丢绑定」问题同步变为可达（`_common.py:315-322`）。UI 上的 `mode` 下拉已加 tooltip 说明语义（「create=始终新建（重名报 409）；upsert=按编码/名称命中则更新；update=必须命中」）。绑定修复见 §零 待决项。

---

## 三、测试：三层守卫

### 3.1 内容契约 `tests/test_frontend_imp_exp_ui.py`（30 例，新建）

沿用仓库既有前端契约模式（`test_quality_checker_p6a.py::TestReportLocateContract`）。守卫八类事实：

1. 旧双层 Tab 脚手架清除（函数 / 20 个 DOM id / `.v2-ie-tab` 样式类）；
2. 三段同框并列（分段容器不得内联 `display:none`；`v2IeFmt` 不得触碰分段容器）；
3. 唯一格式选择器（`V2_IE_FMT` 三格式齐全、驱动 `accept`/`multiple`/文案）；
4. `mode` 暴露 + 真实下发；
5. 单一 dropzone + **弹窗内无重复 DOM id**（`getElementById` 只取首个，重复 id 静默取错）；
6. 端点契约 + CSV 导出拦截 + 后端真实能力钉死；
7. 孤儿 DOM id 守卫：`ie_js` 内每个 `getElementById('x')` 的 `x` 必须在 HTML 存在；
8. `node --check` 语法关卡（无 node 时 skip）。

**自证有效（4 次变异测试，每例被正确守卫捕获）**：

| 变异 | 捕获者 |
|------|--------|
| `data-fmt="json"` → `"yaml"` | `test_exactly_three_format_buttons` |
| `var(--t2)` → `var(--t3)`（未定义变量） | `test_no_undefined_css_var` |
| `getElementById('v2IeName')` → 错拼 | `test_every_referenced_id_exists` |
| 删掉 `fd.append('mode', mode)` | `test_mode_read_and_sent` |

### 3.2 真实 DOM 冒烟 `scripts/smoke_imp_exp_ui.py`（28 例，新建）

Playwright + Chromium，`file://` 加载 `index.html`，**无需后端**。验证字符串断言看不见的真实副作用：格式按钮高亮切换、`accept`/`multiple` 复位、JSON 框与 dropzone 互斥、导出按钮 CSV 禁用、模板文案跟随格式、三段始终同框、无 `pageerror`。**28/28 通过**。

> 首轮出现 1 例误报：模态框是 `position:fixed`，而 `offsetParent` 对 fixed 元素**恒为 null**（CSSOM 规范），导致可见性判定错误。已改为「计算样式 + 包围盒」的 position 无关判定；该判定在正反用例（可见段 / 隐藏的 `#v2IeJsonBox`）上双向验证通过。

### 3.3 全量回归

| 项 | 结果 |
|----|------|
| 全量 | **1617 passed**（1587 基线 + 30 新增），**0 回归** |
| 覆盖率 | **100.00%**（11094 stmts / 0 miss）保持 |
| `node --check app.js` | OK |

---

## 四、发现 3 后续：数据标准 → 知识图谱（本批次未实施）

GitHub 调研结论：**无「数据标准 → 知识图谱」的现成开源产品**。相邻可借鉴项：

| 类别 | 项目 | 不适配处 |
|------|------|---------|
| 元数据→图谱 | `mkd-neo4j/lineage-graph` | 从活库抽而非标准定义；质量差（2 commits） |
| 元数据图谱库 | `neocarta`（Neo4j Labs） | 面向 LLM 生成 SQL，非人看图 |
| 元数据平台 | `OpenMetadata` / `Marquez` + `OpenLineage` | 平台级重（Java+ES+Airflow），与本仓 FastAPI 架构不符 |
| **本体约束图构建** | `neo4j-graphrag-python` `SimpleKGPipeline`（支持 schema grounding） | 输入为非结构化文本 |
| 本体资产 | `OpenMetadataStandards`（OWL/SHACL/RDF + 911 schema） | 规范资产，非构建工具 |
| 渲染 | `Cytoscape.js` / `ECharts graph` / `vis-network` / `AntV G6` | — |

**分档建议**（成本差两个数量级，待决）：

- **A 纯视图**：`GET /{id}/graph` 返回 nodes/edges（从现有 ORM 直接遍历，约 120 行）+ Cytoscape.js 渲染，**不引入图数据库**；
- **B 图查询**：A + 路径查询（「哪些字段受 X 字典约束」「改表 Y 影响哪些字段」）；
- **C 独立图库**：Neo4j/AGE 持久化 + 图算法 + 跨标准推理。

倾向 A 起步 —— 本仓模型（标准→层组递归树→表→字段→字典→字典项 + 字段-字典 N:M + 表-层组 N:M）**本已是一张图**，只是用树 + 关系表存着。

---

## 五、文件清单

| 文件 | 变更 |
|------|------|
| `app/frontend/index.html` | 模态框重写（单框三段）；CSS `.v2-ie-tab` → `.v2-ie-fmt`/`.v2-ie-sec` |
| `app/frontend/app.js` | 新增 `V2_IE_FMT` + `v2IeFmt`/`v2IeImport`/`v2IeExport`/`v2IeTemplate`/`v2IeDropText`/`v2IeDropHint`/`v2IeExportInfo`；删 6 个旧函数 |
| `tests/test_frontend_imp_exp_ui.py` | 新建，30 例 |
| `scripts/smoke_imp_exp_ui.py` | 新建，28 例 Playwright 冒烟 |
| `app/main.py` · `CONTEXT.md` · `deploy_linux.sh` · `deploy_windows.bat` · `restart.bat` | 版本锚 2.22.1 → 2.22.2 |
| `BASELINE.md` | 目录结构 / 脚本清单 / 版本管理 / 顶部日期同步 |
