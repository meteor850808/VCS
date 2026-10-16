# VCS v2.6 质检页重构 + 后端适配修订记录

## 版本信息
- **版本**: v2.6
- **日期**: 2026-07-31
- **类型**: 质检页面重构（紧凑布局）+ 后端接口适配 + 超级管理员缺陷修复
- **影响范围**: 前端 `index.html`（质检页）、质检 API、质检服务、认证上下文、开发库环境

---

## 修订目标

1. **质检页紧凑重构**: 将质检页面从占位/旧布局重构为紧凑单页工作台（文件栏 + 标准栏 + 可折叠检查配置 + 报告表），并接入真实后端接口
2. **检查项横向排列**: 属性/拓扑检查项以胶囊形式横向流式排列（flex-wrap），替代纵向堆叠
3. **字体两档放大**: 标签 13px / 表格 12px / 按钮 10-11px 的紧凑字号体系（用户两轮反馈后的最终档位）
4. **无标准纯拓扑检查**: 后端允许 `standard_id` 为空时仅执行拓扑检查（原为 400 拒绝）
5. **超级管理员可见性修复**: 修复 admin 通过 `ADMIN_INIT_KEY` 鉴权时看不到自己创建数据的问题

---

## 详细变更

### 1. 质检页前端重构（`app/frontend/index.html`）

#### 1.1 页面结构（`#page-quality`）
| 区块 | 说明 |
|------|------|
| 左文件栏 | `#qcFileList` 已上传文件列表（含要素/字段统计），`#qcFileCnt` 计数 |
| 标准栏 | `#qcStd2` 标准选择器（optgroup 分组）、选中标准 tag、无标准提示 |
| 检查配置 | 3 组可折叠面板（属性 9 项 / 拓扑 13 项 / 规则组），每组标题栏含全选/生成按钮 |
| 阈值输入 | 拓扑项带 `tol` 的 8 项渲染阈值输入框（`qc-tol-<id>`） |
| 报告区 | 6 列报告表（文件/标准/属性/拓扑/评分/时间）+ 计数 + 过滤 |
| 底栏 | 执行质检按钮、方案保存/加载/导出 |

#### 1.2 检查项定义
**拓扑 13 项**（与后端 `topology_checker.ALL_RULES` 对齐）：
`t01`空几何 · `t02`自相交 · `t03`极大面 · `t04`极小面 · `t05`微短线 · `t06`面重叠 · `t07`点重叠 · `t08`线重叠 · `t09`面裂隙 · `t10`线连通 · `t11`不规则面 · `t12`环方向 · `t13`多部件

其中 8 项带可调阈值（t03-t09、t11），默认值与后端 `TopologyConfig` 一致：
| 项 | 参数 | 默认值 |
|----|------|--------|
| t03 极大面 | `max_area` | 10000 km²（后端默认 1e9 m²） |
| t04 极小面 | `min_area` | 0.01 m² |
| t05 微短线 | `min_length` | 0.001 m |
| t06/t08 重叠 | `overlap_tolerance` | 0.0001 m |
| t07 点重叠 | `point_distance` | 0.001 m |
| t09 面裂隙 | `gap_threshold` | 0.001 m |
| t11 不规则面 | `convexity_threshold` | 0.3 |

**属性 9 项**（标准驱动，与 `attribute_checker` 对齐）：`31010001`必要图层 · `31010002`要素类型 · `31010003`属性结构 · `31010004`字段必填 · `31010005`值域控制 · `31010006`字段唯一 · `31010007`图层非空 · `31010008`小数位 · `31010009`长度限制

#### 1.3 布局与字号（`.qc-*` CSS）
- 检查项胶囊：`display:flex; flex-wrap:wrap; gap:3px 6px`，胶囊 `padding:2px 7px`
- 字号体系（两档放大后最终值）：
  - 表格/表头/表体、胶囊标签、文件项、组标题：**12px**
  - 执行按钮 11px、全选按钮/计数 10px、阈值输入 11px

#### 1.4 JS 模块（QC 区块，约 2158-2550 行）
| 函数 | 职责 |
|------|------|
| `loadStoredSel` | 加载已上传文件（`GET /api/v1/data`） |
| `loadStdsSel`（重写） | 标准选择器 optgroup 分组 + `qcStdMap` + 切页恢复选中 |
| `qcRenderCfg` | 渲染 3 组检查配置 + 计数 + 折叠态 |
| `qcTogItem / qcTogAll / qcTogGroup` | 检查项单选/全选/组折叠 |
| `buildQcTopoConfig` | 汇总拓扑开关 + 阈值 → `topology_config` |
| `doQcStored` | 执行质检（`POST /quality/check`）+ 报告刷新 |
| `qcSaveCfg / qcLoadCfg / qcExportCfg` | 方案保存/加载/导出（localStorage） |
| `loadQcStdTree / qcTogTable` | 标准结构树（layer_groups 嵌套） |
| `qcGenRules` | 从标准生成规则库 |
| `initQcPage` | 页面初始化（`goPage('quality')` 调用） |

#### 1.5 交互细节
- 属性组全选需先选标准（提示「属性检查由标准驱动」）
- 无标准 + 无检查项时执行 → 提示「未选择标准且未启用任何检查项」
- 选中标准后顶部显示标准 tag（可关闭），无标准时显示提示文案

### 2. 后端：无标准纯拓扑检查

#### 2.1 `app/api/v1/quality.py`
- `POST /quality/check`：`standard_id` 为空时不再返回 400，改为 `logger.info` 记录后跳过标准相关逻辑
- 补充 `logging` 引入与模块级 `logger`

