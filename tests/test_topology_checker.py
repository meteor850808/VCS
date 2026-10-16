"""P5: 拓扑检查引擎测试 — app/services/topology_checker.py 13% → 全覆盖。

策略：纯函数直接单测；异步 SQL 用按 SQL 特征分发的脚本化 FakeTopologyDB。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app.services.topology_checker as tc
from app.models.spatial_feature import PolygonFeature
from app.services.topology_checker import (
    MetricStrategy,
    TopologyConfig,
    _make_result,
    _summarize,
    resolve_metric_strategy,
    run_topology_checks,
)


# ── Fake DB ─────────────────────────────────────────────────────────────────

class _R:
    """行对象：同时支持属性访问与下标访问（模拟 SQLAlchemy Row）。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getitem__(self, i):
        return list(self.__dict__.values())[i]

    def __repr__(self):
        return f"_R({self.__dict__})"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalar(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def _sql_str(query, params=None):
    if isinstance(query, str):
        return query
    try:
        return str(query.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(query)


class FakeTopologyDB:
    """按 SQL 子串特征分发的脚本化 DB（handler 顺序即优先级）。

    handlers: list[(子串, 工厂函数)] — 未匹配即报错，帮助测试防呆。
    """

    def __init__(self, handlers):
        self.handlers = handlers
        self.executed = []
        self.rolled_back = False

    async def execute(self, query, params=None):
        sql = _sql_str(query, params)
        self.executed.append((sql, params))
        for substr, factory in self.handlers:
            if substr in sql:
                return factory()
        raise AssertionError(f"未处理的查询: {sql[:200]!r}")

    async def rollback(self):
        self.rolled_back = True


async def _fake_resolve(db, models, fid):
    return MetricStrategy("planar", None)


# ── 样本几何 ─────────────────────────────────────────────────────────────────

SQUARE_WKT = "POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))"           # CCW 凸面 4 边 → 正常
CROSS_WKT = "POLYGON ((0 0, 1 0, 1 3, 4 3, 4 0, 5 0, 5 5, 4 5, 4 2, 1 2, 1 5, 0 5, 0 0))"  # 凸度 0.28 < 0.3
DEGENERATE_WKT = "POLYGON ((0 0, 1 0, 0 0))"                  # 2 边 → 边数不足
GARBAGE_WKT = "NOT A GEOMETRY"


class TestMetricStrategy:
    def test_area_native_no_transform(self):
        s = MetricStrategy("native", 32651)
        sql = _sql_str(s.area_m2(PolygonFeature.geometry))
        assert "ST_Area" in sql and "ST_Transform" not in sql

    def test_area_planar_no_transform(self):
        s = MetricStrategy("planar", None)
        sql = _sql_str(s.area_m2(PolygonFeature.geometry))
        assert "ST_Area" in sql and "ST_Transform" not in sql

    def test_area_utm_transform(self):
        s = MetricStrategy("utm", 32651)
        sql = _sql_str(s.area_m2(PolygonFeature.geometry))
        assert "ST_Transform" in sql and "32651" in sql

    def test_length_utm(self):
        s = MetricStrategy("utm", 32760)
        sql = _sql_str(s.length_m(PolygonFeature.geometry))
        assert "ST_Transform" in sql and "32760" in sql

    def test_distance_native(self):
        s = MetricStrategy("native", 32651)
        sql = _sql_str(s.distance_m("a", "b"))
        assert "ST_Distance" in sql
        assert "ST_Transform" not in sql

    def test_distance_utm(self):
        s = MetricStrategy("utm", 32651)
        sql = _sql_str(s.distance_m("a", "b"))
        assert sql.count("ST_Transform") == 2

    def test_sql_metric_native(self):
        s = MetricStrategy("native", None)
        assert s.sql_metric("a.geometry", "ST_Area") == "ST_Area(a.geometry)"

    def test_sql_metric_utm(self):
        s = MetricStrategy("utm", 32651)
        assert s.sql_metric("a.geometry", "ST_Area") == "ST_Area(ST_Transform(a.geometry, 32651))"

    def test_sql_metric_planar(self):
        s = MetricStrategy("planar", None)
        assert s.sql_metric("x", "ST_Length") == "ST_Length(x)"


class TestMakeResult:
    def test_known_rule_fail(self):
        r = _make_result("empty_geometry", status="fail", failed_count=3, k=1)
        assert r.rule_code == "T01"
        assert r.rule_name == "空几何"
        assert r.status == "fail"
        assert r.severity == "error"
        assert r.detail == {"k": 1}

    def test_known_rule_pass_severity_info(self):
        r = _make_result("max_area", status="pass")
        assert r.severity == "info"
        assert r.rule_code == "T03"

    def test_unknown_rule(self):
        r = _make_result("bogus_rule", status="fail")
        assert r.rule_code == "T??"
        assert r.rule_name == "bogus_rule"
        assert r.severity == "warning"

    def test_line_connectivity_mapping(self):
        r = _make_result("line_connectivity", status="fail")
        assert r.rule_code == "T10"
        assert r.rule_name == "线连通性"


class TestSummarize:
    def test_mixed(self):
        results = [
            _make_result("empty_geometry", status="pass"),     # pass, 不计 severity
            _make_result("self_intersection", status="pass"),  # pass
            _make_result("empty_geometry", status="fail"),     # fail → error
            _make_result("point_overlap", status="fail"),      # fail → warning
            _make_result("polygon_gap", status="error"),       # error 状态不计入
        ]
        s = _summarize(results)
        assert s["total"] == 5
        assert s["passed"] == 2
        assert s["failed"] == 2
        assert s["by_severity"] == {"error": 1, "warning": 1, "info": 0}

    def test_empty(self):
        s = _summarize([])
        assert s == {"total": 0, "passed": 0, "failed": 0,
                     "by_severity": {"error": 0, "warning": 0, "info": 0}}


class TestResolveMetricStrategy:
    async def test_no_srid_planar(self, monkeypatch):
        db = FakeTopologyDB([
            ("ST_SRID", lambda: _Rows([])),   # 三个几何类型均无 SRID
        ])
        s = await resolve_metric_strategy(db, ["point", "line", "polygon"], "file-abc123")
        assert s.mode == "planar"
        assert s.target_srid is None

    async def test_meter_projection_native(self, monkeypatch):
        async def fake_is_geo(db, srid):
            return False
        monkeypatch.setattr("app.services.custom_crs.get_srid_is_geographic", fake_is_geo)
        db = FakeTopologyDB([
            ("ST_SRID", lambda: _Rows([4528])),
            ("proj4text", lambda: _Rows([_R(p4="+proj=tmerc +units=m +no_defs")])),
        ])
        s = await resolve_metric_strategy(db, ["polygon"], "file-abc123")
        assert s.mode == "native"
        assert s.target_srid == 4528

    async def test_geographic_utm(self, monkeypatch):
        async def fake_is_geo(db, srid):
            return True
        monkeypatch.setattr("app.services.custom_crs.get_srid_is_geographic", fake_is_geo)
        db = FakeTopologyDB([
            ("ST_SRID", lambda: _Rows([4326])),
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=105.0, max_lon=112.0, min_lat=30.0, max_lat=42.0)])),
        ])
        s = await resolve_metric_strategy(db, ["point", "line", "polygon"], "file-abc123")
        assert s.mode == "utm"
        assert s.target_srid == 32649  # 中心 (108.5, 36) → zone 49, 北半球

    async def test_projected_non_meter_utm(self, monkeypatch):
        async def fake_is_geo(db, srid):
            return False
        monkeypatch.setattr("app.services.custom_crs.get_srid_is_geographic", fake_is_geo)
        db = FakeTopologyDB([
            ("ST_SRID", lambda: _Rows([3857])),
            ("proj4text", lambda: _Rows([_R(p4="+proj=merc +units=ft +no_defs")])),
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=-60.0, max_lon=-50.0, min_lat=-30.0, max_lat=-20.0)])),
        ])
        s = await resolve_metric_strategy(db, ["polygon"], "file-abc123")
        assert s.mode == "utm"
        assert s.target_srid == 32721  # 中心 (-55, -25) → zone 21, 南半球

    async def test_unregistered_srid_planar(self, monkeypatch, caplog):
        import logging

        async def fake_is_geo(db, srid):
            return None
        monkeypatch.setattr("app.services.custom_crs.get_srid_is_geographic", fake_is_geo)
        db = FakeTopologyDB([("ST_SRID", lambda: _Rows([999999]))])
        with caplog.at_level(logging.WARNING):
            s = await resolve_metric_strategy(db, ["point"], "file-abc123")
        assert s.mode == "planar"
        assert "无法判定类型" in caplog.text


