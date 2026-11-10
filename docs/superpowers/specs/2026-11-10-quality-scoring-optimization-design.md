# VCS 质检评分口径优化 — 设计方案（路线一：要素级为源）

> **状态**: 待批准（v3，已通过一次独立审查，两项待定已由项目方决断）
> **日期**: 2026-11-10
> **范围**: `空间质检/` 的质检评分语义、报告展示口径、相关文档与产物清理。
> **决策记录**: 口径路线=要素级为源、维度分为派生；T06–T10 降为诊断项不计分；历史报告不重算；落地顺序=低风险层先行（P0→P4）。
> **基线版本**: v2.24.0 → 目标 v2.25.0

---

## 1. 背景与现状

以下每条均为代码可查事实。v1 文档中 F6、F7、F10、F13 只写了一半或写错，本版已按实际代码更正，并在第 16 节列出修订明细。

| # | 事实 | 证据 |
|---|------|------|
| F1 | 维度分按「检查条目通过率」计算，不是按要素记录 | `quality_checker.py` 54–60 |
| F2 | 要素级失败量 `failed_count` 只进 `total_violations`，不进分数 | `quality_checker.py` 210–214 |
| F3 | 无相关结果时维度分返回 1.0（不惩罚） | `quality_checker.py` 57–58 |
| F4 | 未关联标准时属性四维恒为 1.0，综合分化简为 0.85 + 0.15×拓扑分 | `quality_checker.py` 163–167、71–73、75–82 |
| F5 | 接口层只拒绝 `feature_count <= 0`，未关联标准是显式允许的 | `quality.py` 113–143；`README.md` 720 |
| F6 | 结构类三项的 fail 分支既没写 `failed_count` 也没写 `total_checked`，两者都取默认 0；只补分母会让缺失图层、类型错误、结构不符一律算满分 | `attribute_checker.py` 260–266、298–304、327–333、86 |
| F7 | 拓扑多层问题：T06/T07/T08 的 `failed_count` 是**要素对数**分母却是要素数，且查询带 `LIMIT 1000`；T09 的分子是**裂隙个数**；T10 的分子是**悬挂端点数**；T11 的分母与分析范围都截断在 5000 | `topology_checker.py` 535–571、578–619、626–662、694–700、744–747、771–781、784、812、819 |
| F8 | 文件页读 `completeness_score`，详情页读 `score_overall` | `app.js` 1355–1361、2267 |
| F9 | 列表项 `issues_count` = 两个结果列表长度之和，含通过项 | `quality.py` 771、206–207 |
| F10 | 上传路径把属性检查项总数写入 `total_features`，该写法存在两处 | `quality.py` 321；`data.py` 2488 |
| F11 | 文档仍写第五维为「及时性」，代码为 `topology` | `README.md` 717；`BASELINE.md` 551；`quality_checker.py` 34–40 |
| F12 | 已删除模块的字节码残留在 `__pycache__` | `app/services/quality/__pycache__/scoring.cpython-31*.pyc`；提交 d3541b1 |
| F13 | 评分阈值散落在前后端共六处：前端四处、后端两处 | `app.js` 1137、1356、2268、2274；`quality.py` 581–584；`reporter.py` 10–14 |
| F14 | 报告外键 `stored_file_id` 为 `ON DELETE SET NULL` | `quality_report.py` 49–52 |
| F15 | `quality_daily_snapshots` 在 `app/`、`scripts/`、`tools/` 下均无写入点 | 全仓检索无命中 |
| F16 | HTML 报告端点无条件用五维重算综合分，不读已存的 `score_overall`；Excel 导出把 `score_completeness` 当综合分打印 | `quality.py` 573–579；`quality_exporter.py` 269–271 |
| F17 | 写 `score_*` 的路径有三处，且前端质检工作台走的是 `data.py` 那条 | `quality.py` 192–221、316–336；`data.py` 2483–2508；`app.js` 1131、1390 |
| F18 | 测试库是 PostgreSQL 的 `vcs_test`，建表走 `create_all`，仓库内无任何测试调用 alembic | `tests/conftest.py` 21–22、47；`scripts/create_quality_tables.py` 1 |

