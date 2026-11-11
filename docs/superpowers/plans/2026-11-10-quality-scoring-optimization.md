# 质检评分口径 v3 优化 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `空间质检/` 的五维评分从「检查条目通过率」切换为「要素级派生比率」，并让同一份报告在前端列表、前端详情、服务端 HTML、Excel 导出四处显示同一个总分与同一个判定。

**Architecture:** 口径 v3 只改「分母从哪来、分子怎么累加、未评价怎么加权」三层，不改任何空间谓词与阈值。结果对象新增 `denominator_scope` 描述分母口径；`compute_scores` 改为按 `DIMENSION_RULES` 归集后逐维 `1 - Σfailed/Σtotal_checked`，未评价维度剔出加权并按 `WEIGHTS` 重新归一化；`verdict` 由后端单点派生，前端只消费不判定。落库侧新增 `scoring_profile`（`v2`/`v3`）与 `score_evaluated`，让「这份报告的分数是哪种口径」成为可查询的数据而不是代码推断。

**Tech Stack:** Python 3.13 / FastAPI / Pydantic v2 / SQLAlchemy 2 async / PostgreSQL+PostGIS / Alembic / 原生 JS 前端（无构建步骤）/ pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-11-10-quality-scoring-optimization-design.md`

## Global Constraints

- 权重唯一来源是 `app/services/quality_checker.py` 的 `WEIGHTS`（`completeness 0.25 / accuracy 0.25 / consistency 0.20 / uniqueness 0.15 / topology 0.15`）。任何任务都不得新增第二份权重表。
- 维度归集在 v3 中保持不变：`completeness←31010004`、`accuracy←31010005`、`consistency←31010001/02/03`、`uniqueness←31010006`、`topology←T01–T05 与 T11–T13`。
- `DIAGNOSTIC_RULES = {"31010007","31010008","31010009","T06","T07","T08","T09","T10"}`，这 8 条执行但不计分。
- 写 `score_*` 的路径共三处，缺一不可：`app/api/v1/quality.py:192`、`app/api/v1/quality.py:316`、`app/api/v1/data.py:2483`。
- 历史报告不重算、不回填数值。`score_overall IS NULL` 且 `scoring_profile = 'v2'` 的行必须继续按 `_overall_from_scores` 兜底显示，不得显示「未评价」。
- 阈值只允许出现在 `app/config.py`：`scoring_pass_threshold = 0.85`、`scoring_warn_threshold = 0.60`。前端与 HTML 模板不得出现 `85/60/0.85/0.6` 判定字面量。
- 迁移接在 `0013_cad_enhancement` 之后，`revision = "0014_scoring_profile"`，`down_revision = "0013_cad_enhancement"`。
- 现有测试只可参数化或改名，不可删除断言：`tests/test_quality_checker_p6a.py::TestComputeScores` 的 6 个用例、`tests/test_tail_misc_p6e.py` 的 `_score_color` 用例。
- 计划中的偏差决定（与设计文档 §8 / §11.4 不同，已核对代码后调整）：P2 **不改任何数值**，只加元数据与分母；`status="not_evaluated"` 的写入推到 P3 `compute_scores_v3` 生效时；迁移验证改为静态测试 + 生产同构库手工演练，不引入 alembic 测试夹具。

## Review Focus

以下五类输入计划中的测试未必覆盖，但一旦出错用户会直接看见错分数。每条都在标注的任务里落地成一个测试。

1. **未关联标准的文件**（`standard_id=None`，`attr_results` 为空）：四个属性维全部未评价，综合分只能由拓扑维归一化得出；既不能是 `0.0`（判死）也不能是 `1.0`（虚高）。→ Task 11
2. **规则执行异常**（`status="error"`，分子分母均为 0）：该结果不得被静默剔出，报告的 `verdict` 必须强制 `not_evaluated`，即使其他维全是满分。→ Task 11
3. **历史 v2 报告**（`score_overall IS NULL`）：详情页与 HTML 报告必须走 `_overall_from_scores` 兜底，显示数值而不是「未评价」。→ Task 5
4. **分母为 0 但状态是 pass 的结果**（如单表模式下 A1）：该维未评价，不是满分 `1.0`。→ Task 11
5. **要素数超过 5000 的大文件**（T11）：`denominator_scope` 必须是 `truncated`，分母是实际分析行数，界面标注该规则只按前 5000 个要素评价。→ Task 10

---

## 文件结构

**新增**

| 文件 | 职责 |
|------|------|
| `alembic/versions/0014_scoring_profile.py` | 加三列 + 五列改可空 |
| `tests/test_scoring_v3.py` | v3 聚合式、`derive_verdict`、schema 默认值的单测 |
| `tests/test_scoring_contract_v3.py` | 源码扫描契约（阈值字面量、`scoreView` 存在性、`DIAGNOSTIC_RULES`、文档名称、三处写入） |
| `tests/test_migration_0014.py` | 迁移文件与 ORM 模型的静态一致性 |

**修改**

| 文件 | 改动范围 |
|------|----------|
| `app/config.py` | +3 项设置 |
| `app/models/quality_report.py` | +3 列、5 列改可空 |
| `app/schemas/quality.py` | `DimensionScores` / `QualityCheckResponse` / `QualityReportBrief` / `QualityReportDetail` / 两个 item 模型加 `denominator_scope` |
| `app/services/quality_checker.py` | `DIMENSION_RULES`、`DIAGNOSTIC_RULES`、`derive_verdict`、`compute_scores_v2`、`compute_scores_v3`、`compute_scores` 分发、`run_quality_check` 返回值 |
| `app/services/quality/attribute_checker.py` | A1/A2/A3 分子分母；`CheckResult.denominator_scope` |
| `app/services/topology_checker.py` | T01 分母；T11 `truncated`；`TopologyCheckResult.denominator_scope`；`_make_result` 显式参数 |
| `app/api/v1/quality.py` | 两处写入、`_resolve_overall`、详情端点、HTML 端点、列表端点 |
| `app/api/v1/data.py` | 第三处写入 |
| `app/services/quality_exporter.py` | 综合分改读 `score_overall` |
| `app/frontend/app.js` | `scoreView` / `dimCard`，替换 4 处判定 |
| `README.md`、`BASELINE.md`、`CONTEXT.md`、`docs/revision_v2_25_0.md` | 名称、口径说明、发版记录 |

`app/services/quality/reporter.py` **不改**。它渲染的是旧 `QualityResult`（单一 `completeness_score`），只被旧接口与 `tests/test_tail_misc_p6e.py` 使用，与五维报告无关；改它会把 `_score_color` 的既有用例一并推翻，收益为零。

---

### Task 1: P0 — 修正第五维名称与清理字节码残留

> **状态**：complete（commit `d1fa6cb..28a71f9`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `README.md:717`
- Modify: `BASELINE.md:551`
- Delete: `app/services/quality/__pycache__/scoring.cpython-31*.pyc`
- Test: `tests/test_scoring_contract_v3.py`

**Interfaces:**
- Consumes: 无
- Produces: 无（纯内容修正）

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_scoring_contract_v3.py`，内容为：

