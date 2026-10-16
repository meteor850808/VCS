# 修订记录 v2.22.3 — CSV 无损归档往返 + 字段-字典绑定模板往返

> **日期**: 2026-09-29
> **前版**: v2.22.2（UI 重构 + mode 暴露 + 字典绑定缺口查实）
> **性质**: 闭合 v2.22.2 §17 留档的两条技术债，CSV 格式升级为 A2-1 归档，无 UI 布局变更

---

## §零 本版要解决的问题

v2.22.2 查实并留档了两条缺口（`BASELINE.md` §17）：

| 缺口 | v2.22.2 状态 | v2.22.3 结果 |
|------|--------------|--------------|
| **字段-字典绑定不可随模板往返** | Excel/JSON 模板能导入字典，但 `import` 3 端点从不创建 `StandardFieldDictLink`；导出不带关联列 → 绑定全丢。绑定唯一入口是逐字段手工 `POST /fields/{id}/dict-links` | ✅ Excel / JSON / **CSV** 三格式均支持绑定往返 |
| **导出 CSV 端点缺失** | `export_standard` 只有 `json`/`excel` 两分支，`fmt=csv` 落 `else` 抛 400，而端点 Query 描述写「json/excel/csv」属不实描述 | ✅ `csv` 分支实现，产出 ZIP 归档 |

第二条尤其危险：绑定是质检的真实数据源（`rule_generator.py` → `VALUE_IN_SET` 自动规则 → `attribute_checker.py` A5 值域 31010005）。**导入完不绑定，值域质检整链失效**，且用户看不出任何异常。

---

## §一 方案决策

### Q1 绑定如何进 Excel？→ A 方案（用户已确认）

在「字段定义」Sheet **新增第 10 列「字典编码」**。

被否的 B 方案（独立「字段字典绑定」Sheet，需额外约束表编码+字段名复合键、行序、跨表查找）是模板复杂度换能力，A 方案在用户填写的**同一行**就地声明，认知成本最低。

**分隔符**：`,` `，` `;` `；` `|` 及空白 —— 中文 Excel 习惯用中文逗号，只支持半角会静默失败。
**语义**：保序去重；**首个编码落 `is_primary=True`**；未知编码跳过并产出告警（不硬失败，避免一个错编码让整批导入 400）。

### Q2 CSV 归档布局？→ A2-1「manifest + 规范化角色 CSV」（用户已确认）

ZIP 内 = `_manifest.json` + 6 个角色 CSV。manifest **只含格式元信息，不含任何结构数据** —— CSV 本身是唯一数据源，因此可在 Excel 里改 CSV 再导回，且不存在双源不一致。

被否的两个方案：

- **「一表一文件」**（v2.22.3 初版形态）：表名在标准内**不唯一**（实测存在两个层组下各一张「路面」），靠文件名单独标识无法消歧；且表 CSV 装不下层组层级/表编码/字段类型/字典项/绑定。
- **拍平成单个 CSV**：需要新增「所属表」列消歧，而 legacy CSV 导入器把表名当层组名 —— 拍平导出再导回会得到一张大表。

**关键取舍**：manifest 是格式标识器（format/version/encoding/generator/roles/diagnostics），把结构数据塞进 manifest 会产生「双源不一致」——用户改了 CSV 却没改 JSON，导入时以谁为准无从判断。

### Q3 `update/upsert` 的绑定清理策略？→ 保持全删重建

`_common.py` `_delete_standard_children` 先删全部子节点再建。这是既有的**「模板为权威状态」**语义。原先担心的「删除已手工绑定」在本版自动消失：模板既然携带绑定，重建时就会恢复。改为按字段精确清理会引入「本次导入未涉及的字段其绑定如何处置」的新语义，收益不抵风险。

### Q4 无 manifest 的普通 CSV 怎么办？→ 保留 legacy 路径

v2.22.2 之前外部作者可能已按「一个 CSV 一张表」在工作，归档格式升级不能打断他们。因此 ZIP 里没有 `_manifest.json` 时走 legacy 路径（每文件一张表、字段取首行、类型恒 `string`），**零破坏**。

---

## §二 CSV 归档格式规格

### ZIP 成员与表头

| 角色 | 文件名 | 表头 |
|------|--------|------|
| `standard` | `标准信息.csv` | 标准名称,标准描述,分类 |
| `layer_groups` | `层组.csv` | 层组名,层组编码,父级编码,描述 |
| `tables` | `数据表.csv` | 层组编码,表名,表编码,表类型,几何类型,描述 |
| `fields` | `字段.csv` | 表编码,字段名,中文名,数据类型,长度,精度,必填,主键,描述,字典编码,默认值 |
| `dictionaries` | `字典.csv` | 字典编码,字典名称,适用范围,字典描述 |
| `dictionary_items` | `字典项.csv` | 字典编码,字典项编码,字典项名称,父级编码,字典项描述,排序 |

- 全部 CSV **UTF-8 with BOM**（中文用户在 Excel 里双击无 BOM 会乱码）
- 列名与 Excel 模板同构，两个格式只需学一次
- 字典/字典项**拆两个文件**：Excel 是合并表，拆开后同一字典名称不可能在多行里互相冲突
- 字段表比 Excel 多一列「默认值」—— Excel 模板没有该列，往返会丢 `StandardField.default_value`
- `standard` 是**单行语义**（表头 + 1 行数据）