## 2. 目标

1. 让维度分成为可复核的派生值：由要素级比率聚合，任何读者都能从结果明细复算出维度分。
2. 让「未检查」与「检查通过」在数据和界面上都不可混淆，包括规则执行异常的情况。
3. 让同一份报告在任一界面（前端列表、前端详情、服务端 HTML、Excel 导出）显示同一个总分与同一个判定。
4. 让不同报告的百分比具备可比性：分母口径写进数据，而不是藏在代码里。

## 3. 非目标

1. 本轮不重算历史报告。F14 决定了原文件清理后无法重跑拓扑。
2. 本轮不改拓扑与属性规则的判定算法（阈值、空间谓词、取样上限都不动），只改分母、分子口径与计分方式。T06–T10 的分子单位错配不用改算法解决，改用「排除出维度分」解决，见 7.2。
3. 本轮不恢复 `quality_daily_snapshots` 的写入与趋势图（F15）。
4. 本轮不改 WebSocket 推送协议。
5. 本轮不新建结果明细表，结果仍存于现有 JSON 列。
6. 本轮不改 `quality.py` 398–419 的旧兼容分支，它只写 `completeness_score`，不产五维分。

## 4. 反目标（即使技术实现成功也算失败）

1. 任何文件在「没有执行任何检查」或「检查执行失败」的情况下得到高于 0 的综合分。
2. 同一份报告在同一界面上出现两个不同的总分。
3. 跨口径（v2 与 v3）的数值被直接平均或画在同一趋势线上。
4. 分母口径只能从代码推断，无法从报告数据读出。

## 5. 约束

1. 写 `score_*` 的路径有三处，必须同时改：`quality.py` 192–221、`quality.py` 316–336、`data.py` 2483–2508。漏掉第三处会让工作台产出的 v3 分数被默认值标成 `v2`。
2. 迁移必须接在 `0013_cad_enhancement` 之后，采用仓库既有风格（`op.add_column` / `op.alter_column` + `server_default`）。测试库是 PostgreSQL（F18），迁移验证只能在 PG 上做。
3. 仓库工作流要求每次变更同步 `BASELINE.md`、`CONTEXT.md`、`docs/revision_vX_Y.md`（`CLAUDE.md` 9–17）。
4. 现有契约测试不可删除，只能参数化：`tests/test_quality_checker_p6a.py` 77–96。
5. `WEIGHTS` 是权重的唯一来源（`quality_checker.py` 34–40），v3 不得另立第二份权重表。

## 6. 考虑过的三条路线

| 路线 | 做法 | 代价 | 结论 |
|------|------|------|------|
| 一 | 要素级为唯一源；维度分改为 `1 - Σfailed/Σtotal_checked`；条目通过率降为诊断字段 | 同一组数值列承载两种口径，跨口径统计靠 `scoring_profile` 过滤 | **选定** |
| 二 | 保留 `score_*`，另加 `score_*_f` 双列并存，逐条对账 | 列数翻倍、三处写入、前端选列 | 否决：对账能力可用一个发版的临时双写替代 |
| 三 | 只补「未评价」与口径元数据，不改聚合方式 | 问一那类问题原样保留 | 否决：下一轮要重开同一议题 |

## 7. 口径 v3 定义

### 7.1 结果字段

每条检查结果保留现有字段，并新增两项语义：

- `total_checked`：参与判定的单位数，即分母。取值为 0 时该结果视为未评价。
- `denominator_scope`：`full` | `sampled` | `truncated` | `none`。`truncated` 表示分母被采样上限截断，`sampled` 表示分子被上限截断。

分子与分母必须同单位。下表列出每类规则的单位，以及 v3 的处置：