#### 2.2 `app/services/quality_checker.py`
- `run_quality_check()` 的 `standard_id` 参数类型改为 `str | None`
- 为空时 `attr_results=[]`、`attr_summary` 返回零值（total=0/failed=0/passed=0），仅执行拓扑检查

### 3. 超级管理员可见性修复（`app/core/auth_context.py`）

#### 3.1 问题
- `create_super_admin()` 使用虚拟 ApiKey id `00000000-0000-0000-0000-000000000001`
- `scope_clause()` / `owns()` 按 `api_key_id IN (accessible_key_ids)` 过滤，而 `accessible_key_ids` 只含真实活跃 Key → 管理员创建的数据（api_key_id=虚拟 id）被自身查询过滤

#### 3.2 修复
- `AuthContext.__init__` 新增 `is_super_admin: bool = False`
- `scope_clause()`：超级管理员返回 `True`（不过滤，可访问所有数据）
- `owns()`：超级管理员返回 `True`
- `create_super_admin()` 传入 `is_super_admin=True`

#### 3.3 影响
- 全部依赖 `scope_clause()/owns()` 的路由统一受益（data / quality / quality_rules / data_standard_v2 / source_data / spatial / convert / batch）
- 超级管理员现在可见：所有 Key 数据 + 自身虚拟 ID 创建的数据 + `api_key_id IS NULL` 记录

### 4. 环境修复（开发库，非代码）

- `data_standards.sheet_count` 列（NOT NULL 无默认值）与代码模型不一致 → `POST /data-standards-v2` 500
- 临时修复：`ALTER TABLE data_standards ALTER COLUMN sheet_count SET DEFAULT 0`
- 背景：alembic 停在 `0004_add_key_group`，存在历史手动 DDL，详见 `docs/environment-notes.md`

---

## 文件变更清单

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/frontend/index.html` | `#page-quality` 紧凑重构 + `.qc-*` CSS + QC JS 模块重写（13 拓扑项/9 属性项/执行/方案/树） |
| `app/api/v1/quality.py` | `standard_id` 可空（纯拓扑检查）+ logging |
| `app/services/quality_checker.py` | `run_quality_check` 支持 `standard_id=None` |
| `app/core/auth_context.py` | `is_super_admin` 标记 + scope/owns 放行 |

### 新增文件
| 文件 | 说明 |
|------|------|
| `docs/revision_v2_6.md` | 本文档 |
| `docs/environment-notes.md` | 环境差异记录（alembic 版本、sheet_count 修复、AuthContext 修复、清理记录） |

### 参考 mockup（未纳入主程序）
| 文件 | 说明 |
|------|------|
| `docs/qc_redesign_v5.html` | 用户批准的紧凑布局 mockup（横向胶囊 + 两档字体） |
| `docs/qc_page_mockup*.html` | 早期 v1-v4 迭代稿 |

---

## E2E 验证（2026-07-31，Playwright + Chrome）

### 页面功能（全部通过）
| 断言 | 结果 |
|------|------|
| 文件列表加载 | 46 个文件，badge=46 ✅ |
| 标准选择器 | 3 个 optgroup（custom/auto/测试），16 个选项 ✅ |
| 检查配置渲染 | 3 组：属性 9 项 / 拓扑 13 项 / 规则组，阈值输入 8 个 ✅ |
| 报告表 | 6 列、page_size=50 行 ✅ |
| 标准联动 | 选标准 → tag 亮起、无标准提示隐藏 ✅ |
| 全选/取消切换 | 22 项已选（属性 9 + 拓扑 13）✅ |
| 无标准纯拓扑质检 | toast「检查完成：0 个问题」，生成报告 ✅ |
| 带标准质检 | toast「检查完成：0 个问题」，生成报告 ✅ |
| 前端校验 | 无标准且无检查项 →「未选择标准且未启用任何检查项」✅ |
| JS 错误 / 404 | 0 个 ✅ |

### 布局计算样式（getComputedStyle）
- 检查项容器：`display:flex; flex-wrap:wrap; gap:3px 6px`，胶囊 `padding:2px 7px` ✅
- 表格/表头/表体 12px、执行按钮 11px、全选按钮 10px、胶囊标签 12px ✅

### 后端接口（curl）
- 无标准 + 拓扑：HTTP 200，`attr_summary.total=0`，拓扑项通过，阈值生效 ✅
- 带标准：HTTP 200，属性检查执行（无表定义 → 失败项）✅

### 数据清理
- 删除 E2E 测试报告 4 条（`ul3.json`：207d6245 / ebbc3a05 / 038dc5a8 / 3cc8df17，级联清理 rule_results/violations）
- 删除 E2E 测试标准 1 条（`c646a993`「E2E-验证标准」）
- 清理后 smoke：报告表 50 行、14 条标准、页面正常 ✅
- 当前库：46 文件 / 14 标准 / 72 报告（历史数据保留）

---

## 备注

- 报告删除暂无 API 端点，清理走 SQL；`quality_rule_results` / `quality_violations` 对 `quality_reports` 为 `ON DELETE CASCADE`
- 前端报告列表使用 `GET /api/v1/quality?page_size=50`（上限 50），库中报告多于 50 时仅显示最新 50 条
- 环境差异（alembic 版本落后、手动 DDL）属开发库已知问题，未纳入本次代码迁移，跟踪见 `docs/environment-notes.md`