### manifest

```json
{
  "format": "vcs-standard-csv",
  "version": 1,
  "encoding": "utf-8-sig",
  "generator": "VCS 空间质检 2.22.3",
  "roles": { "standard": "标准信息.csv", "...": "..." },
  "diagnostics": []
}
```

`version` 未来值（>1）→ 400；非整数 / bool → 400；`format` 不符 → 400。

### 连接键

**层组编码 / 表编码 / 字典编码**是唯一的连接键，**行顺序即 `ordering`**。因此归档不依赖任何服务端 id，导到别的环境再导回来结构不变。

### 必需 / 可选角色

| 角色 | 类别 | 缺席时 |
|------|------|--------|
| `tables` `fields` | 必需 | 未声明或声明后文件缺失 → **400** |
| `standard` `layer_groups` `dictionaries` `dictionary_items` | 可选 | 未声明 → 静默按空处理；声明了却没上传 → **告警** |

区分两种「没有」：声明了却没上传 = 用户上传出错，必须告警；压根没声明 = 合法精简归档，不该骚扰。

### 降级与容错

| 场景 | 行为 |
|------|------|
| `layer_groups` 未声明 | 合成单个兜底层组，表按「第 0 个单元格」回落到兜底组 |
| 表的层组编码未知 | 落入兜底层组 + 告警 |
| 层组父级编码未知 | 当顶级 + 告警 |
| 字段的表编码未知 | 跳过该字段 + 告警 |
| 字典项的字典编码未知 | 跳过 + 告警 |
| 字段的字典编码未知 | 字段照常建，跳过绑定 + 告警 |
| 重复层组编码 / 同父级同名层组 / 同层组同名或同编码表 | 按首次出现生效 + 告警（否则撞 DB 唯一约束 500） |
| 重复字典编码 | 两行都保留，但绑定按首次生效 + 告警 |
| 层组/表/字典编码留空 | 可用（表回落到兜底组），不污染 diagnostics |
| 列数不齐 / 整数列填非数字 | 当空处理，不整份归档失败 |
| manifest 未声明的额外文件 | 告警并忽略 |
| 角色文件不是合法 UTF-8 | **400**（静默按空导入 = 删数据） |

### `标准信息.csv` 的消费语义

`DataStandard.description/category` 会被导出到这个角色，导入时**必须真的读回来**——否则「无损往返」只是半句承诺：结构无损而元信息有损，属于最难被发现的一类数据丢失。

- **表单优先**：表单显式传了值 → 以表单为准，归档只作**默认值来源**
- **名称永不覆盖**：名称是导入目标解析键（`update/upsert` 靠它找标准），归档改名会让 update 打到一个用户没指定的记录上。冲突只告警
- `category` 的表单默认值是 `'custom'`，无法区分「显式传 custom」与「没传」→ 沿用本模块既有约定：非 `'custom'` 视为显式指定，否则回落到归档值
- 多行 → 告警并取首行

---

## §三 实现清单

### 后端

| 文件 | 变更 |
|------|------|
| `app/services/data_standard_parser.py` | `V2FieldDef` 增 `dict_codes: list[str]`；新增 `_split_dict_codes()`（多分隔符 + 保序去重 + 表头残留过滤）；「字段定义」读第 10 列；**新增空字段名守卫** |
| `app/api/v1/data_standard_v2/import_export.py` | `_create_v2_children` 建 `StandardFieldDictLink`（`pending_links` 延迟到字典建完再统一建链）；JSON 导入/导出 `dict_codes`；Excel 模板 +10 列；JSON 模板 +`dict_codes`；**CSV 归档导出 + manifest 导入分派 + 六角色模板**；导出查询加 `selectinload(fields→dict_links→dictionary)` |
| `app/frontend/app.js` | 解除 CSV 导出禁用；下载扩展名 `csv→zip`；tooltip 改为说明归档结构（manifest + 6 角色 CSV + 字典 + 绑定） |

### `import_export.py` 新增符号

```
_CSV_FORMAT / _CSV_VERSION / _CSV_ENCODING / _CSV_MANIFEST_NAME / _CSV_BOM
_CSV_ROLES / _CSV_HEADERS / _CSV_REQUIRED_ROLES / _CSV_TRUE_TOKENS
_csv_bytes / _csv_cell / _csv_flag / _csv_int / _sorted_by_ordering
_read_upload_blobs        # 读所有上传文件（无名文件计告警，HTTP 层走不到，由直测覆盖）
_validated_manifest       # format/version 严格校验
_archive_rows             # 按 manifest.roles 定位并读某角色数据行
_archive_standard_meta    # 读 标准信息.csv → {name, description, category}
_merge_standard_meta      # 归档作默认值、表单优先、名称不覆盖
_archive_declared_files
_create_csv_children_legacy     # 无 manifest 的旧路径
_create_csv_children_from_archive  # 归档路径
_field_dict_codes / _duplicate_codes / _manifest_bytes / _build_csv_archive
```

### 模板填写说明