| 规则 | 现状分子 | 现状分母 | v3 处置 |
|------|----------|----------|---------|
| 31010001 必要图层 | 未写（默认 0） | 未写 | 补：分子=缺失图层数，分母=标准要求图层数 |
| 31010002 要素类型 | 未写（默认 0） | 未写 | 补：每图层 1 个单位，分子=类型不符的图层数 |
| 31010003 属性结构 | 未写（默认 0） | 未写 | 补：每图层 1 个单位，分子=结构不符的图层数 |
| 31010004 必填 | 要素×字段 | 记录数 | 不变 |
| 31010005 值域 | 要素×字段 | 非空记录数 | 不变 |
| 31010006 唯一 | 要素 | 记录数 | 不变 |
| 31010007 必要图层不为空 | 未写 | 未写 | **不计分**，见 7.2 |
| 31010008 小数位 | 要素×字段 | 有小数位的记录数 | **不计分**，见 7.2 |
| 31010009 长度 | 要素×字段 | 记录数 | **不计分**，见 7.2 |
| T01 空几何 | 要素 | 未写 | 补：分母=参与检查的要素总数 |
| T02–T05 | 要素 | 要素数 | 不变 |
| T06 面重叠 | **要素对数**，且截断 1000 | 要素数 | **排除出维度分**，降为诊断项，见 7.2 |
| T07 点重叠 | **要素对数**，且截断 1000 | 要素数 | **排除出维度分**，降为诊断项，见 7.2 |
| T08 线重叠 | **要素对数**，且截断 1000 | 要素数 | **排除出维度分**，降为诊断项，见 7.2 |
| T09 面裂隙 | **裂隙个数** | 要素数 | **排除出维度分**，降为诊断项，见 7.2 |
| T10 线连通性 | **悬挂端点数** | 线要素数 | **排除出维度分**，降为诊断项，见 7.2 |
| T11 不规则面 | 要素 | 截断 5000 | 分母改为实际分析行数，标 `truncated` |
| T12 环方向 | 要素 | 要素数 | 不变 |
| T13 多部件 | 要素 | 要素数 | 不变 |

### 7.2 维度归集

v3 **不改变** v2 的维度归集，以免把口径改造变成权重改造：

- `completeness` ← 31010004
- `accuracy` ← 31010005
- `consistency` ← 31010001、31010002、31010003
- `uniqueness` ← 31010006
- `topology` ← T01–T05、T11–T13（共 8 条，单位均为要素）

T06、T07、T08、T09、T10 这五项**不计入拓扑维**，只作诊断项。原因是它们的分子单位与分母不同（依次为要素对数、要素对数、要素对数、裂隙个数、悬挂端点数），直接套用 7.3 的聚合式会产生负比率，并被 clamp 抹成 0 分。把它们排除后，拓扑维只由单位=要素的 8 条规则聚合，口径自洽。

这五项的处置边界（本项目已确认采纳的方案 a）：

- 规则照常执行、照常出现在结果列表和报告里，`status` 仍为 `pass` / `fail`。
- 它们不写 `total_checked`、不参与 `rate(topology)` 的分子分母累加。
- 结果上标 `scoring_role: "diagnostic"`，与计分规则区分。若 `scoring_role` 字段成本过高，最低要求是其 `rule_code` 出现在一份单一常量清单 `DIAGNOSTIC_RULES` 中，且该清单被单测锁住。
- 这属于「排除」，不属「不跑」，因此这五项仍会出现在报告正文中；本方案不据此判断外部系统是否需要缓冲期，该判断见 14.2 第 2 项。

31010007、31010008、31010009 在 v2 与 v3 中都是「跑但不计分」的诊断项，本方案不改变这一点（现状见 `quality_checker.py` 62–73 的归集表）。报告需显式标注这三项为诊断项，避免 P4 文档自相矛盾。

### 7.3 维度分

对维度 D，取属于 D 且 `total_checked > 0` 的结果集合 R。

