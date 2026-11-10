"""P1：后端读路径统一（_resolve_overall / _verdict_color）。"""
from types import SimpleNamespace

import pytest

from app.api.v1.quality import _resolve_overall, _verdict_color, _verdict_of


def _legacy_report(**over):
    base = dict(
        score_overall=None,
        score_completeness=1.0,
        score_accuracy=1.0,
        score_consistency=1.0,
        score_uniqueness=1.0,
        score_topology=1.0,
        scoring_profile="v2",
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestResolveOverall:
    def test_legacy_row_recomputes(self):
        """历史 v2 行（score_overall 为 NULL）必须兜底重算，不得显示「未评价」。"""
        assert _resolve_overall(_legacy_report()) == 1.0

    def test_legacy_row_recompute_matches_weights(self):
        r = _legacy_report(
            score_completeness=0.9, score_accuracy=0.85, score_consistency=0.8,
            score_uniqueness=0.75, score_topology=0.7,
        )
        assert _resolve_overall(r) == pytest.approx(0.815)

    def test_v3_row_without_overall_stays_none(self):
        """v3 行没有 overall 表示「全部维度未评价」，不得回退成 0 分。"""
        r = _legacy_report(scoring_profile="v3")
        assert _resolve_overall(r) is None

    def test_stored_overall_wins(self):
        r = _legacy_report(score_overall=0.43219)
        assert _resolve_overall(r) == 0.4322

    def test_missing_profile_column_defaults_to_legacy(self):
        """Task 8 之前 ORM 没有 scoring_profile 列，getattr 必须兜底 v2。"""
        r = _legacy_report()
        del r.scoring_profile
        assert _resolve_overall(r) == 1.0

    def test_v2_row_with_all_dims_null_stays_none(self):
        """五列全空且无 overall 的 v2 行（旧同步接口）→ 未评价，不伪造 0.0。"""
        r = _legacy_report(
            score_completeness=None, score_accuracy=None, score_consistency=None,
            score_uniqueness=None, score_topology=None,
        )
        assert _resolve_overall(r) is None


class TestVerdictOf:
    """读路径的判定必须与写入端同源：都要带上 evaluation_errors。"""

    def test_high_score_without_errors_is_pass(self):
        assert _verdict_of(_legacy_report(score_overall=0.95)) == "pass"

    def test_high_score_with_errors_stays_not_evaluated(self):
        """B1：库里有 evaluation_errors 时，高分也不得显示为 pass。"""
        r = _legacy_report(
            score_overall=0.99,
            evaluation_errors=[{"rule_code": "T03", "rule_name": "极大面", "message": "boom"}],
        )
        assert _verdict_of(r) == "not_evaluated"

    def test_missing_overall_is_not_evaluated(self):
        assert _verdict_of(_legacy_report(scoring_profile="v3")) == "not_evaluated"

    def test_missing_errors_column_defaults_to_empty(self):
        """Task 8 之前 ORM 没有 evaluation_errors 列，getattr 必须兜底空列表。"""
        r = _legacy_report(score_overall=0.95)   # 无 evaluation_errors 属性
        assert _verdict_of(r) == "pass"


class TestVerdictColor:
    def test_verdict_color_mapping(self):
        assert _verdict_color("pass") == "#27ae60"
        assert _verdict_color("warn") == "#f39c12"
        assert _verdict_color("fail") == "#e74c3c"
        assert _verdict_color("not_evaluated") == "#7f8c8d"

    def test_unknown_verdict_falls_back_to_grey(self):
        assert _verdict_color("bogus") == "#7f8c8d"