「字段定义」末尾追加一行：第 10 列写「编码须与「字典定义」一致，多个用逗号分隔」，**表编码/字段名留空**。解析器空字段名守卫保证该行不被当成数据。

---

## §四 测试

`tests/test_imp_exp_dict_roundtrip.py`（76 例，含 1 例断言卫生守卫）+ `tests/test_import_conflict_messages.py`（6 例，约束冲突文案） + `tests/test_csv_upload_edge_cases.py`（2 例）+ `tests/test_import_export_routes.py` 新六角色模板契约。

| 分组 | 覆盖 |
|------|------|
| Excel 第 10 列 | 模板表头 + 示例 + 填写说明行、解析多分隔符/去重/空值/表头残留、幽灵字段守卫、导入建 N:M、多值建链、未知编码告警、导出关联列、旧 9 列兼容 |
| JSON `dict_codes` | 模板示例、导入建链、未知编码告警、导出→再导入保绑定 |
| CSV 归档导出 | manifest + 六角色成员、manifest 不含结构数据、字典拆两文件、空字典存活、全 CSV 带 BOM、重复编码进 diagnostics、空标准仍产出合法 ZIP、未知格式 400 |
| CSV 归档导入 | 全结构往返、嵌套层组父级、字典绑定、未知编码告警不失败、重复编码告警、diagnostics 回放、upsert 重建、角色文件名可自定义、额外文件告警、必需角色缺失 400、未来版本 400、manifest 畸形 400（多组参数化）、非 UTF-8 400、单元格容错 |
| CSV 降级 | 无 manifest 走 legacy、精简归档不告警、缺层组合成兜底组、未知层组/父级/表/字典引用、重复层组与表跳过、重复字典保留、代码留空可用 |
| `标准信息.csv` | 往返恢复 description/category、表单优先、名称冲突告警不静默改名、角色缺席时只用表单、update 模式生效、多行告警、**导出必须带表头行**（回归守卫） |
| 质检联动 | 导入建链后 `generate_rules_for_standard` 能生成 `VALUE_IN_SET` 规则 |
| 断言卫生 | 守卫用例：禁止 warnings 断言文案成为 fixture 名的子串（假阳性） |
| 变异测试 | 11 条语义变异全部被捕获（见下） |

### 回归

```
全量：1701 passed，0 失败
覆盖：100.00%（11439 stmts / 0 miss，124 文件满覆盖）
前端契约：tests/test_frontend_imp_exp_ui.py 30 passed
Playwright：scripts/smoke_imp_exp_ui.py 31/31
node --check app/frontend/app.js：OK
归档人工校验：scripts/verify_csv_archive.py
变异测试：scripts/mutation_check.py 11/11 全部捕获（0 存活）
```

### 变异测试

`scripts/mutation_check.py` 对 `import_export.py` 的关键契约逐条注入语义变异，跑
`tests/test_imp_exp_dict_roundtrip.py` 验证「测试真的会红」。11 条全部被捕获，
且每次运行结束自动还原源文件、复跑基线确认 PASS。

| # | 变异意图 |
|---|----------|
| M1 | 必需角色判断反转：`tables`/`fields` 缺席不报 400 |
| M2 | 「名称永不覆盖」被去掉：归档名称直接改写导入名称 |
| M3 | 空字典又被过滤掉（回退到 Excel 侧 `if d.items` 的旧行为） |
| M4 | 重复编码检测失效：`diagnostics` 永远为空 |
| M5 | 未知字典编码不告警（静默跳过绑定） |
| M6 | `standard` 角色的 `description` 不消费（回到 §五 修复前） |
| M7 | BOM 被移除（中文用户在 Excel 里双击会乱码） |
| M8 | 必需角色缺席只告警不报错（静默建空标准） |
| M9 | `standard` 角色回到 §五 的错位 bug（首格表头文字 + 3 个数据值） |
| M10 | legacy 降级失效：有 manifest 也走 legacy（外部作者的旧 ZIP 会静默丢结构） |
| M11 | 重复层组编码不再跳过（撞 DB 唯一约束 → 500） |

### 变异测试暴露的测试缺陷

M11 首次运行为「存活」。追查后确认**不是实现有洞，而是测试在假阳性**：

- `test_duplicate_layer_group_code_skipped` 用整行复制制造重复，
  于是「编码重复」和「名称重复」两个分支**同时**命中；变异掉 code 分支后
  name 分支兜住，落库结果不变，code 分支从未被真正考验。
- 该用例的种子标准名叫「层组编码重复」，导入时必发
  「标准名称为「层组编码重复」，与导入名称…不一致」告警，
  而断言正是 `any("层组编码重复" in w ...)` —— 被这条**无关告警**以子串满足。

同类隐患在全文件扫出 3 处，全部修掉：

| 用例 | 问题 | 修法 |
|------|------|------|
| `test_duplicate_layer_group_code_skipped` | 种子名含断言文案；样本同时触发两个分支 | 改名「组码撞车」；重复行改成**同码不同名**，让 name 分支兜不住 |
| `test_duplicate_dict_code_warns_but_keeps_both_rows` | 种子名「字典编码重复」= 断言文案 | 改名「两本材料表」 |
| `test_archive_duplicate_code_warns_on_import` | 断言文案是裸「重复」，种子名「导入侧重复」含之 | 改名「手改归档」；断言锚定 `表编码重复` + `SURF` |
| `test_standard_role_only_first_row_is_used` | 断言「标准信息」，种子名「多行标准信息」含之 | 改名「单值角色」 |