- 若 R 为空 → D 标记为 `not_evaluated`，不参与加权。
- 否则 `rate(D) = clamp(1 - Σfailed_count / Σtotal_checked, 0, 1)`。

### 7.4 综合分

- 记已评价维度集合为 E。若 E 为空 → `score_overall = null`。
- 否则 `w'(D) = WEIGHTS[D] / Σ_{E} WEIGHTS[D]`，`score_overall = Σ_{E} rate(D) × w'(D)`。
- 权重仍只来自 `quality_checker.WEIGHTS`，v3 只做归一化，不新增常量。

### 7.5 执行异常

规则执行异常现在产出 `status="error"`、分子分母均为 0（`topology_checker.py` 464、500、575、623、666、713）。v3 的处置：

1. 该结果写 `status="not_evaluated"`、`denominator_scope="none"`，并进入报告新增的 `evaluation_errors` 列表。
2. 只要存在任一 `evaluation_errors`，该报告的 `verdict` 强制为 `not_evaluated`，即使综合分不为空。
3. 这条规则的目的：异常不得被当作「未评价」静默剔除，否则规则报错反而抬高分数，违反反目标 1。

### 7.6 判定

- 阈值配置化：`SCORING_PASS_THRESHOLD`（默认 0.85）、`SCORING_WARN_THRESHOLD`（默认 0.60）。
- `verdict`：`score_overall` 为 null 或存在 `evaluation_errors` → `not_evaluated`；否则按阈值给出 `pass` / `warn` / `fail`。
- `verdict` 由后端派生，不落库。

### 7.7 诊断字段

`rule_pass_rate`：现有条目通过率，保留在响应中供诊断，不再进入综合分。

## 8. 分阶段落地（低风险先行）

### P0 零语义修复（不改任何数值）

1. `README.md` 717 的「及时性(0.15)」改为「拓扑(0.15)」；`BASELINE.md` 551 的括注同步。
2. 删除 `app/services/quality/__pycache__/scoring.cpython-31*.pyc`，确认 `.gitignore` 已忽略 `__pycache__`。

### P1 展示层统一（仍不改评分公式）

1. `DimensionScores` 增加 `evaluated: dict[str, bool]` 与 `rule_pass_rate`，`overall` 允许 `null`。
2. 响应增加 `verdict`、`scoring_profile`、`violation_count`（只计 `status == "fail"`）、`evaluation_errors`。
3. `QualityReportBrief` 增加 `score_overall`、`verdict`、`scoring_profile`；`issues_count` 保留一个发版期并标 deprecated（F9）。
4. 前端新增单一渲染函数 `renderScore(report)`：读 `score_overall`，为 null 显示「未评价」，颜色由 `verdict` 决定。替换 `app.js` 1137、1356–1361、1393、2267–2276。
5. 删除 `app.js` 1135 对 `completeness_score` 的兜底分支（F8）。
6. 服务端 HTML 报告端点改用 `score_overall` + `verdict`，删除 `quality.py` 573–579 的无条件重算与 581–584 的阈值（F16）。
7. Excel 导出改读 `score_overall`，不再把 `score_completeness` 当综合分（F16）。
8. 修正 `quality.py` 321 与 `data.py` 2488：`total_features` 改回要素数，检查项数另放字段（F10）。

### P2 口径元数据与分母补齐（语义变更点 A，只降不升）

1. 迁移 `0014_scoring_profile`：
   - `quality_reports` 增加 `scoring_profile`（`String(8)`，`NOT NULL`，`server_default 'v2'`）、`score_evaluated`（`JSON`，`nullable`）、`evaluation_errors`（`JSON`，`nullable`）。
   - 五列 `score_completeness`/`score_accuracy`/`score_consistency`/`score_uniqueness`/`score_topology` 改为 `nullable=True`，`downgrade` 时把 NULL 回填 0.0 再改回 `NOT NULL`。
