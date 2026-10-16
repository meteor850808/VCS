"""P6a: quality_checker.py 服务级全覆盖（55% → 100%）。

覆盖目标：
  52-82  compute_scores（五维 + 权重 + 空结果守卫 + _score_by_codes 全分支）
  132-214 run_quality_check 编排（feature_count 守卫 / standard_id 双分支 /
            topo 结果 / violation_samples 双来源 / total_violations / dict 转换）
  226/244 _check_to_dict / _topo_to_dict
  296-297 check_quality process_attributes 异常兜底
  347      _calculate_score_legacy total==0 → 0.0

注意：check_quality 内 detect_format / process_attributes 均为**函数内 import**，
      patch 点必须位于源模块（app.services.format_detection / attribute_processing）。
"""

from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from app.services import quality_checker as qc
from app.services.quality.attribute_checker import CheckResult
from app.services.topology_checker import TopologyCheckResult
from app.services.quality.models import QualityResult


def _attr(rule_code="31010004", status="pass", **kw):
    return CheckResult(
        rule_code=rule_code, rule_name=f"r-{rule_code}", status=status,
        severity="error", **kw,
    )


class TestComputeScores:
    """52-82 五维评分。"""

    def test_all_pass(self):
        rel = [_attr("31010004"), _attr("31010005"),
               _attr("31010001"), _attr("31010002")]
        topo = [TopologyCheckResult("T01", "空几何", "pass", "error")]
        s = qc.compute_scores(rel, topo)
        assert s["completeness"] == 1.0
        assert s["accuracy"] == 1.0
        assert s["consistency"] == 1.0
        assert s["uniqueness"] == 1.0
        assert s["topology"] == 1.0
        assert s["overall"] == 1.0  # 各权重求和

    def test_partial_and_fail(self):
        rel = [_attr("31010004"), _attr("31010004", status="fail", failed_count=3),
               _attr("31010005", status="fail", failed_count=1),
               _attr("31010006", status="fail", failed_count=2),
               _attr("31010001", status="fail", failed_count=1)]
        topo = [TopologyCheckResult("T01", "空几何", "fail", "error", failed_count=4),
                TopologyCheckResult("T02", "自相交", "pass", "error")]
        s = qc.compute_scores(rel, topo)
        assert s["completeness"] == 0.5    # 2 中 1 过
        assert s["accuracy"] == 0.0        # 1 中 0 过
        assert s["consistency"] == 0.0     # 1 中 0 过
        assert s["uniqueness"] == 0.0      # 1 中 0 过
        assert s["topology"] == 0.5        # 2 中 1 fail
        overall = 0.5 * 0.25 + 0.5 * 0.15
        assert s["overall"] == round(overall, 4)

    def test_no_relevant_returns_1(self):
        """55-56/71：某维无相关结果 → 1.0（不惩罚）。"""
        rel = [_attr("31010001")]  # 只有 consistency
        s = qc.compute_scores(rel, [])
        assert s["completeness"] == 1.0
        assert s["accuracy"] == 1.0
        assert s["uniqueness"] == 1.0
        assert s["topology"] == 1.0  # topo 空

    def test_empty_everything(self):
        s = qc.compute_scores([], [])
        assert s["topology"] == 1.0 and s["overall"] == 1.0

    def test_weights_normalized(self):
        """v2.20 契约：WEIGHTS 求和为 1.0（防权重漂移）。"""
        total = sum(qc.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9
        assert set(qc.WEIGHTS) == {
            "completeness", "accuracy", "consistency", "uniqueness", "topology",
        }

    def test_frontend_no_hardcoded_weights(self):
        """v2.20 契约：前端 app.js 不得再硬编码五维权重（防漂移回归）。

        前端只消费后端返回的 score_overall。
        """
        from pathlib import Path
        js = Path(__file__).resolve().parents[1] / "app" / "frontend" / "app.js"
        src = js.read_text(encoding="utf-8")
        for literal in ("*0.25", "*0.25", "*0.20", "*0.15", "*0.15",
                        "score_completeness*", "score_topology*0"):
            assert literal not in src, f"前端残留硬编码权重: {literal}"
        assert "score_overall" in src, "前端应消费后端 score_overall"


class TestDictConverters:
    """225-254 两个 dict 转换。"""

    def test_check_to_dict(self):
        r = _attr("31010004", status="fail", field_name="f1", layer="L",
                  failed_count=2, total_checked=5, expected="非空",
                  actual="3 空", message="m", samples=["a", "b"])
        d = qc._check_to_dict(r)
        assert d["rule_code"] == "31010004" and d["field"] == "f1"
        assert d["field_name"] == "f1" and d["layer"] == "L"
        assert d["failed_count"] == 2 and d["total_checked"] == 5
        assert d["expected"] == "非空" and d["actual"] == "3 空"
        assert d["samples"] == ["a", "b"] and d["severity"] == "error"

    def test_topo_to_dict_truncates(self):
        r = TopologyCheckResult("T06", "面重叠", "fail", "error",
                                failed_count=9, total_checked=100,
                                feature_indices=list(range(100)),
                                pairs=[(i, i + 1) for i in range(100)])
        d = qc._topo_to_dict(r)
        assert d["failed_count"] == 9
        assert len(d["feature_indices"]) == 50   # [:50]
        assert len(d["pairs"]) == 50


class TestRunQualityCheck:
    """94-222 主编排器（真实 vcs_test + monkeypatch checker）。"""

    async def test_feature_count_zero_guard(self, db_session):
        """134-152：feature_count=0 → 空失败结论。"""
        from app.models.stored_file import StoredFile
        sf = StoredFile(original_name="empty.geojson",
                        file_format="geojson", data_type="geometry",
                        feature_count=0, api_key_id=None)
        db_session.add(sf)
        await db_session.commit()

        res = await qc.run_quality_check(db_session, "sid", sf.id, "key1")
        assert res["attr_results"] == []
        assert res["attr_summary"]["message"] == "文件无图形要素（feature_count=0），质检无法执行"
        assert res["scores"]["overall"] == 0.0
        assert res["total_violations"] == 0

    async def test_file_missing_guard(self, db_session):
        """134：文件不存在 → _sf_row None → 继续流程（不抛错）。"""
        with patch.object(qc, "run_attribute_checks", new=AsyncMock(
                return_value=([], {"total": 0, "passed": 0, "failed": 0,
                                   "by_severity": {"error": 0, "warning": 0}}))), \
             patch.object(qc, "run_topology_checks", new=AsyncMock(
                return_value=([], {"total": 0, "passed": 0, "failed": 0,
                                   "by_severity": {"error": 0, "warning": 0}}))):
            res = await qc.run_quality_check(db_session, "sid", "no-such-file", "key1")
        assert res["attr_results"] == []
        assert res["scores"]["overall"] == 1.0

    async def test_happy_no_standard(self, db_session, monkeypatch):
        """156-165：standard_id=None → attr 空；topo 正常跑。"""
        from app.models.stored_file import StoredFile
        sf = StoredFile(original_name="a.geojson",
                        file_format="geojson", data_type="geometry",
                        feature_count=5, api_key_id=None)
        db_session.add(sf)
        await db_session.commit()

        topo_result = TopologyCheckResult("T01", "空几何", "fail", "error",
                                          layer="L", failed_count=1,
                                          total_checked=2, message="m",
                                          feature_indices=[0, 1, 2, 3, 4, 5])
        monkeypatch.setattr(qc, "run_topology_checks", AsyncMock(
            return_value=([topo_result], {"total": 1, "passed": 0, "failed": 1,
                                          "by_severity": {"error": 1, "warning": 0}})))
        res = await qc.run_quality_check(db_session, None, sf.id, "key1",
                                         geometry_type="Polygon")
        assert res["attr_results"] == []
        assert len(res["topo_results"]) == 1
        assert res["total_violations"] == 1
        # topo feature_indices[:5] → 6 个索引截 5 条
        assert len(res["violation_samples"]) == 5
        assert res["violation_samples"][0]["feature_index"] == 0

    async def test_full_attr_and_topo_violations(self, db_session, monkeypatch):
        """156-212：standard 存在 + attr/topo 都 fail → 双来源样本。"""
        from app.models.stored_file import StoredFile
        sf = StoredFile(original_name="b.geojson",
                        file_format="geojson", data_type="geometry",
                        feature_count=10, api_key_id=None)
        db_session.add(sf)
        await db_session.commit()

        attr_fail = _attr("31010004", status="fail", field_name="YWBH", layer="LY",
                          failed_count=3, total_checked=10, message="必填为空",
                          samples=["", None, "x"])
        topo_fail = TopologyCheckResult("T06", "面重叠", "fail", "error",
                                        layer="L", failed_count=2,
                                        feature_indices=[7, 8], pairs=[(0, 1)])
        attr_summary = {"total": 1, "passed": 0, "failed": 1,
                        "by_severity": {"error": 1, "warning": 0}}
        topo_summary = {"total": 1, "passed": 0, "failed": 1,
                        "by_severity": {"error": 1, "warning": 0}}
        monkeypatch.setattr(qc, "run_attribute_checks", AsyncMock(
            return_value=([attr_fail], attr_summary)))
        monkeypatch.setattr(qc, "run_topology_checks", AsyncMock(
            return_value=([topo_fail], topo_summary)))
        res = await qc.run_quality_check(db_session, "std-1", sf.id, "key1",
                                         topology_config=None, layer_names=["LY"])
        assert res["attr_summary"] == attr_summary
        assert res["total_violations"] == 5  # 3 + 2
        samples = res["violation_samples"]
        assert len(samples) == 5              # attr 3 样本 + topo 2 条
        assert samples[0]["rule_code"] == "31010004" and samples[0]["sample"] == ""
        assert samples[3]["feature_index"] == 7

    async def test_pass_only_no_samples(self, db_session, monkeypatch):
        """仅 pass 结果 → 无 violation_samples、total=0。"""
        from app.models.stored_file import StoredFile
        sf = StoredFile(original_name="c.geojson",
                        file_format="geojson", data_type="geometry",
                        feature_count=3, api_key_id=None)
        db_session.add(sf)
        await db_session.commit()
        attr = _attr("31010001", status="pass")
        topo = TopologyCheckResult("T01", "空几何", "pass", "error")
        monkeypatch.setattr(qc, "run_attribute_checks", AsyncMock(
            return_value=([attr], {"total": 1, "passed": 1, "failed": 0,
                                   "by_severity": {"error": 0, "warning": 0}})))
        monkeypatch.setattr(qc, "run_topology_checks", AsyncMock(
            return_value=([topo], {"total": 1, "passed": 1, "failed": 0,
                                   "by_severity": {"error": 0, "warning": 0}})))
        res = await qc.run_quality_check(db_session, "std-1", sf.id, "key1")
        assert res["total_violations"] == 0
        assert res["violation_samples"] == []
        assert res["attr_results"][0]["status"] == "pass"


class TestCheckQualityLegacy:
    """259-352 旧接口兼容（patch 源模块函数内 import）。"""

    def _mock_detect(self, format_name, feature_count, schema=()):
        class _Fmt:
            pass
        f = _Fmt()
        f.format_name = format_name
        f.feature_count = feature_count
        f.attribute_schema = list(schema)
        return f

    def test_empty_file_score_zero(self, tmp_path):
        """347：total_features==0 → 0.0（不再满分）。"""
        with patch("app.services.format_detection.detect_format") as m_det, \
             patch("app.services.attribute_processing.process_attributes") as m_attr:
            m_det.return_value = self._mock_detect("GeoJSON", 0)
            class _A:
                total_input = 0
                fields = []
                sample_data = []
            m_attr.return_value = _A()
            res = qc.check_quality(str(tmp_path / "nope.geojson"))
        assert res.completeness_score == 0.0
        assert res.detected_format == "GeoJSON"
        assert res.total_fields == 0

    def test_attr_exception_swallowed(self, tmp_path):
        """296-297：process_attributes 抛错 → warning + 继续（score 回落 1.0）。"""
        with patch("app.services.format_detection.detect_format") as m_det, \
             patch("app.services.attribute_processing.process_attributes",
                   side_effect=RuntimeError("boom")) as m_attr:
            m_det.return_value = self._mock_detect("CSV", 4)
            res = qc.check_quality(str(tmp_path / "a.csv"))
        assert m_attr.called
        assert res.total_features == 4
        # 无样本/无缺失 → generic 1.0；total>0 → 非 0.0
        assert res.completeness_score == 1.0

    def test_total_features_max(self, tmp_path):
        """291-292：attr.total_input 大于 detect → 取 max。"""
        with patch("app.services.format_detection.detect_format") as m_det, \
             patch("app.services.attribute_processing.process_attributes") as m_attr:
            m_det.return_value = self._mock_detect("GeoJSON", 2)
            class _A:
                total_input = 50
                fields = ["a"]
                sample_data = [{"a": "x"}]
            m_attr.return_value = _A()
            res = qc.check_quality(str(tmp_path / "c.geojson"),
                                   detected_format="GeoJSON")
        assert res.total_features == 50
        assert res.completeness_score == 1.0  # 无空值 → generic 1.0

    def test_detected_format_precedence(self, tmp_path):
        """272：显式 detected_format 覆盖 detect 结果。"""
        with patch("app.services.format_detection.detect_format") as m_det, \
             patch("app.services.attribute_processing.process_attributes",
                   side_effect=RuntimeError("x")):
            m_det.return_value = self._mock_detect("GeoJSON", 9)
            res = qc.check_quality(str(tmp_path / "d.bin"),
                                   detected_format="SHP")
        assert res.detected_format == "SHP"
        assert res.total_features == 9


class TestLegacyAnalyze:
    """303-352 旧分析法（构造 QualityResult 纯函数直测，含临界分支）。"""

    def test_critical_fields_present_and_missing(self):
        """316-340：缺失字段 → present=False/null_rate=1；存在字段 → 统计空值。"""
        res = QualityResult(file_name="f", detected_format="CSV",
                            total_features=2, total_fields=0, completeness_score=0)
        fields = ["ywbh", "other"]           # 大小写不敏感匹配 CRITICAL_FIELDS
        samples = [{"ywbh": "A1", "other": 1}, {"ywbh": "", "other": ""}]
        qc._analyze_critical_fields_legacy(res, fields, samples)
        # "ywbh" 在关键字段清单（或其别名）中且数据集存在
        st = res.critical_fields_status
        assert any(v["present"] for v in st.values())
        hit = [v for v in st.values() if v["present"]][0]
        assert hit["null_count"] == 1 and hit["null_rate"] == 0.5
        # 未被数据集覆盖的关键字段 → 整体缺失
        missing = [v for v in st.values() if not v["present"]]
        assert missing and all(v["null_rate"] == 1.0 for v in missing)

    def test_null_values_analysis(self):
        """303-313：空值统计 + missing_fields 排序。"""
        res = QualityResult(file_name="f", detected_format="CSV",
                            total_features=3, total_fields=0, completeness_score=0)
        fields = ["a", "b"]
        samples = [{"a": "", "b": "x"}, {"a": None, "b": "y"}, {"a": "", "b": ""}]
        qc._analyze_null_values_legacy(res, fields, samples)
        assert res.null_value_counts == {"a": 3, "b": 1}
        assert res.missing_fields[0]["field"] == "a"
        assert res.missing_fields[0]["null_rate"] == 1.0
        assert res.missing_fields[1]["field"] == "b"
        assert res.missing_fields[1]["null_rate"] == round(1 / 3, 3)

    def test_calculate_score_legacy_partial(self):
        """343-352：有缺失 → generic 按平均 null_rate 扣减。"""
        res = QualityResult(file_name="f", detected_format="CSV",
                            total_features=10, total_fields=0, completeness_score=0)
        res.missing_fields = [{"null_rate": 0.2}, {"null_rate": 0.4}]
        assert qc._calculate_score_legacy(res) == round(1.0 - 0.3, 3)

    def test_calculate_score_legacy_zero(self):
        """347：total==0 → 0.0。"""
        res = QualityResult(file_name="f", detected_format="CSV",
                            total_features=0, total_fields=0, completeness_score=0)
        assert qc._calculate_score_legacy(res) == 0.0


class TestReportLocateContract:
    """v2.21 契约：报告违规样本 → 预览页定位联动（前端重构 + 后端回传）。

    守卫三类事实：
      1. 前端定位核心已从 locatePvFeature 抽出，可被按 feature_index 复用；
      2. 报告样本单元格在 stored_file_id 存在时渲染定位入口；
      3. 后端 GET /quality/{id} 必须回传 stored_file_id（联动前提）。
    """

    _ROOT = Path(__file__).resolve().parents[1]

    def _js(self):
        return (self._ROOT / "app" / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_locate_core_extracted_and_indexable(self):
        """定位核心可复用：存在 _pvLocateCore + locatePvByFeatureIndex（跨页翻页）。"""
        src = self._js()
        assert "function _pvLocateCore(f,gi,idx)" in src
        assert "function locatePvByFeatureIndex(featureIndex)" in src
        assert "Math.floor(gi/PV.size)+1" in src, "跨页翻页逻辑必须存在"

    def test_report_link_entry(self):
        """报告联动入口：样本单元格仅在 stored_file_id 时渲染 qc-locate。"""
        src = self._js()
        assert "function locateReportSample(fileId,featureIndex)" in src or \
            "async function locateReportSample(fileId,featureIndex)" in src
        assert 'class="qc-locate"' in src
        assert "r.stored_file_id" in src, "联动需依赖后端回传的 stored_file_id"
        assert "locateReportSample('${r.stored_file_id}',${_idx})" in src

    def test_pending_consume_and_recovery(self):
        """待定位项：loadPvMap 成功末尾消费 / 失败 catch 清理，不残留。"""
        src = self._js()
        assert "let _pvPendingLocate=null" in src
        # 消费点在图例渲染（成功路径最后）之后
        assert "_pvPendingLocate=null;\n      if(pl.featureIndex!=null)" in src
        # catch 分支必须清理待定位项（失败不残留）
        assert "}catch(e){\n    _pvPendingLocate=null;" in src
        assert src.index("appendChild(legend)") < src.index("locatePvByFeatureIndex(pl.featureIndex)")

    def test_backend_returns_stored_file_id(self):
        """后端契约：GET /quality/{id} 构造 QualityReportDetail 时回传 stored_file_id。"""
        schema = (self._ROOT / "app" / "schemas" / "quality.py").read_text(encoding="utf-8")
        assert "stored_file_id" in schema
        api = (self._ROOT / "app" / "api" / "v1" / "quality.py").read_text(encoding="utf-8")
        assert "stored_file_id=report.stored_file_id" in api, "GET 端点必须回传 stored_file_id"