并新增永久守卫 `TestWarningAssertionsAreNotFooledByNames` —— 静态扫描本文件，
禁止任何 `any("<文案>" in w ...)` 的文案成为 fixture 名的子串。
（守卫自身已做注入验证：临时塞入假阳性 → 用例转红。）

> 教训：变异测试的价值不只在「发现实现有洞」，同样在「发现测试没在测它声称的东西」。
> 一条恒真的断言比没有断言更危险 —— 它让覆盖率报告说谎。

### 新增/修改的既有测试

- `tests/test_import_export_routes.py::test_export_bad_format` — 反例从 `csv` 改为 `xml`
- `tests/test_frontend_imp_exp_ui.py::test_csv_export_guarded` → `test_csv_export_enabled_with_zip_extension`
- `tests/test_imp_exp_dict_roundtrip.py::test_empty_standard_yields_header_only_roles` — `standard` 角色是表头 + 1 行，其余角色空标准下只有表头

---

## §五 本版修掉的一个静默 bug

`标准信息.csv` 导出时写成 `[["标准名称", std.name, std.description, std.category]]` —— **首格是表头文字、后面直接跟 3 个数据值，整列错位**：

```
标准名称,道路检查标准,演示标准,custom     ← 4 列，无表头行
```

导入侧因首行不匹配 `_CSV_HEADERS["standard"]` 不会剥表头，于是把这一行当数据 → 名称/描述/分类整体左移一列。这正是「导出自己认自己」型测试抓不到、只有真的消费该角色才会暴露的缺陷。

修复为 `[_CSV_HEADERS["standard"], [name, description, category]]`，并加回归守卫 `test_standard_role_is_exported_with_header_row`。

---

## §六 未做 / 已知技术债

- **知识图谱**仍为调研结论，未实施。建议纯视图（ORM → nodes/edges + Cytoscape.js/ECharts），不引入图数据库。
- ~~**Excel 空字典导出问题**：Excel 侧 `if d.items` 会静默丢弃空字典（CSV 归档已能表达）。本轮未改。~~ → **原记录不成立，已在「§六·补二」查实并修复**（见下）。
- ~~`update/upsert` 三格式统一返回 201（沿用既有跨格式契约，非本版引入）。~~ → **经取证从记录时点起即已满足，契约已集中钉死（见 §六·补五）**。
- `_delete_standard_children` 保持全删重建（见 Q3）。

---

## §六·补 约束冲突错误信息纠偏（已收口）

§六 开头记录的「宽泛 `IntegrityError` 误报」在本轮追加收口。

### 缺陷实证

新建标准时**编码**已被占用（名称全新），接口返回：

```
409 标准名称 '全新的名字' 已存在（或标准编码 'TAKEN' 已被使用），创建失败
```

**名字根本没冲突。** 用户按提示去改名字永远改不通 —— 这是比崩溃更糟的失败：
接口给出可执行建议，但那个建议是错的。

根因：6 处 `except IntegrityError:` 丢弃异常内容，一律套用「标准名称已存在」文案。
`dictionaries.py` 的同名分支反而正确（该路径已预查重，且记了 `e.orig` 日志）。

### 修复

1. `_common.py` 新增约束名 → 实体的映射，覆盖 8 张表 11 条唯一约束
   （约束名从 `models/*.py` 的 `UniqueConstraint` 实测导出，不靠猜）：

   | 约束 | 实体 |
   |------|------|
   | `uq_ds_api_name` / `uq_ds_api_code` | 标准名称 / 标准编码 |
   | `uq_slg_std_parent_name` / `uq_slg_std_parent_code` | 层组名称 / 层组编码 |
   | `uq_st_layer_name` / `uq_st_layer_code` | 数据表名称 / 数据表编码 |
   | `uq_sf_table_field` | 字段名称 |
   | `uq_sd_standard_name` / `uq_di_dict_code` | 字典名称 / 字典项编码 |
   | `uq_slgt_layer_table` / `uq_sfdl_field_dict` | 层组-表关联 / 字段-字典绑定 |

2. `_extract_constraint_name()` 从 `IntegrityError` 取 PG 约束名。
   **注意**：asyncpg 的错误对象**没有** `constraint_name` 属性，`sqlstate=23505`
   只能区分「唯一冲突」不能说明是哪一条 —— 约束名只在 `str(e.orig)` 文本里，
   故走正则匹配。这是当前驱动下的次优解（换 `psycopg` 可拿到结构化字段）。
3. `_conflict_detail()` 生成文案。**未知约束绝不猜测成「标准名称已存在」**，
   退回通用文案并回显约束名，便于排查。
4. `_raise_conflict()` 统一入口，补上此前完全缺失的 `logger.warning`。
5. 接入 5 处：`standards.py` 2 处（create / update）、`import_export.py` 4 处
   （json / excel / csv / v2 模板导入）。`standards.py` 的 create 按约束类型
   决定回显 `code` 还是 `name` —— 编码冲突不该回显名称，反之亦然。

