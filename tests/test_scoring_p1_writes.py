"""P1：三处写入路径的 total_features 与列表语义（真实 vcs_test）。"""
from unittest.mock import AsyncMock

import pytest

from app.models.stored_file import StoredFile
from app.services import quality_checker as qc
from app.services.quality.attribute_checker import CheckResult
from app.services.topology_checker import TopologyCheckResult


def _attr(rule_code: str, status: str, failed: int = 0, checked: int = 7) -> CheckResult:
    return CheckResult(
        rule_code=rule_code,
        rule_name=f"规则{rule_code}",
        status=status,
        severity="error",
        field_name="name",
        layer="图层A",
        failed_count=failed,
        total_checked=checked,
        message="m",
    )


def _summary(total: int, passed: int, failed: int) -> dict:
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "by_severity": {"error": failed, "warning": 0},
    }


@pytest.fixture
async def stored_file(db_session):
    """一个要素数为 7 的已入库文件（检查项数将与之不同）。"""
    sf = StoredFile(
        original_name="a.geojson",
        file_size=1,
        feature_count=7,
        data_type="geometry",
        file_format="geojson",
    )
    db_session.add(sf)
    await db_session.flush()
    return sf


def _patch_checkers(monkeypatch, attr_results, topo_results, attr_summary, topo_summary):
    monkeypatch.setattr(
        qc, "run_attribute_checks",
        AsyncMock(return_value=(attr_results, attr_summary)),
    )
    monkeypatch.setattr(
        qc, "run_topology_checks",
        AsyncMock(return_value=(topo_results, topo_summary)),
    )


class TestRunQualityCheckDerivedFields:
    async def test_feature_count_not_check_item_count(
        self, db_session, stored_file, monkeypatch
    ):
        """feature_count 是要素数（7），不是检查项数（3）。"""
        attr = [
            _attr("31010004", "pass"),
            _attr("31010005", "pass"),
            _attr("31010006", "fail", failed=2),
        ]
        _patch_checkers(
            monkeypatch, attr, [], _summary(3, 2, 1), _summary(0, 0, 0)
        )

        res = await qc.run_quality_check(db_session, "std-1", stored_file.id, "key1")

        assert res["feature_count"] == 7
        assert res["attr_summary"]["total"] == 3
        assert res["feature_count"] != res["attr_summary"]["total"]

    async def test_violation_count_counts_failed_entries(
        self, db_session, stored_file, monkeypatch
    ):
        """violation_count = 不通过条目数（2），total_violations 仍是失败要素数之和（14）。"""
        attr = [
            _attr("31010004", "fail", failed=5),
            _attr("31010005", "pass"),
            _attr("31010006", "pass"),
        ]
        topo = [
            TopologyCheckResult(
                rule_code="T01",
                rule_name="空几何",
                status="fail",
                severity="error",
                layer="图层A",
                failed_count=9,
                total_checked=7,
                message="m",
            )
        ]
        _patch_checkers(
            monkeypatch, attr, topo, _summary(3, 2, 1), _summary(1, 0, 1)
        )

        res = await qc.run_quality_check(db_session, "std-1", stored_file.id, "key1")

        assert res["violation_count"] == 2
        assert res["total_violations"] == 14