```python
"""评分口径 v3 的源码契约（不需要数据库）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_docs_call_fifth_dimension_topology():
    """第五维在文档中必须叫「拓扑」，不得残留「及时性」。"""
    assert "及时性" not in _src("README.md")
    assert "及时性" not in _src("BASELINE.md")
    assert "拓扑(0.15)" in _src("README.md")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_contract_v3.py -v`
Expected: FAIL — `及时性` 在 README.md 与 BASELINE.md 中各出现 1 次

- [ ] **Step 3: 改两处文档** — `README.md:717` 的 `+ 及时性(0.15)` 改为 `+ 拓扑(0.15)`；`BASELINE.md:551` 的 `| 拓扑(及时性) |` 改为 `| 拓扑 |`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_scoring_contract_v3.py -v`
Expected: PASS

- [ ] **Step 5: 清理残留字节码并确认忽略规则**

Run:
```
Get-ChildItem app/services/quality/__pycache__ -Filter "scoring.cpython-31*.pyc" | Remove-Item -Force
Select-String -Path .gitignore -Pattern '__pycache__'
```
Expected: 命令无输出（文件已不存在），`.gitignore` 命中一行 `__pycache__/`

- [ ] **Step 6: 提交**

```bash
git add README.md BASELINE.md tests/test_scoring_contract_v3.py
git commit -m "docs: 第五维名称由「及时性」更正为「拓扑」"
```

---

### Task 2: P1 — 阈值与判定收敛到 config + derive_verdict

> **状态**：complete（commit `28a71f9..e9cf603`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/config.py`（在 `sandbox_limit: int = 1000` 之后追加）
- Modify: `app/services/quality_checker.py`（`WEIGHTS` 之后追加常量与函数）
- Test: `tests/test_scoring_v3.py`

**Interfaces:**
- Produces:
  - `settings.scoring_pass_threshold: float = 0.85`
  - `settings.scoring_warn_threshold: float = 0.60`
  - `settings.scoring_profile: str = "v3"`
  - `quality_checker.derive_verdict(overall: float | None, evaluation_errors: list[dict] | None = None) -> str`，返回 `"pass" | "warn" | "fail" | "not_evaluated"`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_scoring_v3.py`，先只写判定部分：

```python
"""口径 v3 的算分与判定单测（纯函数）。"""
import pytest

from app.services import quality_checker as qc


class TestDeriveVerdict:
    def test_thresholds(self):
        assert qc.derive_verdict(0.95) == "pass"
        assert qc.derive_verdict(0.85) == "pass"      # 边界含等号
        assert qc.derive_verdict(0.70) == "warn"
        assert qc.derive_verdict(0.60) == "warn"      # 边界含等号
        assert qc.derive_verdict(0.59) == "fail"
        assert qc.derive_verdict(0.0) == "fail"

    def test_none_overall_is_not_evaluated(self):
        assert qc.derive_verdict(None) == "not_evaluated"

    def test_error_forces_not_evaluated(self):
        errs = [{"rule_code": "T03", "rule_name": "极大面", "message": "boom"}]
        assert qc.derive_verdict(0.99, errs) == "not_evaluated"

    def test_empty_error_list_does_not_downgrade(self):
        assert qc.derive_verdict(0.99, []) == "pass"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_v3.py -v`
Expected: FAIL — `AttributeError: module 'app.services.quality_checker' has no attribute 'derive_verdict'`

- [ ] **Step 3: 实现**

`app/config.py` 追加三项设置（沿用文件既有的 `name: type = default  # 中文注释` 风格）：

```python
    # ── 质检评分判定（v2.25.0；阈值唯一来源，前端/HTML 不得硬编码）──
    scoring_pass_threshold: float = 0.85
    scoring_warn_threshold: float = 0.60
    scoring_profile: str = "v3"      # v2 | v3，回滚开关
```

`app/services/quality_checker.py` 在 `WEIGHTS` 之后追加（注意顶部已 `from app.config import settings` 吗？——没有，需在文件 import 区加 `from app.config import settings`）：

```python
def derive_verdict(
    overall: float | None,
    evaluation_errors: list[dict] | None = None,
) -> str:
    """由综合分派生判定。阈值来自 settings，不在本函数内写字面量。"""
```

函数体按三条规则实现：`overall is None` → `"not_evaluated"`；`evaluation_errors` 非空 → `"not_evaluated"`；否则与两个阈值比较给出 `pass`/`warn`/`fail`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_scoring_v3.py -v`
Expected: PASS（4 个用例）

- [ ] **Step 5: 提交**

```bash
git add app/config.py app/services/quality_checker.py tests/test_scoring_v3.py
git commit -m "feat(quality): 判定阈值收敛到 config 并新增 derive_verdict"
```

---

### Task 3: P1 — 扩展响应模型（可为空与会话诊断字段）

> **状态**：complete（commit `e9cf603..5f60985`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/schemas/quality.py:39-65`（两个 item 模型）、`:77-84`（`DimensionScores`）、`:100-122`（`QualityCheckResponse`）、`:125-152`（`QualityReportBrief`）、`:155-196`（`QualityReportDetail`）
- Test: `tests/test_scoring_v3.py`