### 修复前后

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 编码撞车（名称全新） | `标准名称 '全新的名字' 已存在` ❌ | `标准编码「TAKEN」已存在或重复（uq_ds_api_code）` ✅ |
| 名称撞车（编码全新） | 扯上无辜的编码 ⚠️ | `标准名称「X」已存在或重复（uq_ds_api_name）` ✅ |
| 子实体冲突（字段重名等） | 报「标准名称已存在」 ❌ | 报「字段名称…已存在（uq_sf_table_field）」 ✅ |

### 回归

`tests/test_import_conflict_messages.py`（6 例）：3 个 HTTP 端点级 + 3 个纯函数级
（含 9 条约束的映射表逐条断言、未知约束与缺失约束的兜底）。
全量 **1701 passed**，覆盖 **100.00%**（11439 stmts / 0 miss），随机序与固定序均通过。

---

## §六·补二 Excel 无编码字典静默丢失（已收口）

§六 原记录的「Excel 空字典导出丢失」经取证**不成立**，但沿同一条线查出了**真的**数据丢失。

### 取证：原记录不成立

原记录称 `download_excel_template` 用 `if d.items:` 过滤会丢空字典。实测导出侧
**已有 `else` 分支**（`import_export.py` ~1592 行）写占位行，空字典往返无损：

| 变体 | 回导结果 | 判定 |
|------|----------|------|
| 原样回导 | `('EMPTY','空字典',…)` 完整保留 | ✅ |
| 删掉整行 | 空字典消失 | ✅ 用户主动删除 |
| 清空名称留编码 | `('EMPTY','EMPTY',…)` 名称回落编码 | ✅ 合理兜底 |
| **清空编码留名称** | **空字典消失** | ⚠️ 见下 |

（回导须用 `mode=update` —— 导出件自带原标准编码，`create` 撞编码是预期行为。）

### 真缺陷：解析侧静默丢弃

`data_standard_parser.py` 解析「字典定义」Sheet 时，首列空即 `continue`：

```python
if not row or row[0] is None or str(row[0]).strip() in ("字典编码", "dict_code"):
    continue          # ← 整行连字典带字典项一起丢，且无任何告警
```

`row[0] is None` 对「只有名称、没填编码」的行同样命中 —— 用户填了字典名，
导入后字典和字典项凭空消失，**界面上一句提示都没有**。

### 修法：对齐下游既有契约，不新造语义

关键依据：消费解析结果的 `_create_v2_children` **本来就是按「字典可以无编码」写的**：

```python
dict_code=d_def.dict_code or None,      # 存 None
...
if d_def.dict_code:                     # 有编码才注册字段绑定
    dict_id_by_code[d_def.dict_code] = dict_obj.id
```

`_create_json_children` 同样如此。**下游早已准备好接收无编码字典，唯一的 bug 是
解析器在送过去之前就扔了**。故改法是让解析器对齐既有契约：

1. 新增 `_cell_str(row, idx)` 统一取值 —— openpyxl 空单元格读作 `None`，
   直接 `str()` 会得到字面量 `"None"`；早前正是 `row[0] is None` 的写法
   造成了误判。
2. 编码与名称**皆空**才跳过（真正空行）。
3. 无编码字典按**名称**归组（`@name:` 前缀），保留实体与字典项，
   并告警「字典「X」缺少字典编码：字典已保留，但该字典无法参与字段绑定，
   请补上编码后重新导入」。

### 回归

`tests/test_excel_codeless_dict.py`（8 例）：解析器层 7 例（按名保留、告警、
字典项不丢、同名区分、有编码路径不变、真空行守卫、`_rows_of` 上游过滤）
+ 端点级 1 例（导出件追加无编码行 → 回导 201 → 库中在且有告警）。

`tests/test_tail_data_p6e.py::test_v2_full_parse` 原固化「空格编码 → 跳过」
的旧行为（注释引用的是**源码行号注解**、非外部规格），已按新契约更新断言。

变异测试 5/5 全部捕获：还原旧 `if not code` 丢弃、`_cell_str` 去掉 `None`
守卫、去掉告警、无编码字典改用编码位归组（名字不同也互相覆盖）、丢弃字典项。

全量 **1709 passed**，覆盖 **100.00%**（11452 stmts / 0 miss），随机序与固定序均通过。

---

## §六·补三 版本回滚快照保真度（已收口）

知识图谱方案取证（图数据库 vs 纯视图）时顺带发现的**静默丢数据**，优先级高于图谱本身。

### 缺陷链

`rollback_version` 的流程是「先全删、再从快照重建」：

```
_delete_standard_children   →  删 FieldDictLink/Field/Table/LayerGroup/Item/Dict
_restore_from_snapshot      →  按 snapshot_data 重建
```

但 `_build_standard_snapshot` 只存了实体主干 —— **两张「边」表压根不在快照里**，
一批列也没存。于是回滚等于：

