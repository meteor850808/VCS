# VCS v2.9 修订记录 — 数据字典展示拆分（全局 / 标准两个维度）

## 版本信息
- **版本**: v2.9
- **日期**: 2026-08-12
- **类型**: 数据标准模块功能拆分——数据字典展示按「全局字典 / 对应数据标准字典」两个独立维度组织，不与表/字段做关联
- **影响范围**: `app/api/v1/data_standard_v2.py`、`app/frontend/index.html`、数据库存量数据清洗
- **关联文档**: `docs/test_manual_v2_9.md`

---

## 修订目标

1. **字典维度混杂**：改造前标准级字典与全局共享字典在列表中混杂展示，全局字典展示依赖选中标准/表/字段的上下文
2. **拆分需求**：数据字典应独立为「🌐 全局字典」与「📋 标准字典」两个维度——全局字典不与任何标准/表/字段绑定；标准字典仅对应当前选中的数据标准（不依赖层组/表/字段）
3. **数据模型一致性**：确立不变量——`scope='global'` 的字典 `standard_id` 恒为 NULL；`scope='standard'` 必须归属标准

---

## 详细变更

### 1. 后端（`app/api/v1/data_standard_v2.py`）

#### 1.1 标准下字典列表强制 standard 维度
- `list_dictionaries`（`GET /data-standards-v2/{standard_id}/dictionaries`）：移除 `scope` Query 参数，查询强制 `scope='standard'`——标准维度列表只含标准字典

#### 1.2 标准级字典创建校验
- `create_dictionary`（`POST /data-standards-v2/{standard_id}/dictionaries`）：`body.scope` 非 `standard` 时 400（提示走全局端点 `POST /dictionaries`）；实际落库 `scope="standard"`

#### 1.3 新增全局字典创建端点
- `POST /dictionaries`：创建全局字典——`standard_id=None`、`scope` 强制 `'global'`（忽略 body 传入值）、**应用层重名预查**（DB 唯一约束 `uq_sd_standard_name(standard_id, dict_name)` 对 NULL `standard_id` 不生效，重名保护只能在应用层）+ `flush`，撞唯一约束兜底 409

#### 1.4 全局字典列表只返回全局维度
- `list_global_dictionaries`（`GET /dictionaries`）：仅返回 `scope='global' AND standard_id IS NULL`（保留 `keyword` 过滤）；移除 `scope` 参数

#### 1.5 更新 scope 一致性
- `update_dictionary`（`PUT /dictionaries/{id}`）：scope 变更一致性——
  - 转 `global`：自动清空 `standard_id`
  - 转 `standard`：`standard_id` 为空则 400（标准字典必须归属标准）

#### 1.6 分享语义修正
- `share_dictionary`（`PUT /dictionaries/{id}/share`）：转 `global` 同时 `standard_id=None`（此前遗留共享字典可能保留 standard_id，产生隐身脏数据）

### 2. 前端（`app/frontend/index.html`）

#### 2.1 字典面板双 Tab
- 右侧「数据字典」面板新增 tab 条：**🌐 全局字典**（`#v2DictTabGlobal`，默认激活）/ **📋 标准字典**（`#v2DictTabStd`）；新增 `.v2-dict-tab` / `:hover` / `.active` CSS

#### 2.2 状态与加载分流
- `V2` 状态新增 `dictTab: 'global'`；`initStds()` 增加 `v2LoadDicts()`（默认加载全局字典）
- `v2LoadDicts` 按 `V2.dictTab` 分流：
  - **global**：`GET /api/v1/dictionaries`——不依赖选中标准/表/字段
  - **standard**：`GET /data-standards-v2/{stdId}/dictionaries`——仅依赖 `V2.stdId`；未选标准时提示「请先在左侧选择数据标准」
- 新增 `v2DictTab(tab)` 切换函数与 `_v2RenderDictList(list)`（badge 按维度显示「共享」/「标准」）

#### 2.3 创建与删除联动
- `v2AddDict` 按 tab 分流：global 分支 `POST /api/v1/dictionaries`（无 scope 选择）；standard 分支 `POST /{stdId}/dictionaries`（scope='standard'）
- `v2DelStd` 删除选中标准后追加 `v2LoadDicts()` 刷新字典面板；`v2SelectStd` / `v2CreateStd` 既有 `v2LoadDicts()` 调用保留

