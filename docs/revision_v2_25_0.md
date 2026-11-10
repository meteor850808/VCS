# v2.25.0 — 五维评分口径 v3（要素级派生）

> 日期：2026-11-10
> 版本：v2.24.0 → v2.25.0
> 范围：评分口径改造（要素级派生成唯一源）、诊断规则分层、`quality_reports` 迁移 0014。
> 设计规格见 `docs/superpowers/specs/2026-11-10-quality-scoring-optimization-design.md`，
> 实现计划见 `docs/superpowers/plans/2026-11-10-quality-scoring-optimization.md`。

## 1. 口径变更：维度分改由要素级派生

v2 口径的维度分是**条目通过率**：某一维内有哪些检查条目，就按「通过条目数 / 条目总数」算。
v3 口径改为**要素级派生比率**：

```
维度分 = 1 - Σ(failed_count) / Σ(total_checked)    # 只在被归集到该维的规则之间求和
```

- 每条规则只贡献「实际检查了多少要素」和「其中多少要素不合格」两个数，不再按条目的
  pass/fail 二值计分。
- 归集关系（唯一真源：`app/services/quality_checker.py` 的 `DIMENSION_RULES`）：

  | 维度 | 权重 | 归集规则 |
  |------|------|----------|
  | completeness | 0.25 | 31010004（必填项） |
  | accuracy | 0.25 | 31010005（属性值域控制） |
  | consistency | 0.20 | 31010001 / 31010002 / 31010003（图层、要素类型、属性结构） |
  | uniqueness | 0.15 | 31010006（主键唯一性） |
  | topology | 0.15 | T01–T05、T11–T13（共 8 条） |

- 权重数值不变，但**只在已评价的维度上重新归一化**：未评价维度不进分母，不再按 0 分拉低
  综合分。全部维度都未评价时综合分为 `NULL`，判定为 `not_evaluated`（不是 0%）。
- 条目通过率降级为诊断字段 `rule_pass_rate`（`DimensionScores.rule_pass_rate`），
  仍返回但不参与综合分。
- 某一维的所有规则**都拿不到有效分母**（`total_checked = 0`，或该维规则整轮未执行，
  如未关联标准、该几何类型无数据）时，该维为「未评价」而不是「满分」；只要该维有一条规则
  有有效分母，就用这条规则的分母参与聚合（分母为 0 的规则自动被排除，不会稀释或抬高分数）。
- 任一规则执行异常（`status="error"`）会被收集进 `evaluation_errors`，报告的判定整体降为
  `not_evaluated`——数据可信度优先于分数好看。

判定阈值（`SCORING_PASS_THRESHOLD` / `SCORING_WARN_THRESHOLD`，默认 0.85 / 0.60）与
v2 相同，但输入换成了要素级综合分。

## 2. 不可比声明：v3 与 v2 的分数不得并列比较

**v3 与 v2 的分数不可直接比较，也不可求平均、不可混入同一条趋势线。**

原因有三：

1. 分子分母的统计单位不同（要素 vs 条目），同一份数据在两种口径下会得到不同分值，且差异
   方向不固定。
2. 参与聚合的规则集合不同：v3 的拓扑维只由 8 条规则聚合，v2 由当时启用的全部拓扑条目聚合。
3. 未评价维度在 v3 里被剔除出权重分母，在 v2 里则按 0 分或满分参与。

因此：

- 历史报告**不重算**，其 `scoring_profile` 为 `v2`（存量行由迁移的列默认值标注）；
- 任何跨版本报表、看板、趋势图必须按 `scoring_profile` 分组，不能把 `v2` 与 `v3` 放在
  同一序列里排序或对比；
- 需要横向对比时，请用同一口径重跑，或只看 v3 内部的相对变化。

## 3. 受影响数值

升级后同一份数据的分数可能明显变化，属于口径切换的正常结果，不是回归缺陷：

- **一致性维（consistency）可能下降**：v2.24 及以前，31010002（要素类型）与 31010003
  （属性结构）没有可靠的 `total_checked` 分母，结构类条目的通过率与「每个图层 1 个单位」
  并不等价。v3 补齐分母后（A1 分母=标准要求图层数、A2/A3 每图层 1 个单位），一致性维会按
  实际图层规模计算，通常比 v2 偏低。