| 丢失项 | 后果 |
|---|---|
| `StandardFieldDictLink`（整个实体） | **字段-字典绑定全丢** → 值域质检（`VALUE_IN_SET` / A5 值域 31010005）整链失效 |
| `StandardDictionary.dict_code` | v2.22.3 刚确立的绑定主键没了 |
| `DictionaryItem.parent_code` | 字典层级塌成平铺 |
| S9 次归属（`StandardLayerGroupTableLink`） | 只恢复主归属，多分类丢失 |
| 层组/表/字典项的 `description`/`ordering` | 元信息丢失 |
| `StandardDictionary.description`/`is_system`、表的 `description` | 同上 |

另有两个**幽灵字段**：快照写入 `field_code` / `table_type_ext`，但 ORM 里没有
这两列 —— 写了又不会被恢复，纯虚假安全感。

### 修法

**快照端**（`_build_standard_snapshot`）：
- 补 `selectinload(table_links)` / `selectinload(field_links)` 两张边表的加载
- 补 `field_dict_links` + `layer_group_table_links` 两个顶层键（含次归属、
  `is_primary` / `filter_condition` / `ordering`）
- 字典补 `dict_code` / `description` / `is_system`；字典项补 `parent_code` /
  `description` / `ordering`；层组与表补 `description` / `ordering`
- 删两个幽灵字段

**恢复端**（`_restore_from_snapshot`）：
- 顺序改为**先建完所有实体，再建边** —— 边两端都是 FK，实体没落库插边会撞约束
- `_restore_layer` 不再顺手 `_primary_table_link`（显式边存在时会与快照里的
  主归属行撞 `uq_slgt_layer_table`）；主归属统一在边段恢复
- **老快照兼容**：没有边表的旧版本快照 → 按 `layer_id` 兜底补一条主归属，
  保证「每表恰有一主归属」不变量；缺列键走默认值，不 KeyError

### 回归与变异

`tests/test_version_snapshot_fidelity.py`（9 例）：边保真 4（绑定含
`is_primary`/`filter_condition`/`ordering`、dict_code、字典层级、S9 次归属）+
列保真 2（层/表/字段元信息）+ 幽灵字段 1 + 老快照兼容 2（无边表回滚、缺键不炸）。

变异 **14/14 捕获**：逐条还原各丢失项（不重建绑定、不重建 S9、丢 dict_code、
丢 parent_code、丢各 description/ordering、老快照不补主归属、幽灵字段写回等）。
首轮 M14「S9 ordering」漏过 —— 揪出一个**测试自身的假绿缺陷**：

> 回滚用快照里的**相同 id** 重建实体，而测试全程共用一个 `db_session` ——
> 查询命中 identity map 里破坏前的旧对象，断言的其实是「没变过」而非
> 「被还原了」。修复：断言前 `db_session.expire_all()`。

全量 **1718 passed**，覆盖 **100.00%**（11465 stmts / 0 miss），随机序与固定序均通过。

---

## §六·补四 知识图谱 A 档纯视图（已落地）

§六 首条「知识图谱仍为调研」收口。决策与权衡全文见
`docs/adr/ADR-0003-knowledge-graph-pure-view.md`，要点：

- **不引入图数据库**：层级深度硬上限 5（真实参照物深度实测 2）、双写面
  横跨 20+ 写入口且 update/upsert 走全删重建、与「PG 唯一真源」方向冲突。
  重评估触发条件（深度放开/跨标准影响分析高频/万级标准/图算法需求）留档 ADR。
- **两端点契约**（载荷由真实数据驱动）：`GET /{id}/graph` 摘要
  （nodes + binds 聚合边 + 徽章，**不含明细**）+ `GET /{id}/graph/nodes/{id}`
  懒加载。SHGT_SURVEY（255 表/3288 字段/2031 条目）摘要实测 ~70KB，
  而一次全给约 1.5MB —— 「一次全给、前端折叠」的简单方案被真实数据否掉。
- **语义细节**：全局字典按引用纳入（未引用不进图）、悬挂边不产生、
  S9 次归属画虚线、binds 聚合到 (表,字典) 级带 field_count/primary。
- **前端**：标准卡片 🕸 按钮 → `v2GraphModal` 手写 SVG 三列布局
  （层组|表|字典），零新依赖（不引 Cytoscape，遵守不依赖 CDN 全部内联规矩），
  点击表/字典懒加载展开明细，绑定边粗细 = 字段数。

### 参照物导入顺带修掉的真实数据缺陷

一厅八室五份 JSON **全部**无法导入——两层数据脏：

| 脏 | 规模 | 原行为 | 现行为 |
|----|------|--------|--------|
| 同表重复字段名 | GEO 2 / LAND 24 / PLAN 116(216 excess) / PROJECT 1 / SURVEY 111 处 | 撞 `uq_sf_table_field` 整单 409 | 保首个 + 告警 |
| 同字典重复条目码 | LAND 8 / SURVEY 3 处 | 撞 `uq_di_dict_code` 整单 409 | 保首个 + 告警 |

设计边界：**行级重复**（字段/条目）告警去重；**结构级重复**（表/字典）
仍硬失败但按真实约束归因（v2.22.3 既有原则的延续）。计数同步改为
实际创建数——源 1348 字段去重后落库 1324，响应计数不再说谎
（为此删掉了按源文件字面统计的 `_count_tables`/`_count_fields`）。

### 回归