2. 结果写入 `denominator_scope`；分母为 0 或执行异常的结果写 `status="not_evaluated"`。
3. 补齐结构三项与 T01 的分子分母（F6、F7 中 T01 部分）。
4. **本阶段不启用权重归一化**：未评价维度暂时按 0 计入并显式列出，避免一致性维度在 P2 就整维消失、到 P3 又带着真实分母回来，导致同一文件连续两个版本分数跳动两次。

### P3 聚合切换与权重归一化（语义变更点 B）

1. `compute_scores` 改为 7.3 / 7.4 的聚合式；`rule_pass_rate` 作为诊断字段并列返回。
2. 启用权重归一化，未评价维度真正剔出加权。
3. T11 分母改为实际分析行数并标 `truncated`（F7）。
4. 定义 `DIAGNOSTIC_RULES = {"T06","T07","T08","T09","T10","31010007","31010008","31010009"}` 并接入 `compute_scores`：命中的规则不计入任何维度分，也不写 `total_checked`（见 7.2）。
5. 三处写入路径同时切换（`quality.py` 192–221、`quality.py` 316–336、`data.py` 2483–2508），统一写 `scoring_profile = 'v3'`。
6. 落实 7.5 的异常处置与 `verdict` 强制降级。

### P4 文档与发版

1. `BASELINE.md` §3/§4/§5/§15 更新；`CONTEXT.md` 版本改 2.25.0；新增 `docs/revision_v2_25_0.md`。
2. 发版说明必须写明三件事：v3 与 v2 不可直接比较；一致性维度因补分母可能下降；P2 与 P3 两个版本之间的分数会再变动一次。

## 9. 接口与前端变更清单

| 位置 | 变更 |
|------|------|
| `app/schemas/quality.py` `DimensionScores` | +`evaluated`、+`rule_pass_rate`，`overall` 可空 |
| `app/schemas/quality.py` `QualityCheckResponse` | +`verdict`、+`scoring_profile`、+`violation_count`、+`evaluation_errors` |
| `app/schemas/quality.py` `QualityReportBrief` | +`score_overall`、+`verdict`、+`scoring_profile`；`issues_count` 标 deprecated |
| `app/schemas/quality.py` `QualityReportDetail` | +`scoring_profile`、+`evaluated`、+`evaluation_errors` |
| `app/models/quality_report.py` 62–72 | 五列改可空；新增三列 |
| `app/services/quality_checker.py` 45–91 | v3 聚合；新增 `DIAGNOSTIC_RULES` 常量；旧函数保留为 `compute_scores_v2` 供回退 |
| `app/services/quality/attribute_checker.py` 260–339 | 结构三项补分子分母与 `denominator_scope` |
| `app/services/topology_checker.py` 397–400、464–713、763–821 | T01 补分母；异常分支改 `not_evaluated`；T11 分母与 scope；T06–T10 不改算法，只在结果上标诊断身份 |
| `app/api/v1/quality.py` 192–221、316–336、573–584、771 | 写入三列；HTML 改读；`issues_count` 拆分 |
| `app/api/v1/data.py` 2483–2508 | 同上，第三处写入路径 |
| `app/services/quality/reporter.py` 10–14 | 删除阈值函数或改读 verdict |
| `app/services/quality_exporter.py` 269–271 | 综合分改读 `score_overall` |
| `app/frontend/app.js` 1135、1137、1356–1361、1393、2267–2276 | 统一走 `renderScore` |

## 10. 回滚

1. 环境变量 `SCORING_PROFILE`（`v2` / `v3`，默认 `v3`）决定调用哪个算分函数。
2. `compute_scores_v2` 保留至 v2.26.0。
3. 迁移 0014 的 `downgrade`：删除三列、把 NULL 回填 0.0 后恢复 `NOT NULL`。

## 11. 测试策略