**Interfaces:**
- Consumes: `derive_verdict`（Task 2）
- Produces:
  - `DimensionScores`: 五个维度字段类型改为 `float | None`，默认 `None`；`overall: float | None`；新增 `evaluated: dict[str, bool] = {}`、`rule_pass_rate: float = 1.0`
  - `CheckItemResult` / `TopoCheckItemResult`: 新增 `denominator_scope: str = "full"`
  - `QualityCheckResponse`: 新增 `verdict: str = "not_evaluated"`、`scoring_profile: str = "v2"`、`violation_count: int = 0`、`evaluation_errors: list[dict] = []`
  - `QualityReportBrief`: 新增 `score_overall: float | None = None`、`verdict: str = "not_evaluated"`、`scoring_profile: str = "v2"`、`violation_count: int = 0`；`issues_count` 的注释改为 `# deprecated: 旧语义=结果条目数；新语义见 violation_count`
  - `QualityReportDetail`: `score_overall` 改 `float | None = None`；新增 `scoring_profile: str = "v2"`、`evaluated: dict[str, bool] = {}`、`evaluation_errors: list[dict] = []`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_scoring_v3.py`:

```python
from app.schemas.quality import DimensionScores, QualityReportBrief


class TestSchemaV3:
    def test_dimension_scores_allow_none(self):
        s = DimensionScores()
        assert s.overall is None
        assert s.completeness is None
        assert s.evaluated == {}
        assert s.rule_pass_rate == 1.0

    def test_dimension_scores_still_accept_floats(self):
        s = DimensionScores(completeness=0.5, overall=0.5)
        assert s.completeness == 0.5 and s.overall == 0.5

    def test_brief_has_score_overall_and_verdict(self):
        b = QualityReportBrief(
            id="r1", file_name="a.geojson", detected_format="stored_file",
            completeness_score=0.0, total_features=1, total_fields=0,
            created_at="2026-11-10T00:00:00Z",
        )
        assert b.score_overall is None
        assert b.verdict == "not_evaluated"
        assert b.violation_count == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_v3.py -v -k SchemaV3`
Expected: FAIL — `assert 0.0 is None`

- [ ] **Step 3: 改 `app/schemas/quality.py`**

按 Interfaces 列出的字段逐项修改。`DimensionScores` 不论是否可空都保留 `ge=0, le=1` 约束（`float | None = Field(None, ge=0, le=1)`）。

- [ ] **Step 4: 跑测试确认通过，并跑既有 schema 相关用例**

Run: `python -m pytest tests/test_scoring_v3.py tests/test_schemas_ws.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/schemas/quality.py tests/test_scoring_v3.py
git commit -m "feat(schema): 五维分可空并新增 verdict/evaluated/violation_count"
```

---

### Task 4: P1 — 修正 total_features 与列表语义、补响应派生字段

> **状态**：complete（commit `5f60985..dab3ef8`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/services/quality_checker.py:216-224`（返回值）、`:140-154`（空文件守卫返回值）
- Modify: `app/api/v1/quality.py:192-242`、`:316-362`、`:763-791`
- Modify: `app/api/v1/data.py:2483-2534`
- Test: `tests/test_scoring_p1_writes.py`（新建）、`tests/test_quality_checker_p6a.py`

**Interfaces:**
- Consumes: `derive_verdict`（Task 2）、schema 新字段（Task 3）
- Produces:
  - `run_quality_check(...)` 返回值新增两个键：`"feature_count": int`、`"violation_count": int`（`status == "fail"` 的结果条目数，不是 `failed_count` 之和）
  - 三处写入路径的 `total_features` 一律取 `check_result["feature_count"]`
  - 列表端点 `issues_count` 语义改为「不通过条目数」，与 `violation_count` 同值

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_scoring_p1_writes.py`：

```python
"""P1：三处写入路径的 total_features 与列表语义（真实 vcs_test）。"""
import json
from unittest.mock import AsyncMock

import pytest

from app.services import quality_checker as qc
from app.services.topology_checker import TopologyCheckResult


@pytest.fixture
def _stored_file(db_session):
    ...
```

测试主体两条用例，都 monkeypatch `qc.run_attribute_checks` 返回 3 条检查项，``StoredFile.feature_count = 7``：

1. `test_feature_count_not_check_item_count`：调 `qc.run_quality_check(db_session, "std-1", sf.id, "key1")`，断言 `res["feature_count"] == 7` 且 `res["attr_summary"]["total"] == 3`（两者必须不等）。
2. `test_violation_count_counts_failed_entries`：attr 返回 1 条 fail（`failed_count=5`）+ 2 条 pass，topo 返回 1 条 fail（`failed_count=9`），断言 `res["violation_count"] == 2` 且 `res["total_violations"] == 14`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_p1_writes.py -v`
Expected: FAIL — `KeyError: 'feature_count'`

- [ ] **Step 3: 实现**

`run_quality_check` 的两处 `return` 都补 `"feature_count"`（正常路径取 Phase 0.5 查到的 `_fc` 或用 `sum` 折算的 `feature_count`；空守卫路径取 `0`）与 `"violation_count"`（正常路径为 `sum(1 for r in attr_results + topo_results if r.status == "fail")`，空守卫为 `0`）。

`app/api/v1/quality.py:321` 与 `app/api/v1/data.py:2488` 的 `total_features=check_result["attr_summary"].get("total", 0)` 改为 `total_features=check_result["feature_count"]`。

三处响应构造补 `verdict=derive_verdict(scores["overall"])`、`scoring_profile="v2"`（P3 改为读 `scores.get("profile")`）、`violation_count=check_result["violation_count"]`。

列表端点 `issues_count=` 改为 `sum(1 for r in (r.constraint_violations or []) + (r.topology_violations or []) if isinstance(r, dict) and r.get("status") == "fail")`，并补 `violation_count=` 同值、`score_overall=_resolve_overall(r)`（Task 5 提供；本任务暂用 `r.score_overall`）、`verdict=derive_verdict(...)`、`scoring_profile="v2"`。

- [ ] **Step 4: 跑测试确认通过，并确认既有编排测试未被破坏**

Run: `python -m pytest tests/test_scoring_p1_writes.py tests/test_quality_checker_p6a.py -v`
Expected: PASS（含 p6a 全部既有用例）

- [ ] **Step 5: 提交**

```bash
git add app/services/quality_checker.py app/api/v1/quality.py app/api/v1/data.py tests/test_scoring_p1_writes.py
git commit -m "fix(quality): total_features 取要素数，issues_count 只统计不通过条目"
```

---