- `tests/test_standard_graph_view.py`（9 例）：契约（摘要无明细/徽章/层级边/
  S9 主次/聚合口径/stats/404 归属/空标准/明细 404 族）
- `tests/test_frontend_graph_ui.py`（19 例）：弹层结构/入口接线/端点一致/
  折叠契约双保险（JS+Pydantic 两层查）/零依赖/深色主题/孤儿 id/CSS 变量
- `tests/test_graph_reference_oracle.py`（8 例）：**真实数据 oracle**——期望值
  全部离线预计算自源 JSON（LAND 1324 字段/218 绑定字段/214 边；SURVEY
  3177 字段/摘要 <400KB；「户」84 源字段去重后 50），不从实现反推
- 图谱端点变异 **10/10 捕获**（两处漏网均为夹具数值巧合，已修：
  linked≠binds 拉开差距、真悬挂边场景）
- `scripts/import_reference_standards.py`（mode=upsert 幂等）已将五份真实
  标准导入开发库（GEO 678 字段/LAND 1324/PLAN 1559/PROJECT 1744/SURVEY 3177）

全量 **1755 passed**，覆盖 **100.00%**（11591 stmts / 0 miss），随机序与固定序均通过。

---

## §六·补五 update/upsert 状态码契约钉死（债清单清零）

§六 最后一条债收口。**取证结论：该债从记录时点起就已满足** ——
三个导入端点装饰器均为 `status_code=201`（记债时的 `6e1d39d` 即如此），
行为矩阵实测一致：

| 格式 | update(命中) | upsert(命中) | upsert(未命中) | update(未命中) | mode 非法 |
|------|--------------|--------------|----------------|----------------|-----------|
| json | 201/updated | 201/updated | 201/新建 | 404 | 400 |
| excel | 201/updated | 201/updated | 同 json 语义（`_resolve_import_target` 统一解析） | | 400 |
| csv | 201/updated | 201/updated | 同上 | | 400 |

残留的不是行为缺陷，而是**契约未被集中钉死** —— 既有 201 断言散落在
各场景测试里，任何一格被改回 200/204 都不会被专门发现。

**修法**：新增 `tests/test_import_status_contract.py`（7 例）把矩阵钉在
一处。变异 **3/3 捕获**：逐格式把 `status_code=201` 改 200，各被对位
测试类咬住（json 2 红 / excel 1 红 / csv 1 红）。

**设计取舍留档**：update 回 201 而非 200 是沿用的既有跨格式契约 ——
导入端点整体按「创建或替换」语义建模，`ImportResult.updated` 区分两种
结果；改 200 属破坏性契约变更且无用户可见收益。

**至此 §六 债清单全部清零**：知识图谱（§六·补四落地）、约束冲突
（§六·补）、Excel 空字典（§六·补二）、update/upsert 状态码（本节）。

全量 **1762 passed**，覆盖 **100.00%**（11591 stmts / 0 miss），随机序与固定序均通过。

---

## §六·补六 用户实测报告：Excel/CSV 重复字段名 409 + upsert 500（已修复）

用户在导入 UI 实测：同一份文件，纯新建报
`字段名称「大地质」已存在或重复（uq_sf_table_field）`，切「新建/更新」(upsert)
直接 **500**。

### 根因

§六·补四的参照物兼容修复**只覆盖了 JSON 路径**：

| 路径 | 行级去重 | update/upsert 分支兜底 |
|------|----------|------------------------|
| JSON | ✅（补四修的） | ❌ 裸奔 → **500** |
| Excel v2 | ❌ | ❌ → **500** |
| Excel legacy | ❌ | ❌（不可达，见下） |
| CSV archive | ❌（字段/条目；表/字典编码行本有宽容去重） | ❌ → **500** |
| CSV legacy | ❌（表头重名列） | （表名=文件名，天然唯一） |

create 分支有 `except IntegrityError → 409`，update/upsert 分支三个端点全部没包 ——
所以同一个冲突 create 报 409、upsert 变 500。

### 修法

1. **行级去重铺满四路径**：同表重复字段名 / 同字典重复条目码 / legacy 表头
   重名列 → 保首个 + 告警（`_create_v2_children` / `_create_flat_children` /
   `_create_csv_children_legacy` / `_create_csv_children_from_archive`）。
2. **update/upsert 分支兜底**：JSON / Excel v2 / CSV 三处补
   `IntegrityError → rollback → _raise_conflict`（409 精确归因）。
   **Excel legacy 有意不补**：Sheet 名即表名天然唯一、目标按名解析无外部撞名、
   字段去重后无约束可达 —— 包了就是测不到的死代码。
3. **CSV 归档语义维持**：重复表行继续宽容（201+告警，v2.22.3 既有设计）；
   字典重名行（不同 code 同名）撞 `uq_sd_standard_name` → upsert 下 409。

### 取证顺带发现（新债，未修）

**前端 CSV 导入收不了归档 ZIP**：`V2_IE_FMT.csv.accept='.csv'`（v2.22.2
legacy 语义），而 v2.22.3 导出产出 ZIP；端点 `_read_upload_blobs` 也不解压
整包 ZIP part → UI 导出的 CSV 归档无法经 UI 回导。修法留档 BASELINE §17
（推荐后端检测 ZIP part 自动解压，前端零改动）。

