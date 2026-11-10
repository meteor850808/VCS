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