1. **口径单测**（新增 `tests/test_scoring_v3.py`）：分母聚合、clamp、未评价剔除、权重归一化、全未评价返回 null、异常强制降级、`DIAGNOSTIC_RULES` 内规则不进维度分。
2. **契约测试**（改 `tests/test_quality_checker_p6a.py` 33–96）：按 `SCORING_PROFILE` 参数化断言；保留「权重和为 1」与「前端无硬编码权重」；新增「前后端源码中不出现 85/60/0.8/0.5 阈值字面量」——覆盖 `app.js`、`quality.py`、`reporter.py` 三个文件。
3. **路径覆盖**：三处写入路径各一条用例，断言 `scoring_profile`、`score_evaluated`、`evaluation_errors` 均被写入。
4. **迁移验证**：在 PG 测试库上跑 `alembic upgrade head` 与 `downgrade -1`，用 pytest marker `migration` 隔离，不引入 SQLite 路径。
5. **回归基准**：用一份固定样例文件记录 v2 与 v3 的维度分差值，作为发版说明的量化依据。

## 12. 风险与缓解

| 风险 | 后果 | 缓解 |
|------|------|------|
| 结构三项 fail 分支分子为 0 | 补分母后仍是满分 | P2.3 同时补分子，单测覆盖三类 fail |
| 异常规则被当作未评价剔除 | 报错反而抬分 | 7.5 强制 `verdict=not_evaluated` |
| 三处写入漏改 | v3 分数被标成 v2 | 非空列 + 三条路径用例 |
| 五列改可空 | 直接读列的消费者拿到 NULL | 发版说明列出受影响消费者；downgrade 回填 0.0 |
| HTML/Excel 未纳入 | 服务端报告与前端判定不一致 | P1.6、P1.7 纳入，契约测试覆盖 |
| 生产库 `alembic_version` 落后 | `upgrade head` 连带跑历史迁移 | 见 14.2 前置条件，未取得 `V_prod` 前不启动 P2 |
| 拓扑五项降为诊断 | 用户看到拓扑维数值变化 | 发版说明列出「拓扑维今后只由 8 条规则聚合」；报告把 T06–T10 单列「诊断结果」区 |
| 部分维度未评价时分数跳动 | 同一文件两次结果不同 | 响应给出 `evaluated` 清单；界面显示「已评价 N/5 维」 |

## 13. 留给实现者的决定

1. `verdict` 第三态取名（本方案用 `warn`）。
2. `score_evaluated` 的 JSON 结构（建议 `{"completeness": true, ...}`）。
3. 「口径 v2」角标的文案与位置。
4. `rule_pass_rate` 是否进入列表接口（本方案只放详情）。
5. `evaluation_errors` 的元素结构（建议 `{rule_code, message}`）。

## 14. 已决事项与前置条件

### 14.1 已由项目方确认（2026-11-10）

1. **拓扑 T06–T10 的计分处置**：采纳方案 (a)，排除出维度分、只作诊断。理由与边界见 7.2。因此本轮**不触碰**「不改规则算法」这条非目标，不需要改 T06–T10 的 SQL 聚合方式。
2. **生产库建表来源**：先确认生产库 `alembic_version` 的实际值，再决定 0014 的投递方式。这是 P2 的前置条件，不是实现细节。

### 14.2 前置条件（阻塞 P2 开工）

在写 `0014_scoring_profile` 之前，必须先取得以下两项结果。任一项缺失，P0/P1 可继续，P2 不启动。

1. 在生产库执行 `SELECT version_num FROM alembic_version;`，把结果记为 `V_prod`。
   - `V_prod = 0013_cad_enhancement` → 0014 正常接链尾。
   - `V_prod` 落后于 0013 → 先出一份补齐历史迁移的执行计划，再谈 0014；不能直接 `upgrade head`。
   - 表不存在或为空 → 生产库是用 `create_all` 或 `scripts/create_quality_tables.py` 建的，0014 需另写 `scripts/apply_0014.py` 直连 `ALTER TABLE`，并放弃本轮的迁移回滚保证。