### 回归

`tests/test_import_row_dedup_all_formats.py`（8 例）：Excel v2 去重+upsert
结构冲突 409 / Excel legacy 去重 / CSV archive 去重+表行宽容+字典重名 409 /
CSV legacy 表头去重 / JSON upsert 结构冲突 409。

全量 **1770 passed**，覆盖 **100.00%**（11640 stmts / 0 miss），随机序与固定序均通过。

---

## §七 教训

**「导出自己认自己」不是往返测试**。往返测试的「给定」必须由 ORM 直搭源状态（不依赖被测导入路径），否则测的是「导入认得导出」而非「结构真的等价」。

**导出格式的每一列都要有消费方**。写了但没人读的列 = 静默数据丢失，比没写更难发现。

**笼统的异常兜底会把「诊断」变成「误导」**。`except IntegrityError` + 固定文案
看似处理了异常，实则把根因替换成了猜测；而猜测又恰好可执行，用户的信任被引导向
错误方向。宁可给「约束 `uq_xxx` 冲突」这种不友好但真实的文案。

**债清单本身也会错，要取证不能照单全收**。本轮 §六 两条「未修」债：一条早已
在 `e6d6a6a` 修掉却仍标「未修」，一条描述的缺陷压根不存在。照着不准确的记录
去「修」，会写出错的代码；而真正在丢数据的缺陷（无编码字典静默丢弃）就藏在
那条错记录背后。**改代码前先证明 bug 存在。**

**「下游已经准备好」是最强的修法依据**。发现某类输入被丢弃时，先看消费方是否
已经写好了处理逻辑 —— 若 `_create_v2_children` 早就按「可以没有编码」写，
那解析器的丢弃就是单点 bug，照下游契约回填即可，不必另立语义。

**「删除」与「快照」必须成对审计**。任何「先删后建」的路径，删除清单里的
每一项都要能回答「快照记了它吗？」—— 本缺陷正是 `_delete_standard_children`
删了绑定与 S9 边，而快照构造者根本不知道有这些东西要记。删了但没记 = 回滚即丢。

**同 id 重建会让断言读到内存里的旧世界**。回滚/恢复类测试若全程共用一个
session，重建的实体与旧实体主键相同，查询会命中 identity map 里的陈旧对象 ——
变异测试是发现这种「假绿」的最有效手段：实现怎么改测试都绿，说明断言根本
没落在数据库上。`expire_all()` 应成为此类测试的标准动作。

**「一次全给更简单」活不过第一份真实数据**。图谱摘要最初倾向一次全给、
前端折叠；SHGT_SURVEY 的 3288 字段 + 2031 条目把该方案直接判死（~1.5MB）。
设计载荷契约前先量真实数据的尺寸——同一批参照物还揭出五份源文件全部带
重复字段名/条目码，两个静默 409 缺陷在合成测试下从未现形。**真实数据是
最便宜的 oracle：期望值离线预计算自源文件，断言不从实现反推。**

---


---

## §六·补七 用户实测批次发现：CSV 归档「导得出、导不回」（已修复）

### 症状

v2.22.3 把 CSV 导出改成 ZIP 归档（manifest + 6 角色 CSV），但导入 UI 的
`V2_IE_FMT.csv.accept` 还停在 `.csv`（v2.22.2 散装语义），后端
`import_standard_csv` 也只认**拆开的散装 part** —— 整包 ZIP 会被 legacy
路径当「非 CSV 文件」跳过，返回 **201 + 0 表的静默空导入**。
`test_import_status_contract` 的 CSV 用例因只断言 201 + updated 而一路空转，
没能拦住这个缺口。

### 修法（前后端各一刀）

1. **后端** `_expand_zip_blobs`：读完 blobs 后整包解包 —— `.zip` 后缀或
   PK 魔数识别；坏包 400（不猜、不静默跳过）；内层按 basename 展平
   （用户打包整个文件夹也能回导）；目录条目与 `.` 开头系统垃圾跳过；
   与散装 part 同名 → 保留先者 + 告警；解包后移除 ZIP part 本身，
   防止它再以「非 CSV 文件 / 未声明文件」身份污染告警。解包后仍按
   manifest 有无分派归档 / legacy 路径 —— 用户自己打包的散装 CSV
   （无 manifest）照样按 legacy 每文件一表导入。
2. **前端** `V2_IE_FMT.csv.accept` 改 `'.csv,.zip'`，`v2IeDropText`
   支持 `cfg.hint`，CSV 提示改「可多选散装 CSV，或直接拖入导出的 ZIP 归档」。

### 回归

`TestImportAcceptsWholeZip`（后端 5 例）：整包往返结构全等、upsert 真实
计数、坏包 400、散装打包 legacy、重名合并保先者；前端 2 例钉 accept 与
拖拽提示。变异 3/3 捕获（移除解包调用 / 移除重名告警 / 移除坏包 400）。

全量 **1778 passed**，覆盖 **100.00%**（11665 stmts / 0 miss），随机序与固定序均通过。

*详细基线见 `BASELINE.md`；历史修订记录见 `archive/revision_vX_Y.md`（v2.15.0 及更早）。*