### Task 5: P1 — 后端读路径统一（详情 + HTML 报告）

> **状态**：complete（commit `dab3ef8..a8808ee`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/api/v1/quality.py:60-72`（`_overall_from_scores` 之后新增 `_resolve_overall`）
- Modify: `app/api/v1/quality.py:512-553`（详情端点）
- Modify: `app/api/v1/quality.py:573-584`、`:600-607`、`:703-710`（HTML 端点）
- Test: `tests/test_scoring_p1_read.py`（新建）

**Interfaces:**
- Consumes: `derive_verdict`（Task 2）
- Produces: `quality._resolve_overall(report: QualityReport) -> float | None`
  - `report.score_overall` 非空 → `round(report.score_overall, 4)`
  - `report.score_overall` 为空且 `getattr(report, "scoring_profile", "v2") == "v2"` → 用五列调用 `_overall_from_scores`
  - 否则 `None`
- Produces: `quality._verdict_color(verdict: str) -> str`，返回 `#27ae60` / `#f39c12` / `#e74c3c` / `#7f8c8d`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_scoring_p1_read.py`，三条用例直接调 `_resolve_overall` 与 `_verdict_color`，构造轻量 `SimpleNamespace` 冒充 report：

1. `test_legacy_row_recomputes`：`score_overall=None`、五列 1.0 → 返回 `1.0`
2. `test_v3_row_without_overall_stays_none`：`score_overall=None`、`scoring_profile="v3"` → 返回 `None`
3. `test_verdict_color_has_no_threshold_literal`：断言四个输入各自返回上表的值（颜色映射集中在一处，源码扫描见 Task 7）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_p1_read.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_overall'`

- [ ] **Step 3: 实现两个辅助函数并替换两处调用点**

详情端点 546-552 的 `score_overall=_overall_from_scores(...) if report.score_overall is None else round(...)` 整段替换为 `score_overall=_resolve_overall(report)`，并补 `verdict=derive_verdict(_resolve_overall(report))`、`scoring_profile=getattr(report, "scoring_profile", "v2")`。

HTML 端点：删除 573-579 的无条件重算，改为

```python
    overall = _resolve_overall(report)          # float | None，0..1 量纲
    verdict = derive_verdict(overall)
```

删除 `_sc` 定义（581-584），`_dim_card` 与综合分格子改用 `_verdict_color`。`overall is None` 时环形图中心文案显示 `—`、标签显示「未评价」，不得出现 `0%`。

- [ ] **Step 4: 跑测试确认通过，并确认 HTML 端点既有用例未破**

Run: `python -m pytest tests/test_scoring_p1_read.py tests/test_quality_routes.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/v1/quality.py tests/test_scoring_p1_read.py
git commit -m "fix(quality): HTML 报告改读 score_overall 并按 verdict 着色"
```

---

### Task 6: P1 — Excel 导出的综合分改读 score_overall

> **状态**：complete（commit `a8808ee..ed3e008`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/services/quality_exporter.py:269-273`
- Test: `tests/test_quality_exporter.py`（追加两条用例）

**Interfaces:**
- Consumes: 无（只读 `report.score_overall`）
- Produces: A2 单元格文本为 `综合评分: {pct}` 或 `综合评分: 未评价`

- [ ] **Step 1: 写失败测试** — 在 `tests/test_quality_exporter.py` 追加：

```python
def test_excel_uses_score_overall(tmp_path):
    report = <现有测试同款 SimpleNamespace，score_overall=0.91, score_completeness=0.12>
    ...
    assert "91%" in wb_cell_text


def test_excel_shows_not_evaluated_when_none(tmp_path):
    report = <score_overall=None>
    ...
    assert "未评价" in wb_cell_text
```

`wb_cell_text` 用 `openpyxl.load_workbook(out_path)["违规清单"]["A2"].value` 取。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_quality_exporter.py -v -k excel_uses`
Expected: FAIL — A2 含 `12%`（旧行为读 `score_completeness`）

- [ ] **Step 3: 实现**

```python
    overall = getattr(report, "score_overall", None)
    score_txt = f"{overall:.0%}" if overall is not None else "未评价"
    ws['A2'] = f"综合评分: {score_txt}  违规总数: {...}"
```

`违规总数` 的表达式同时改为只统计 `status == "fail"` 的条目（与 Task 4 的 `violation_count` 同义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_quality_exporter.py -v`
Expected: PASS（含既有 286-370 全部用例）

- [ ] **Step 5: 提交**

```bash
git add app/services/quality_exporter.py tests/test_quality_exporter.py
git commit -m "fix(export): Excel 综合分改读 score_overall 并标注未评价"
```

---

### Task 7: P1 — 前端统一渲染与清除阈值字面量

> **状态**：complete（commit `ed3e008..0bbd028`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/frontend/app.js:1135-1139`、`:1356-1362`、`:1393-1394`、`:2267-2276`
- Test: `tests/test_scoring_contract_v3.py`（追加）

**Interfaces:**
- Produces（`app.js` 内全局函数，供后述五处调用）：
  - `function scoreView(r)` → `{text: string, color: string}`，读 `r.score_overall` 与 `r.verdict`；`score_overall == null` 时 `text="未评价"`、`color="var(--t2)"`；否则 `text=(score_overall*100).toFixed(1)+"%"`、颜色由 `verdict` 映射
  - `function dimCard(label, val, evaluated)` → HTML 字符串，`evaluated === false` 时显示 `—` 与「未评价」而不是 `0%`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_scoring_contract_v3.py`：

```python
def test_frontend_has_single_score_renderer():
    src = _src("app/frontend/app.js")
    assert "function scoreView(" in src
    assert "function dimCard(" in src
    assert src.count("scoreView(") >= 4      # 定义处 + 至少三处调用
    assert "未评价" in src


def test_frontend_has_no_threshold_literals():
    src = _src("app/frontend/app.js")
    for lit in (">=.85", ">=.6", ">=85", ">=60"):
        assert lit not in src, f"前端残留阈值字面量: {lit}"


def test_frontend_has_no_completeness_fallback():
    src = _src("app/frontend/app.js")
    assert "d.completeness_score" not in src
    assert "r.completeness_score" not in src