2. 确认是否存在外部系统直接读 `quality_reports.score_*` 五列。
   - 有 → P2 的五列改可空需要缓冲期，改为「先加 `NULL` 可写、下个版本再切读写」，或干脆不改可空、未评价写 `-1` 哨兵值（需在 7.3 注明）。
   - 无 → 按 P2.1 原样执行。

### 14.3 仍需项目方回答（不阻塞 P0/P1）

1. 阈值是否应由数据标准表配置，而不是全局常量。
2. 用户是否接受拓扑维因排除 T06–T10 而发生变化（原先 13 条规则的失败量都进 `total_violations` 且影响拓扑维，今后 5 条不再影响维度分）。

## 15. 引用（源码 MLA 改写式）

- quality_checker.py. 空间质检源码库, app/services/quality_checker.py.
- quality.py. 空间质检源码库, app/api/v1/quality.py.
- data.py. 空间质检源码库, app/api/v1/data.py.
- attribute_checker.py. 空间质检源码库, app/services/quality/attribute_checker.py.
- topology_checker.py. 空间质检源码库, app/services/topology_checker.py.
- quality_report.py. 空间质检源码库, app/models/quality_report.py.
- reporter.py / quality_exporter.py. 空间质检源码库, app/services/.
- app.js. 空间质检源码库, app/frontend/app.js.
- conftest.py. 空间质检源码库, tests/conftest.py.
- README.md / BASELINE.md / CLAUDE.md. 空间质检源码库, 仓库根目录.

## 16. 修订记录

**v2（2026-11-10）**：独立审查提出 8 项，逐条对照代码核对后全部成立，据此修订如下。

| 原表述 | 核对结果 | 修订 |
|--------|----------|------|
| F6「结构类不写分母」 | 只写了一半：fail 分支连分子也没写 | F6 改写，P2.3 同时补分子 |
| F7「T11 分母截断」 | 只写了一半：T06–T10 的分子单位与分母不同，且 T06–T08 分子被 `LIMIT 1000` 截断 | F7 改写，7.1 逐条列单位，新增待定项 1 |
| F13「前端阈值是唯一判定来源」 | 不成立：后端另有两处 | F13 改写为六处，P1.6/P1.7 纳入后端 |
| F10「上传路径写错 total_features」 | 只写了一半：`data.py` 有同样写法 | F10 改写为两处 |
| 约束 1「写入路径两处」 | 不成立：共三处，第三处是前端主路径 | 约束 1 与 §9、§11.3 改为三处 |
| 约束 2「迁移需在 SQLite 下可用」 | 不成立：测试库是 PostgreSQL，仓库无 alembic 调用 | 约束 2 与 §11.4 改为 PG 迁移测试 |
| P2 先启用权重归一化 | 会让一致性维度整维消失一个发版期 | P2 只落元数据与分母，归一化移到 P3 |
| 未评价维度在 NOT NULL 列里写什么 | 未定义 | P2.1 五列改可空，downgrade 回填 0.0 |
| 7.1 列出 08/09 分母但 7.2 未归集 | 自相矛盾 | 7.1 标为「不计分诊断项」，7.2 明示不改变归集 |

**v3（2026-11-10）**：项目方对两项待定项作出决定，据此收口。

| 决定 | 影响 |
|------|------|
| T06–T10 排除出维度分、只作诊断（方案 a） | 7.1 五条改为「排除」；7.2 拓扑维收窄为 T01–T05、T11–T13 共 8 条；新增 `DIAGNOSTIC_RULES` 常量（P3.4）；非目标 2 去掉豁免；§12 新增一条风险 |
| 0014 投递方式待生产库 `alembic_version` 确认 | §14 由「待定项」改为「已决事项与前置条件」；P2 增加开工门禁：未取得 `V_prod` 不启动 |
| 原待定项 3「外部系统读 `score_*`」 | 保留为 14.2 的第二道门禁，与迁移一起决定五列是否改可空 |
| 原待定项 4「阈值是否配置化」 | 降级为 14.3，不阻塞 P0/P1 |