class TestUtmStrategy:
    async def test_no_rows_planar(self):
        db = FakeTopologyDB([("ST_Extent", lambda: _Rows([]))])
        s = await tc._utm_strategy_for_data(db, ["point", "line", "polygon"], "file-abc123", 4326)
        assert s.mode == "planar"

    async def test_out_of_bounds_bbox_planar(self, caplog):
        import logging

        db = FakeTopologyDB([
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=25000.0, max_lon=27100.0, min_lat=-1900.0, max_lat=1200.0)])),
        ])
        with caplog.at_level(logging.WARNING):
            s = await tc._utm_strategy_for_data(db, ["point"], "file-abc123", 4326)
        assert s.mode == "planar"
        assert "超出地理度域" in caplog.text

    async def test_north_zone(self):
        db = FakeTopologyDB([
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=120.9, max_lon=121.5, min_lat=30.5, max_lat=31.2)])),
        ])
        s = await tc._utm_strategy_for_data(db, ["point"], "file-abc123", 4326)
        assert s.mode == "utm"
        assert s.target_srid == 32651  # 中心 121.2 → zone 51

    async def test_zone_clamped_at_60(self):
        db = FakeTopologyDB([
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=179.0, max_lon=180.0, min_lat=0.0, max_lat=10.0)])),
        ])
        s = await tc._utm_strategy_for_data(db, ["point"], "file-abc123", 4326)
        assert s.target_srid == 32660  # min(60, 60)

    async def test_zone_clamped_at_1(self):
        db = FakeTopologyDB([
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=-180.0, max_lon=-179.0, min_lat=0.0, max_lat=10.0)])),
        ])
        s = await tc._utm_strategy_for_data(db, ["point"], "file-abc123", 4326)
        assert s.target_srid == 32601  # max(1, 1)

    async def test_south_hemisphere(self):
        db = FakeTopologyDB([
            ("ST_Extent", lambda: _Rows(
                [_R(min_lon=114.0, max_lon=115.0, min_lat=-40.0, max_lat=-30.0)])),
        ])
        s = await tc._utm_strategy_for_data(db, ["point"], "file-abc123", 4326)
        assert s.target_srid == 32750  # 中心 114.5 → zone 50


