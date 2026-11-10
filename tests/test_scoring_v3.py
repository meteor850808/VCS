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