- **拓扑维只由 T01–T05 与 T11–T13 共 8 条规则聚合**。T06（面重叠）、T07（点重叠）、
  T08（线重叠）、T09（面裂隙）、T10（线连通性）连同 31010007/31010008/31010009 一起
  **降为诊断项（`DIAGNOSTIC_RULES`）**：它们照常执行、照常出现在违规清单与 `violation_count`
  里，但**不再进入任何维度分**。因此在 v2 里被 T06–T10 拉低的拓扑分，在 v3 里不再体现为
  扣分；反过来，只看拓扑分的读者会看不到这些问题，请以违规清单为准。
- **结构三项（31010001/02/03）的分母变了**：必要图层检查的分母是标准要求的图层数，失败数
  是缺失图层数；要素类型与属性结构按「每个图层 1 个单位」计。v3 的 consistency 因此反映的是
  「不合规图层占比」，不再是「不合规条目占比」。
- **空结果不再是满分**：v2 里「没有任何检查结果」会得到 1.0 满分；v3 里所有维度均未评价，
  综合分为 `NULL`、判定为 `not_evaluated`。

## 4. T11（不规则面）抽样声明

T11 原本就有分析量上限，v3 把这件事显式化：

- 当参与检查的面要素**超过 5000 个**时，只评价按查询顺序的前 **5000** 个，此时该结果的
  `total_checked` 是 5000，`denominator_scope = "truncated"`；
- 未超过 5000 个时，`total_checked` 等于实际要素数，`denominator_scope = "full"`；
- 截断会带来抽样偏差：若异常面集中在未分析的后段要素中，拓扑维会偏乐观。这 5000 个已分析
  要素的结果**照常计入拓扑维**（不是丢弃该规则），所以分数依然有意义，但它只是这 5000 个
  要素的结论；读到 `denominator_scope="truncated"` 时应把它当作「抽样结论」而非「全量结论」。

`denominator_scope` 是检查结果字段（`CheckResult` / `TopologyCheckResult`），会写进 JSON
报告，并随详情接口的 `attr_results` / `topo_results` 明细返回给调用方；前端列表与 Excel
导出的表格**不展示**该字段（Excel 只展示综合分与违规明细），需要判断分母口径请直接读
JSON 报告或接口明细。

## 5. 回滚方式

口径开关是环境变量 `SCORING_PROFILE`（对应 `app/config.py: settings.scoring_profile`）：

- `SCORING_PROFILE=v3`（默认）：要素级派生口径；
- `SCORING_PROFILE=v2`：回退到 v2 的条目通过率口径（`compute_scores_v2`，原实现原样保留）。

回滚注意事项：

1. 回滚只影响**新产生**的报告，历史报告按写入时的 `scoring_profile` 原样展示；
2. ORM 与 v3 新增的读路径（`verdict` / `score_evaluated` / `evaluation_errors` /
   `rule_pass_rate` / `denominator_scope`）在 v2 下依然可用：v2 结果不带 `evaluated`
   等元数据时取默认值，判定按 v2 综合分计算；
3. 数据库迁移 `alembic/versions/0014_scoring_profile.py` 是 v3 的前置条件，但不阻塞回滚：
   它只做「加三列 + 六列放宽为可空」（五维 `score_*` 与旧单维 `completeness_score`），
   v2 写入非空分值同样合法。
4. 若线上库的 `alembic_version` 已被人工改动（链脱管），仓库链仍为
   `0014_scoring_profile ← 0013_cad_enhancement`，线上可改用幂等直连脚本
   `scripts/apply_0014.py`（支持 `--dry-run`，不触碰 `alembic_version`）。该脚本与 v3
   上线必须同步执行，否则**写入与读取都会直接报错**（已在 PostgreSQL 上实测，行为是
   「失败」而非「静默降级」）：
   - 三列未加时，读写都报 `42703 undefined_column`（`scoring_profile` 字段不存在）——
     报告详情/列表接口会 500；
   - 三列已加但六列未放宽时，报 `23502 not_null_violation`；列上的服务端默认值
     （`DEFAULT 0.0`）**不会**兜住，因为 ORM 对显式 `None` 会发出 `NULL`，而服务端默认
     只在列被省略时生效；
   - 唯一会「静默改写」的情形是 ORM 列上带 Python 侧 `default`（修复前 `completeness_score`
     的 `default=1.0` 会把未评价写成 `1.0`），本次已一并移除。