class TestRunTopologyChecks:
    """run_topology_checks 主函数 — 全规则 + 单规则 + 异常路径。"""

    async def test_full_default_run(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_NumGeometries", lambda: _Rows([0])),      # T13 ×3 → pass
            ("ST_IsEmpty", lambda: _Rows([0])),            # T1 ×3 → pass
            ("ST_IsValid", lambda: _Rows([])),             # T2 ×2 → pass
            ("ORDER BY area DESC", lambda: _Rows(
                [_R(feature_index=3, area=2e10)])),        # T3 → fail
            ("ORDER BY area ASC", lambda: _Rows([])),      # T4 → pass
            ("ST_Intersection", lambda: _Rows(
                [_R(idx_a=1, idx_b=2, overlap_area=0.5)])),  # T6/T8 → fail
            ("ST_Length", lambda: _Rows([])),              # T5 → pass
            ("ST_Distance", lambda: _Rows(
                [_R(idx_a=1, idx_b=2, dist=0.0001)])),     # T7 → fail
            ("ST_UNION", lambda: _Rows(
                [_R(gap_area=0.0003)])),                   # T9 → fail
            ("count(", lambda: _Rows([5])),                # 各 count 查询
        ])
        results, summary = await run_topology_checks(db, "file-abc123",
                                                     config=TopologyConfig())
        # 默认规则: T1 + T2×2 + T3 + T4 + T5 + T6 + T7 + T8 + T9 + T13×3 = 13
        assert len(results) == 13
        codes = [r.rule_code for r in results]
        assert codes.count("T01") == 1
        assert codes.count("T02") == 2
        assert codes.count("T13") == 3
        # T1 通过（无空几何）
        t1 = next(r for r in results if r.rule_code == "T01")
        assert t1.status == "pass"
        assert t1.message == "无空几何"
        # T3 发现极大面
        t3 = next(r for r in results if r.rule_code == "T03")
        assert t3.status == "fail"
        assert t3.failed_count == 1
        assert t3.feature_indices == [3]
        # T6 对面重叠对
        t6 = next(r for r in results if r.rule_code == "T06")
        assert t6.status == "fail"
        assert (1, 2) in t6.pairs
        # T9 面裂隙
        t9 = next(r for r in results if r.rule_code == "T09")
        assert t9.status == "fail"
        assert t9.failed_count == 1
        # 汇总: 8 pass (T01/T02×2/T04/T05/T13×3) + 5 fail (T03/T06/T07/T08/T09)
        assert summary["total"] == 13
        assert summary["failed"] == 5
        assert summary["passed"] == 8
        assert len(db.executed) >= 13

    async def test_t1_empty_geometry_fail(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsEmpty", lambda: _Rows([1])),            # 每个几何类型 1 条空
            ("count(", lambda: _Rows([3])),
        ])
        results, _ = await run_topology_checks(db, "file-abc123",
                                               config=TopologyConfig(enabled_rules=["empty_geometry"]))
        assert len(results) == 1
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 3  # point+line+polygon 各 1
        assert "3 条空几何" in r.message

    async def test_t2_self_intersection_both_types(self, monkeypatch):
        """红用例：T2 循环条件 bug（gtype 永不匹配 active_rules）→ 修复前结果为 0"""
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsValid", lambda: _Rows(
                [_R(feature_index=5, wkt="LINESTRING (0 0, 1 1, 1 0)")])),
            ("count(", lambda: _Rows([10])),
        ])
        results, _ = await run_topology_checks(db, "file-abc123",
                                               config=TopologyConfig(enabled_rules=["self_intersection"]))
        assert len(results) == 2  # line + polygon 各一条
        assert all(r.status == "fail" for r in results)
        assert {r.rule_name for r in results} == {"自相交"}

    async def test_t2_pass(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsValid", lambda: _Rows([])),
            ("count(", lambda: _Rows([10])),
        ])
        results, _ = await run_topology_checks(db, "file-abc123",
                                               config=TopologyConfig(enabled_rules=["self_intersection"]))
        assert len(results) == 2
        assert all(r.status == "pass" for r in results)

    @pytest.mark.parametrize("rule_name,matcher,row_kwargs", [
        ("max_area", "ORDER BY area DESC", {"feature_index": 1, "area": 1e11}),
        ("min_area", "ORDER BY area ASC", {"feature_index": 2, "area": 0.0005}),
        ("short_line", "ST_Length", {"feature_index": 3, "len": 0.00005}),
    ])
    async def test_metric_rules_fail_pattern(self, monkeypatch, rule_name, matcher, row_kwargs):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            (matcher, lambda rk=row_kwargs: _Rows([_R(**rk)])),
            ("count(", lambda: _Rows([8])),
        ])
        results, _ = await run_topology_checks(db, "file-abc123",
                                               config=TopologyConfig(enabled_rules=[rule_name]))
        assert len(results) == 1
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 1
        assert r.feature_indices[0] == row_kwargs["feature_index"]

    @pytest.mark.parametrize("rule_name,matcher", [
        ("max_area", "ORDER BY area DESC"),
        ("min_area", "ORDER BY area ASC"),
        ("short_line", "ST_Length"),
    ])
    async def test_metric_rules_pass_blank(self, monkeypatch, rule_name, matcher):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            (matcher, lambda: _Rows([])),
            ("count(", lambda: _Rows([8])),
        ])
        results, _ = await run_topology_checks(db, "file-abc123",
                                               config=TopologyConfig(enabled_rules=[rule_name]))
        r = results[0]
        assert r.status == "pass"
        assert r.failed_count == 0

    async def test_t10_line_connectivity_pass(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_StartPoint", lambda: _Rows([
                _R(feature_index=1, start_pt="POINT (0 0)", end_pt="POINT (1 1)"),
                _R(feature_index=2, start_pt="POINT (1 1)", end_pt="POINT (2 2)"),
            ])),
            ("count(", lambda: _Rows([2])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="line",
            config=TopologyConfig(enabled_rules=["line_connectivity"]))
        r = results[0]
        assert r.status == "pass"
        assert r.rule_code == "T10"
        assert r.total_checked == 2

    async def test_t10_dangling_detected(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_StartPoint", lambda: _Rows([
                _R(feature_index=1, start_pt="POINT (0 0)", end_pt="POINT (1 1)"),
                _R(feature_index=2, start_pt="POINT (1 1)", end_pt="POINT (2 2)"),
                _R(feature_index=3, start_pt="POINT (5 5)", end_pt="POINT (6 6)"),
            ])),
            ("count(", lambda: _Rows([3])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="line",
            config=TopologyConfig(enabled_rules=["line_connectivity"]))
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 4  # 0-0/2-2/5-5/6-6 悬挂

    async def test_t10_insufficient_features(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_StartPoint", lambda: _Rows([
                _R(feature_index=1, start_pt="POINT (0 0)", end_pt="POINT (1 1)"),
            ])),
            ("count(", lambda: _Rows([1])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="line",
            config=TopologyConfig(enabled_rules=["line_connectivity"]))
        r = results[0]
        assert r.status == "pass"
        assert "要素不足" in r.message

    async def test_t11_irregular_polygon(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_AsText", lambda: _Rows([
                _R(feature_index=1, wkt=SQUARE_WKT),       # 正常（凸度 1.0）
                _R(feature_index=2, wkt=CROSS_WKT),        # 凸度 0.28 不足
                _R(feature_index=3, wkt=DEGENERATE_WKT),   # 边数不足
                _R(feature_index=4, wkt=GARBAGE_WKT),      # 解析失败 → 跳过
                _R(feature_index=5, wkt="POLYGON EMPTY"),  # 空几何 → continue
            ])),
            ("count(", lambda: _Rows([5])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="polygon",
            config=TopologyConfig(enabled_rules=["irregular_polygon"]))
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 2
        assert r.feature_indices == [2, 3]
        assert r.total_checked == 5

    async def test_t12_ring_direction_fail(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("CASE WHEN ST_Area", lambda: _Rows(
                [_R(feature_index=1, winding="CW"),
                 _R(feature_index=2, winding="CW"),
                 _R(feature_index=3, winding="CCW")])),
            ("count(", lambda: _Rows([3])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="polygon",
            config=TopologyConfig(enabled_rules=["ring_direction"]))
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 2
        assert r.rule_code == "T12"

    async def test_t12_ring_direction_pass(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("CASE WHEN ST_Area", lambda: _Rows(
                [_R(feature_index=1, winding="CCW")])),
            ("count(", lambda: _Rows([1])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="polygon",
            config=TopologyConfig(enabled_rules=["ring_direction"]))
        r = results[0]
        assert r.status == "pass"
        assert "所有环方向正确" in r.message

    async def test_t13_multipart_fail(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_NumGeometries", lambda: _Rows([3])),
            ("count(", lambda: _Rows([9])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="point",
            config=TopologyConfig(enabled_rules=["multipart_geometry"]))
        r = results[0]
        assert r.status == "fail"
        assert r.failed_count == 3
        assert r.total_checked == 9
        assert "点多部件几何" in r.message

    @pytest.mark.parametrize("rule_name,enabled_rules,gtype,matcher", [
        ("max_area", ["max_area"], "polygon", "ORDER BY area DESC"),
        ("polygon_overlap", ["polygon_overlap"], "polygon", "ST_Intersection"),
        ("point_overlap", ["point_overlap"], "point", "ST_Distance"),
        ("line_overlap", ["line_overlap"], "line", "ST_Intersection"),
        ("polygon_gap", ["polygon_gap"], "polygon", "ST_UNION"),
        ("min_area", ["min_area"], "polygon", "ORDER BY area ASC"),
    ])
    async def test_rule_exception_returns_error(self, monkeypatch,
                                                rule_name, enabled_rules, gtype, matcher):
        """单规则 SQL 异常 → error 结果 + rollback，不拖垮整体"""
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)

        def _boom():
            raise RuntimeError("connection reset")

        db = FakeTopologyDB([
            (matcher, _boom),
            ("count(", lambda: _Rows([5])),
        ])
        results, summary = await run_topology_checks(
            db, "file-abc123", geometry_type=gtype,
            config=TopologyConfig(enabled_rules=enabled_rules))
        assert len(results) == 1
        r = results[0]
        assert r.status == "error"
        assert db.rolled_back is True
        assert summary["failed"] == 0

    async def test_layer_filter_applied(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsEmpty", lambda: _Rows([0])),
            ("count(", lambda: _Rows([2])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123",
            config=TopologyConfig(enabled_rules=["empty_geometry"]),
            layer_names=["layer2", "layer1", " layer2 "])
        results, _ = await run_topology_checks(
            db, "file-abc123",
            config=TopologyConfig(enabled_rules=["empty_geometry"]),
            layer_names=["layer2", "layer1", " layer2 "])
        assert len(results) == 1
        # 圈定条件出现在每个查询中（去空白、去重、排序后 IN ('layer1','layer2')）
        combined = "".join(sql for sql, _ in db.executed)
        assert "layer_name" in combined
        assert "IN ('layer1', 'layer2')" in combined

    async def test_config_none_creates_default(self, monkeypatch):
        """config=None → 内部创建 TopologyConfig()，默认规则集合生效"""
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsEmpty", lambda: _Rows([0])),
            ("ST_Distance", lambda: _Rows([])),            # T7 (默认规则) → pass
            ("ST_NumGeometries", lambda: _Rows([0])),      # T13 point → pass
            ("count(", lambda: _Rows([2])),                # 各模型 count 预查询
        ])
        results, _ = await run_topology_checks(db, "file-abc123", geometry_type="point")
        assert len(results) == 3  # T1 + T7 + T13(point)
        assert all(r.status == "pass" for r in results)

    async def test_t2_polygon_only_skips_line_model(self, monkeypatch):
        """geometry_type=polygon → T2 循环对 line 走 continue 分支"""
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsValid", lambda: _Rows([])),
            ("count(", lambda: _Rows([6])),
        ])
        results, _ = await run_topology_checks(
            db, "file-abc123", geometry_type="polygon",
            config=TopologyConfig(enabled_rules=["self_intersection"]))
        assert len(results) == 1
        assert results[0].status == "pass"
        assert results[0].total_checked == 6

    async def test_layer_filter_all_rules(self, monkeypatch):
        """全 13 规则 + 图层圈定 → T2/T3/T4/T5/T10/T11/T13 的 ORM 圈定条件全覆盖"""
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("ST_IsValid", lambda: _Rows([])),             # T2 ×2 → pass（须在 ST_AsText 前）
            ("ST_AsText", lambda: _Rows(
                [_R(feature_index=1, wkt=SQUARE_WKT)])),   # T11 → pass
            ("ST_UNION", lambda: _Rows([])),               # T9 → pass（须在 ST_IsEmpty 前）
            ("CASE WHEN ST_Area", lambda: _Rows(
                [_R(feature_index=1, winding="CCW")])),    # T12 → pass（须在 ST_IsEmpty 前）
            ("ST_IsEmpty", lambda: _Rows([0])),            # T1 ×3 → pass
            ("ST_NumGeometries", lambda: _Rows([0])),      # T13 ×3 → pass
            ("ORDER BY area DESC", lambda: _Rows([])),     # T3 → pass
            ("ORDER BY area ASC", lambda: _Rows([])),      # T4 → pass
            ("ST_Intersection", lambda: _Rows([])),        # T6/T8 → pass（须在 ST_Length 前）
            ("ST_Length", lambda: _Rows([])),              # T5 → pass
            ("ST_Distance", lambda: _Rows([])),            # T7 → pass
            ("ST_StartPoint", lambda: _Rows([])),          # T10 → pass（要素不足）
            ("count(", lambda: _Rows([7])),
        ])
        results, summary = await run_topology_checks(
            db, "file-abc123",
            config=TopologyConfig(enabled_rules=list(tc.ALL_RULES.keys())),
            layer_names=["layer1"])
        # 13 条规则: T1×1 + T2×2 + T3~T12×1 + T13×3 = 16 项结果
        assert len(results) == 16
        assert summary["failed"] == 0
        assert summary["passed"] == 16
        combined = "".join(sql for sql, _ in db.executed)
        # 原生 SQL 圈定条件 (T6/T7/T8/T9/T12 均含 = ANY)
        assert "layer_name = ANY" in combined
        # ORM 圈定条件 (T1/T2/T3/T4/T5/T10/T11/T13 均含 IN)
        assert "layer_name IN" in combined

    async def test_geometry_type_none_all_models(self, monkeypatch):
        monkeypatch.setattr(tc, "resolve_metric_strategy", _fake_resolve)
        db = FakeTopologyDB([
            ("count(", lambda: _Rows([2])),
        ])
        results, summary = await run_topology_checks(
            db, "file-abc123", config=TopologyConfig(enabled_rules=[]))
        assert results == []
        assert summary["total"] == 0
        # counts 查询应覆盖 3 个模型
        assert len(db.executed) == 3

    async def test_invalid_geometry_type_no_rules(self, monkeypatch):
        async def fake_resolve_none(db, models, fid):
            assert models == []  # 无效类型 → 无模型
            return MetricStrategy("planar", None)

        monkeypatch.setattr(tc, "resolve_metric_strategy", fake_resolve_none)
        db = FakeTopologyDB([])
        results, summary = await run_topology_checks(
            db, "file-abc123", geometry_type="text",
            config=TopologyConfig(enabled_rules=["empty_geometry"]))
        assert results == []
        assert summary["total"] == 0
        assert db.executed == []