#### 2.4 关联下拉核对（无需改动）
- 字段-字典关联下拉 `v2FeAddDictLink` 已天然按拆分语义分流（全局字典从 `GET /dictionaries`、标准字典从标准端点加载），与拆分后语义吻合

### 3. 数据清洗（同批次完成）

- **发现隐身脏数据**：2 条 `scope='global'` 但 `standard_id` 非空的字典（拆分前历史 share 产生）——全局列表过滤 `standard_id IS NULL` 看不到、标准列表过滤 `scope='standard'` 也看不到，成为彻底隐身数据
- **清理全部测试标准**：删除 32 个测试用数据标准及全部关联数据（含脏字典），遗留开发库干净基线：
  `data_standard_fields 86 / data_standard_sheets 7 / standard_dictionaries 6 / standard_versions 9 / standard_fields 54 / standard_tables 33 / standard_layer_groups 39 / data_standards 32 / 孤儿质检规则 1`
- 外围记录按 `SET NULL` 解除关联但保留：`source_tables` 1、`stored_files` 5、`quality_reports` 109

---

## 验收结果

### 后端集成测试（真实 PostgreSQL + ASGITransport + admin_init_key，`test_dict_split.py` 8/8）
| # | 断言 | 结果 |
|---|------|------|
| 1 | 创建标准 201 | ✅ |
| 2 | 标准端点创建标准字典 201，`scope=standard` | ✅ |
| 3 | 标准端点传 `scope='global'` → 400 | ✅ |
| 4 | 全局端点创建全局字典 201，`scope=global`、`standard_id=None` | ✅ |
| 5 | `GET /dictionaries` 纯全局（`scope=global AND standard_id IS NULL`） | ✅ |
| 6 | 标准字典列表纯标准 | ✅ |
| 7 | share 后 `scope=global` 且 `standard_id=None` | ✅ |
| 8 | 全局 / 标准两个维度无重叠 | ✅ |
| 9 | 测试数据清理完成 | ✅ |

### 前端浏览器级验证（Playwright + Chromium）
- **冒烟（`smoke_dict.js`）**：默认激活「🌐 全局字典」tab；全局字典独立加载（不依赖选中）；未选标准时「📋 标准字典」tab 显示「请先在左侧选择数据标准」；tab 来回切换正常；**0 个 JS 控制台错误**
- **端到端（`e2e_dict.js`）**：全局 tab 创建「测试全局字典G」→ 列表显示 badge「共享」+ 编码 GLB001；未选标准提示；API 建标准 201 → 选中 → 标准 tab 创建「UI标准字典S」→ badge「标准」+ STD001；**切回全局 tab 只显示全局字典、再切标准 tab 只显示标准字典**（维度隔离）；0 JS 错误；测试数据清理完成

### 静态检查
- `py_compile` data_standard_v2.py 通过
- index.html 单块 script（165,738 chars）`node --check` 通过

---

## 遗留说明
- **历史遗留 global 脏字典**：拆分前 share 产生的 `scope='global'` 且 `standard_id` 非空的字典会被全局列表过滤隐身。本次已随测试数据清洗；存量环境如遇此类数据，执行 `UPDATE standard_dictionaries SET standard_id=NULL WHERE scope='global' AND standard_id IS NOT NULL` 即可恢复可见
- **全局字典重名保护在应用层**：DB 唯一约束 `uq_sd_standard_name` 对 NULL `standard_id` 不生效（PostgreSQL 唯一索引忽略 NULL），全局维度重名检查由 `POST /dictionaries` 预查实现
- **字典条目 `parent_code` 为纯记录字段**：无父级存在性校验 / 深度限制 / 循环检测，不参与质检层级（A5 值域按扁平 `item_code` 集合校验），Excel 导入导出不含该列。**父级关联功能暂不实施**，完整方案（自引用外键 + API 校验 + A5 父码通配 + Excel 往返 + 分期实施）已存档于 `docs/proposal_dictionary_hierarchy.md`，待需求明确后按 P1→P4 启用
- **标准 tab 未选标准时只提示不渲染**：空态提示「请先在左侧选择数据标准」，不发起请求
