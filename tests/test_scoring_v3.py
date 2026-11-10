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

    def test_brief_and_detail_accept_null_dimension_scores(self):
        """v3 未评价的维度在库里是 NULL，读取模型必须能承接。"""
        from app.schemas.quality import QualityReportDetail
        b = QualityReportBrief(
            id="r1", file_name="a.geojson", detected_format="stored_file",
            completeness_score=0.0, total_features=1, total_fields=0,
            created_at="2026-11-10T00:00:00Z",
            score_completeness=None, score_topology=None,
        )
        assert b.score_completeness is None and b.score_topology is None
        d = QualityReportDetail(
            id="r1", file_name="a.geojson", detected_format="stored_file",
            total_features=1, total_fields=0, completeness_score=0.0,
            created_at="2026-11-10T00:00:00Z",
            score_accuracy=None, score_overall=None,
        )
        assert d.score_accuracy is None and d.score_overall is None


# ── P2：结果级 denominator_scope ──────────────────────────────────────────────

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

# ── P3：要素级聚合 ────────────────────────────────────────────────────────────

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