def test_backend_html_has_no_threshold_literals():
    src = _src("app/api/v1/quality.py")
    assert ">= 85" not in src and ">= 60" not in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_contract_v3.py -v`
Expected: FAIL — 4 条新用例全部失败

- [ ] **Step 3: 实现**

在 `app.js` 顶部工具函数区（`function esc(` 附近）新增 `scoreView` 与 `dimCard`。然后逐处替换：

- `1135-1139`（文件列表行内质检）：`const v=scoreView(d); el.innerHTML=...v.text...v.color...`，删除 `completeness_score` 兜底
- `1356-1362`（报告卡）：`const v=scoreView(r)`，`qsb` 的 class 由 `verdict` 映射为 `hi/mid/lo`（`not_evaluated` → `mid`），百分比文案取 `v.text`，`issues_count` 文案保留但改为读 `r.violation_count ?? r.issues_count`
- `1393-1394`（范围质检状态文本）：`const v=scoreView(d); st.textContent=\`✓ 完成 ${v.text}（${sel.length} 图层）\``
- `2267-2276`（详情面板）：`const v=scoreView(r)` 用于综合分；五个维度卡改用 `dimCard(label, r.score_x, r.evaluated&&r.evaluated.x!==false)`

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_scoring_contract_v3.py tests/test_quality_checker_p6a.py -v`
Expected: PASS（含 p6a 的 `test_frontend_no_hardcoded_weights`）

- [ ] **Step 5: 手工校验前端无语法错误**

Run: `node --check app/frontend/app.js`
Expected: 无输出（退出码 0）

- [ ] **Step 6: 提交**

```bash
git add app/frontend/app.js tests/test_scoring_contract_v3.py
git commit -m "refactor(frontend): 评分渲染收敛到 scoreView/dimCard，移除 85/60 阈值"
```

---

### Task 8: P2 — 迁移 0014 与 ORM 模型

> **状态**：complete（commit `0bbd028..90cd713`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Create: `alembic/versions/0014_scoring_profile.py`
- Modify: `app/models/quality_report.py:62-72`
- Test: `tests/test_migration_0014.py`

**Interfaces:**
- Consumes: 无
- Produces: ORM 新增 `scoring_profile: str`、`score_evaluated: dict | None`、`evaluation_errors: list | None`；五列 `score_*` 变为 `Mapped[float | None]`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_migration_0014.py`：

```python
"""0014 迁移与 ORM 模型的静态一致性（不连数据库）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0014_scoring_profile.py"


def test_revision_chain():
    src = MIG.read_text(encoding="utf-8")
    assert 'revision = "0014_scoring_profile"' in src
    assert 'down_revision = "0013_cad_enhancement"' in src


def test_upgrade_adds_three_columns():
    src = MIG.read_text(encoding="utf-8")
    for col in ("scoring_profile", "score_evaluated", "evaluation_errors"):
        assert f'op.add_column("quality_reports"' in src
        assert col in src


def test_upgrade_relaxes_five_columns():
    src = MIG.read_text(encoding="utf-8")
    for col in ("score_completeness", "score_accuracy", "score_consistency",
                "score_uniqueness", "score_topology"):
        assert f'"{col}", nullable=True' in src


def test_downgrade_is_symmetric():
    src = MIG.read_text(encoding="utf-8")
    down = src.split("def downgrade")[1]
    for col in ("scoring_profile", "score_evaluated", "evaluation_errors"):
        assert col in down
    assert down.count("nullable=False") == 5


def test_orm_model_matches():
    from app.models.quality_report import QualityReport
    cols = QualityReport.__table__.c
    assert cols.scoring_profile.nullable is False
    assert cols.score_evaluated.nullable is True
    assert cols.evaluation_errors.nullable is True
    for name in ("score_completeness", "score_accuracy", "score_consistency",
                 "score_uniqueness", "score_topology"):
        assert cols[name].nullable is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_migration_0014.py -v`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 写迁移与模型**

迁移 `upgrade()`：三个 `op.add_column("quality_reports", sa.Column(...))`——`scoring_profile` 用 `sa.String(8), nullable=False, server_default="v2"`；`score_evaluated` 与 `evaluation_errors` 用 `postgresql.JSONB(), nullable=True`。随后五个 `op.alter_column("quality_reports", "<col>", existing_type=sa.Float(), nullable=True)`。

`downgrade()`：先用 `op.execute("UPDATE quality_reports SET score_completeness = COALESCE(score_completeness, 0.0)")`（五个列各一条），再五个 `op.alter_column(..., nullable=False)`，最后三个 `op.drop_column`。顺序与 `upgrade` 相反。

`app/models/quality_report.py`：五个 `score_*` 改 `Mapped[float | None] = mapped_column(Float, nullable=True)`（去掉 `default=0.0`）；新增

```python
    scoring_profile: Mapped[str] = mapped_column(
        String(8), nullable=False, default="v2", server_default="v2",
        comment="口径版本：v2=条目通过率，v3=要素级派生",
    )
    score_evaluated: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="{维度: 是否已评价}")
    evaluation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="规则执行异常清单")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_migration_0014.py -v`
Expected: PASS（5 个用例）

- [ ] **Step 5: 在生产同构库演练迁移（唯一需要人工确认的一步）**

先取得生产库真实版本（设计文档 §14.2 门禁）：

```sql
SELECT version_num FROM alembic_version;
```

- 若为 `0013_cad_enhancement`：在预发库执行 `alembic upgrade head`，确认只跑 0014；再 `alembic downgrade -1` 确认可回滚；再 `upgrade head`。
- 若落后于 0013：**停止本任务后续步骤**，先出补齐历史迁移的执行计划，不能直接 `upgrade head`。
- 若表为空或不存在：0014 不走 alembic，改用 `scripts/apply_0014.py` 直连 `ALTER TABLE`，并接受「本轮无迁移回滚保证」。

- [ ] **Step 6: 提交**

```bash
git add alembic/versions/0014_scoring_profile.py app/models/quality_report.py tests/test_migration_0014.py
git commit -m "feat(db): 0014 新增 scoring_profile/score_evaluated/evaluation_errors 并放宽五列"
```

---

### Task 9: P2 — 结果级 denominator_scope

> **状态**：complete（commit `90cd713..5da332c`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/services/quality/attribute_checker.py:76-90`（`CheckResult`）
- Modify: `app/services/topology_checker.py:59-73`（`TopologyCheckResult`）、`:515-540`（`_make_result`）
- Modify: `app/services/quality_checker.py:227-256`（两个 dict 转换）
- Test: `tests/test_scoring_v3.py`

**Interfaces:**
- Consumes: `CheckItemResult.denominator_scope`（Task 3）
- Produces:
  - `CheckResult.denominator_scope: str = "full"`、`TopologyCheckResult.denominator_scope: str = "full"`
  - `_make_result(..., denominator_scope: str = "full")` 显式形参（**必须新增形参**：现有 `**kwargs` 会把值塞进 `detail` 而不是字段）
  - `_check_to_dict` / `_topo_to_dict` 输出 `"denominator_scope"` 键

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_scoring_v3.py`:

```python
from app.services.quality.attribute_checker import CheckResult
from app.services.topology_checker import TopologyCheckResult, _make_result


class TestDenominatorScope:
    def test_default_is_full(self):
        assert CheckResult("31010004", "r", "pass", "error").denominator_scope == "full"
        assert TopologyCheckResult("T01", "空几何", "pass", "error").denominator_scope == "full"

    def test_make_result_forwards_scope_as_field(self):
        r = _make_result("irregular_polygon", status="pass", total_checked=6000,
                         denominator_scope="truncated")
        assert r.denominator_scope == "truncated"
        assert r.detail == {}          # 不得落进 detail

    def test_dict_converters_emit_scope(self):
        assert qc._check_to_dict(CheckResult("x", "r", "pass", "error"))["denominator_scope"] == "full"
        assert qc._topo_to_dict(TopologyCheckResult("T01", "空几何", "pass", "error"))["denominator_scope"] == "full"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_v3.py -v -k DenominatorScope`
Expected: FAIL — `AttributeError: 'CheckResult' object has no attribute 'denominator_scope'`

- [ ] **Step 3: 实现**

两个 dataclass 各加一个带默认值的字段（放在 `total_checked` 之后、`message` 之前）。`_make_result` 新增形参 `denominator_scope: str = "full"` 并传给构造函数。两个 dict 转换各加一行。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_scoring_v3.py tests/test_topology_checker.py -v`
Expected: PASS（`test_make_result_*` 既有用例仍通过）

- [ ] **Step 5: 提交**

```bash
git add app/services/quality/attribute_checker.py app/services/topology_checker.py app/services/quality_checker.py tests/test_scoring_v3.py
git commit -m "feat(quality): 检查结果新增 denominator_scope 描述分母口径"
```

---

### Task 10: P2 — 补结构三项与 T01 的分子分母、标记 T11 截断

> **状态**：complete（commit `5da332c..238c067`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/services/quality/attribute_checker.py:250-272`（A1）、`:293-310`（A2）、`:316-339`（A3）
- Modify: `app/services/topology_checker.py:384-400`（T01）、`:763-821`（T11）
- Test: `tests/test_attribute_checker.py`、`tests/test_topology_checker.py`

**Interfaces:**
- Consumes: `denominator_scope`（Task 9）
- Produces: A1 分子=缺失图层数、分母=标准要求图层数；A2/A3 每图层 1 个单位；T01 分母=参与检查的要素总数；T11 分母=实际分析行数且 `denominator_scope="truncated"`（当总行数 > 5000）

- [ ] **Step 1: 写失败测试**

`tests/test_attribute_checker.py::test_multi_table_all_fail` 追加 6 行断言（该测试已构造「2 个标准表、缺 1 个图层、A2/A3 均不符」）：

```python
        # v3 分母
        assert by_code["31010001"].total_checked == 2   # 标准要求 2 个图层
        assert by_code["31010001"].failed_count == 1    # 缺 1 个
        assert by_code["31010002"].total_checked == 1   # 每图层 1 单位
        assert by_code["31010002"].failed_count == 1
        assert by_code["31010003"].total_checked == 1
        assert by_code["31010003"].failed_count == 1
```

`tests/test_attribute_checker.py::test_multi_table_all_pass` 追加：

```python
        assert by_code["31010001"].total_checked == 1
        assert by_code["31010002"].total_checked == 1
        assert by_code["31010003"].total_checked == 1
```

`tests/test_topology_checker.py::test_t1_empty_geometry_fail` 追加：

```python
        assert r.total_checked == 9   # count( 处理器每个几何类型返回 3 → 3 类型 × 3
```

`tests/test_topology_checker.py` 新增 `test_t11_marks_truncated_over_5000`：`_Rows` 造 5001 行，断言 `r.total_checked == 5000` 且 `r.denominator_scope == "truncated"`；再加 `test_t11_scope_full_under_limit` 断言 3 行时 `denominator_scope == "full"`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_attribute_checker.py tests/test_topology_checker.py -v -k "all_fail or all_pass or t1_empty or t11_"`
Expected: FAIL — `assert 0 == 2`

- [ ] **Step 3: 实现**

`attribute_checker.py`：

- 单表模式 A1 pass 分支：`total_checked=len(expected_table_names), failed_count=0`
- A1 fail 分支：`failed_count=len(missing_layers), total_checked=len(expected_table_names)`
- A1 pass 分支：`total_checked=len(expected_table_names)`
- A2 fail 分支：`failed_count=1, total_checked=1`；pass 分支：`total_checked=1`
- A3 fail 分支：`failed_count=1, total_checked=1`；pass 分支：`total_checked=1`
- 31010007（A7）保持不动（诊断项）

`topology_checker.py` T01：在累加 `total_empty` 的同一循环里累加 `total_rows`，查询形状必须是 `select(func.count()).select_from(model).where(...)` 以保证既有 `FakeTopologyDB` 的 `("count(", ...)` handler 命中；`_make_result` 补 `total_checked=total_rows`。

`topology_checker.py` T11：把 `total_checked=min(len(rows), 5000)` 改为 `total_checked=len(rows[:5000])`，并传 `denominator_scope="truncated" if len(rows) > 5000 else "full"`（fail 与 pass 两个分支都要传）。

- [ ] **Step 4: 跑测试确认通过（整个拓扑文件，防止 FakeTopologyDB 未处理查询断言）**

Run: `python -m pytest tests/test_attribute_checker.py tests/test_topology_checker.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/quality/attribute_checker.py app/services/topology_checker.py tests/test_attribute_checker.py tests/test_topology_checker.py
git commit -m "feat(quality): 结构三项与 T01 补分母，T11 标记截断口径"
```

---

### Task 11: P3 — 切换 v3 聚合、诊断规则与 profile 落库

> **状态**：complete（commit `238c067..303aaca`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `app/services/quality_checker.py:45-91`（`compute_scores` → v2/v3）
- Modify: `app/api/v1/quality.py:192-242`、`:316-362`
- Modify: `app/api/v1/data.py:2483-2508`
- Modify: `tests/test_quality_checker_p6a.py:33-96`
- Test: `tests/test_scoring_v3.py`

**Interfaces:**
- Consumes: 全部前序任务
- Produces:
  - `DIMENSION_RULES: dict[str, frozenset[str]]`（5 键，与 `WEIGHTS` 同键集）
  - `DIAGNOSTIC_RULES: frozenset[str]`（8 个代码）
  - `compute_scores_v2(attr_results, topo_results) -> dict`（原实现原样搬入，键集不变）
  - `compute_scores_v3(attr_results, topo_results) -> dict`，返回 `{5 维: float|None, "overall": float|None, "evaluated": dict[str,bool], "rule_pass_rate": float, "evaluation_errors": list[dict], "profile": "v3"}`
  - `compute_scores(attr_results, topo_results) -> dict` 按 `settings.scoring_profile` 分发

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_scoring_v3.py`:

```python
def _a(code, status="pass", failed=0, total=10, **kw):
    return CheckResult(code, f"r-{code}", status, "error",
                       failed_count=failed, total_checked=total, **kw)


def _t(code, status="pass", failed=0, total=10, **kw):
    return TopologyCheckResult(code, f"r-{code}", status, "error",
                               failed_count=failed, total_checked=total, **kw)


class TestComputeScoresV3:
    def test_element_level_not_entry_level(self):
        """同一维度内 1 条 fail 但失败 1/10 → 该维 0.9，而不是 0.0。"""
        s = qc.compute_scores_v3([_a("31010004", "fail", 1, 10)], [])
        assert s["completeness"] == 0.9

    def test_sums_across_rules_in_dimension(self):
        """consistency 由三条规则累加：Σfailed/Σtotal = 3/30 → 0.9。"""
        res = [_a("31010001", "fail", 1, 10), _a("31010002", "fail", 1, 10),
               _a("31010003", "fail", 1, 10)]
        assert qc.compute_scores_v3(res, [])["consistency"] == 0.9

    def test_clamp_never_negative(self):
        s = qc.compute_scores_v3([], [_t("T01", "fail", 30, 10)])
        assert s["topology"] == 0.0

    def test_zero_denominator_is_not_evaluated(self):
        s = qc.compute_scores_v3([_a("31010004", "pass", 0, 0)], [])
        assert s["completeness"] is None
        assert s["evaluated"]["completeness"] is False

    def test_diagnostic_rules_do_not_score(self):
        res = [_a("31010007", "fail", 5, 5), _a("31010008", "fail", 5, 5),
               _a("31010009", "fail", 5, 5)]
        s = qc.compute_scores_v3(res, [_t("T06", "fail", 900, 100)])
        assert all(s[d] is None for d in qc.WEIGHTS)
        assert s["overall"] is None

    def test_weights_renormalized_over_evaluated_only(self):
        """只有 completeness 已评价 → overall 等于该维，而不是乘 0.25。"""
        s = qc.compute_scores_v3([_a("31010004", "fail", 1, 4)], [])
        assert s["overall"] == 0.75

    def test_no_standard_only_topology(self):
        """未关联标准：attr 为空，拓扑 0.9 → overall 0.9（归一化后）。"""
        s = qc.compute_scores_v3([], [_t("T01", "fail", 1, 10)])
        assert s["overall"] == 0.9
        assert s["evaluated"]["topology"] is True
        assert s["evaluated"]["completeness"] is False

    def test_error_forces_not_evaluated_verdict(self):
        s = qc.compute_scores_v3([_a("31010004", "pass", 0, 10)],
                                 [_t("T03", "error", 0, 0)])
        assert s["evaluation_errors"][0]["rule_code"] == "T03"
        assert qc.derive_verdict(s["overall"], s["evaluation_errors"]) == "not_evaluated"

    def test_rule_pass_rate_is_diagnostic(self):
        """条目通过率只看状态：3 pass + 1 fail → 0.75，与要素量无关。"""
        res = [_a("31010004"), _a("31010004"), _a("31010004"),
               _a("31010005", "fail", 1, 100)]
        assert qc.compute_scores_v3(res, [])["rule_pass_rate"] == 0.75

    def test_profile_switch(self):
        """settings.scoring_profile 决定分发目标。"""
        from app.config import settings
        old = settings.scoring_profile
        try:
            settings.scoring_profile = "v2"
            assert "evaluated" not in qc.compute_scores([_a("31010004")], [])
            settings.scoring_profile = "v3"
            assert qc.compute_scores([_a("31010004")], [])["profile"] == "v3"
        finally:
            settings.scoring_profile = old

    def test_dimension_rules_keys_match_weights(self):
        assert set(qc.DIMENSION_RULES) == set(qc.WEIGHTS)

    def test_diagnostic_rules_exact(self):
        assert qc.DIAGNOSTIC_RULES == frozenset({
            "31010007", "31010008", "31010009",
            "T06", "T07", "T08", "T09", "T10",
        })
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_v3.py -v -k ComputeScoresV3`
Expected: FAIL — `AttributeError: ... no attribute 'compute_scores_v3'`

- [ ] **Step 3: 实现**

把现有 `compute_scores` 函数体整体改名为 `compute_scores_v2`（签名与返回键集完全不变），新增：

```python
DIMENSION_RULES: dict[str, frozenset[str]] = {
    "completeness": frozenset({"31010004"}),
    "accuracy": frozenset({"31010005"}),
    "consistency": frozenset({"31010001", "31010002", "31010003"}),
    "uniqueness": frozenset({"31010006"}),
    "topology": frozenset({"T01", "T02", "T03", "T04", "T05", "T11", "T12", "T13"}),
}
DIAGNOSTIC_RULES: frozenset[str] = frozenset({...})
```

`compute_scores_v3` 按此顺序实现（算法是决策，照写）：① `results = list(attr) + list(topo)`；② `errors = [{"rule_code","rule_name","message"} for r in results if r.status == "error"]`；③ 逐维取 `relevant = [r for r in results if r.rule_code in DIMENSION_RULES[dim] and r.status in ("pass","fail") and r.total_checked > 0]`；为空则该维 `None` 且 `evaluated[dim]=False`，否则 `rate = round(min(1.0, max(0.0, 1 - sum(failed)/sum(total))), 4)`；④ 已评价维度按 `WEIGHTS[d] / sum(WEIGHTS[e] for e in 已评价)` 归一化加权求和得 `overall`（四舍五入 4 位），全未评价则 `None`；⑤ `rule_pass_rate = round(passed / (passed + failed), 4)`，分母为 0 时取 `1.0`；⑥ 返回。

`compute_scores` 分发函数三行即可。

三处写入补 `scoring_profile=scores.get("profile", "v2")`、`score_evaluated=scores.get("evaluated")`、`evaluation_errors=scores.get("evaluation_errors") or None`；`QualityCheckResponse` 的 `scores=` 改为显式构造 `DimensionScores(completeness=..., ..., overall=..., evaluated=scores.get("evaluated", {}), rule_pass_rate=scores.get("rule_pass_rate", 1.0))`（`DimensionScores(**scores)` 在 Pydantic v2 下会静默丢掉多余键，导致 `evaluated` 为空）。

`tests/test_quality_checker_p6a.py`：`class TestComputeScores` 改名为 `class TestComputeScoresV2`，6 个用例内的 `qc.compute_scores(...)` 一律改调 `qc.compute_scores_v2(...)`，断言值一个不改。追加一个分发用例：

```python
    def test_default_dispatch_is_v3(self):
        assert qc.compute_scores([], [])["profile"] == "v3"
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `python -m pytest -q`
Expected: PASS，无失败。若 `tests/test_quality_routes.py` / `tests/test_data_crud_quality.py` 有数值断言失败，逐个核对是新口径的预期结果，还是 `DIMENSION_RULES` 归集错了——不要为了让测试通过而放宽断言。

- [ ] **Step 5: 提交**

```bash
git add app/services/quality_checker.py app/api/v1/quality.py app/api/v1/data.py tests/test_quality_checker_p6a.py tests/test_scoring_v3.py
git commit -m "feat(quality)!: 切换到要素级评分口径 v3，T06-T10 降为诊断项"
```

---

### Task 12: P4 — 文档与发版

> **状态**：complete（commit `303aaca..3e9b40c`）— 完成证据以 `.superpowers/sdd/2026-11-10-quality-scoring-optimization/progress.md` ledger 为准；下方步骤复选框保留初始计划原貌，不逐条勾选。

**Files:**
- Modify: `BASELINE.md`（§3/§4/§5/§15：五列可空、三新列、口径说明）
- Modify: `CONTEXT.md`（版本号 → `2.25.0`）
- Create: `docs/revision_v2_25_0.md`
- Test: `tests/test_scoring_contract_v3.py`（追加）

**Interfaces:**
- Consumes: 全部前序任务的实际行为
- Produces: 发版说明、修订记录

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_scoring_contract_v3.py`:

```python
def test_release_note_exists_and_states_incompatibility():
    note = _src("docs/revision_v2_25_0.md")
    assert "v3" in note and "v2" in note
    assert "不可" in note          # 不可直接比较
    assert "T06" in note           # 诊断项变化必须写明
    assert "5000" in note          # T11 截断口径必须写明


def test_context_version_bumped():
    assert "2.25.0" in _src("CONTEXT.md")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_scoring_contract_v3.py -v`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 写文档**

`docs/revision_v2_25_0.md` 必须包含五节：口径变更（维度分改要素级派生）、不可比声明（v3 与 v2 不得并列比较或求平均）、受影响数值（一致性维因补分母可能下降；拓扑维今后只由 T01–T05 与 T11–T13 共 8 条规则聚合，T06–T10 降为诊断项仍出结果）、T11 抽样声明（超过 5000 个要素时按前 5000 个评价，`denominator_scope=truncated`）、回滚方式（`SCORING_PROFILE=v2`）。

`BASELINE.md`：五列标注改为 `NULLABLE (v2.25.0)`，新增三列行，`score_topology` 描述保持「拓扑」。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_scoring_contract_v3.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add BASELINE.md CONTEXT.md docs/revision_v2_25_0.md tests/test_scoring_contract_v3.py
git commit -m "docs: v2.25.0 口径 v3 发版说明与基线更新"
```

---

## 自审记录

**规格覆盖**：设计文档 §7.1 每行规则对应 Task 10（结构三项、T01、T11）与 Task 11（诊断项、聚合式）；§7.2 归集对应 Task 11 `DIMENSION_RULES`；§7.3/7.4/7.5/7.6 对应 Task 11 与 Task 2；§8 P0/P1/P2/P3/P4 对应 Task 1/2-7/8-10/11/12；§9 变更清单逐行落到 Task 3/4/5/6/7/8/9/11；§11 测试策略落到 `test_scoring_v3.py`、`test_scoring_contract_v3.py`、`test_migration_0014.py`、`test_scoring_p1_writes.py`、`test_scoring_p1_read.py`；§10 回滚对应 `settings.scoring_profile` 与 Task 8 downgrade。

**与设计文档的三处偏差**（均为核对代码后的修正，已在 Global Constraints 记录）：P2 改为零数值变更、`not_evaluated` 写入推后到 P3、迁移验证改为静态测试 + 手工演练。另有一处收窄：`reporter.py` 不在改造范围（它渲染旧 `QualityResult`，与五维报告无关）。

**类型一致性**：`derive_verdict(overall, evaluation_errors)` 在 Task 2 定义，Task 5/11 调用签名一致；`_resolve_overall` 在 Task 5 定义并被 Task 4 的列表端点复用（Task 4 先落地，故 Task 4 内注明暂用 `r.score_overall`，Task 5 落地后由 `_resolve_overall` 接管——执行顺序即为 4 在 5 之前，Task 5 的 Step 3 需回改列表端点一处）；`denominator_scope` 在 Task 9 定义为 dataclass 字段并贯通到 Pydantic item 模型（Task 3 先加 schema 字段，Task 9 供值）。
