# -*- coding: utf-8 -*-
"""quality_exporter.py 单元测试 — GeoJSON/Excel 违规导出。"""
import json

import pytest

from app.services.quality_exporter import (
    _fetch_violation_geometry,
    export_violations_excel,
    export_violations_geojson,
)


# ── Fake DB / Report ─────────────────────────────────────────────────────────

class _FakeDB:
    """记录查询、返回预设 rows 的假会话。"""

    def __init__(self, rows=None, callable_rows=None):
        self.rows = rows if rows is not None else []
        self.callable_rows = callable_rows  # fn(model) -> rows ，支持按表区分
        self.executed = []

    async def execute(self, q):
        self.executed.append(q)
        if self.callable_rows:
            return _ResultProxy(self.callable_rows(q))
        return _ResultProxy(self.rows)


class _ResultProxy:
    def __init__(self, rows):
        self._rows = rows

    async def __aiter__(self):
        for r in self._rows:
            yield r

    def __iter__(self):
        return iter(self._rows)


def _mk_report(**overrides):
    base = {
        "id": 7,
        "file_name": "buildings.shp",
        "detected_format": "SHP",
        "completeness_score": 88.5,
        "score_completeness": 0.885,
        "created_at": "2026-09-07T10:00:00",
        "api_key_id": "key-1",
        "stored_file_id": "file-abc123",
        "topology_violations": [],
        "constraint_violations": [],
        "issues": [],
    }
    base.update(overrides)
    return type("Report", (), base)()


# ── _fetch_violation_geometry ────────────────────────────────────────────────

class TestFetchViolationGeometry:
    async def test_empty_indices_early_return(self):
        db = _FakeDB()
        result = await _fetch_violation_geometry(db, "file-1", [])
        assert result == {}
        assert db.executed == []  # 未发出任何查询

    async def test_fetches_geojson_dicts(self):
        db = _FakeDB([
            (1, '{"type": "Point", "coordinates": [1, 2]}'),
            (2, '{"type": "Point", "coordinates": [3, 4]}'),
        ])
        result = await _fetch_violation_geometry(db, "file-1", [1, 2])
        assert result[1] == {"type": "Point", "coordinates": [1, 2]}
        assert result[2] == {"type": "Point", "coordinates": [3, 4]}
        assert len(db.executed) == 3  # point/line/polygon 三张表都查

    async def test_invalid_json_becomes_none(self):
        db = _FakeDB([(1, "not-json")])
        result = await _fetch_violation_geometry(db, "file-1", [1])
        assert result == {1: None}

    async def test_null_geojson_skipped(self):
        db = _FakeDB([(1, None), (2, "")])
        result = await _fetch_violation_geometry(db, "file-1", [1, 2])
        assert result == {}

    async def test_first_winner_across_tables(self):
        # polygon 表先返回 idx=5，line 表再返回 idx=5 时保留首次
        db = _FakeDB(callable_rows=lambda q: [
            (5, '{"type": "Polygon", "coordinates": [[[0,0],[1,1],[0,0]]]}'),
        ])
        result = await _fetch_violation_geometry(db, "file-1", [5])
        assert result[5]["type"] == "Polygon"
        assert len(db.executed) == 3

    async def test_geo_json_parse_error_typeerror(self):
        db = _FakeDB([(1, 123)])  # 非 str/bytes → json.loads 抛 TypeError
        result = await _fetch_violation_geometry(db, "file-1", [1])
        assert result == {1: None}

    async def test_api_key_filter_applied(self):
        db = _FakeDB([(1, '{"type": "Point"}')])
        await _fetch_violation_geometry(db, "file-1", [1], api_key_id="key-9")
        assert len(db.executed) == 3
        # 每个查询的 where 都带 api_key_id 条件
        from sqlalchemy.sql import visitors

        for q in db.executed:
            seen_key = False
            for col in q.whereclause.get_children():
                pass
            sql = str(q.select)
            # 简化：直接编译检查
        compiled = [str(q.compile(compile_kwargs={"literal_binds": True})) for q in db.executed]
        for c in compiled:
            assert "api_key_id" in c
            assert "'key-9'" in str(c)

    async def test_empty_api_key_no_filter(self):
        db = _FakeDB([(1, '{"type": "Point"}')])
        await _fetch_violation_geometry(db, "file-1", [1], api_key_id="")
        compiled = [str(q.compile(compile_kwargs={"literal_binds": True})) for q in db.executed]
        assert all("api_key_id" not in c for c in compiled)


# ── export_violations_geojson ────────────────────────────────────────────────

class TestExportViolationsGeojson:
    async def test_empty_report_collection(self):
        report = _mk_report()
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []
        assert data["metadata"]["report_id"] == 7
        assert data["metadata"]["total_features"] == 0
        assert data["metadata"]["with_geometry"] == 0

    async def test_topo_violations_with_indices(self):
        report = _mk_report(topology_violations=[
            {"rule_code": "T01", "rule_name": "空几何", "severity": "error",
             "message": "发现 2 条空几何", "feature_indices": [1, 2], "layer": "L1"},
        ])
        db = _FakeDB([
            (1, '{"type": "Point", "coordinates": [0, 0]}'),
            (2, '{"type": "Point", "coordinates": [1, 1]}'),
        ])
        out = await export_violations_geojson(report, db=db)
        data = json.loads(out)
        assert len(data["features"]) == 2
        f1, f2 = data["features"]
        assert f1["properties"]["violation_type"] == "topology_空几何"
        assert f1["properties"]["feature_index"] == 1
        assert f1["geometry"]["coordinates"] == [0, 0]
        assert f2["geometry"]["coordinates"] == [1, 1]
        assert data["metadata"]["with_geometry"] == 2

    async def test_topo_violation_no_indices_summary_feature(self):
        report = _mk_report(topology_violations=[
            {"rule_code": "T03", "rule_name": "极大面", "severity": "warning",
             "message": "发现 5 个极大面", "failed_count": 5},
        ])
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        assert len(data["features"]) == 1
        f = data["features"][0]
        assert f["geometry"] is None
        assert f["properties"]["failed_count"] == 5
        assert f["properties"]["violation_type"] == "topology_极大面"

    async def test_attr_violations_with_samples(self):
        report = _mk_report(constraint_violations=[
            {"rule_code": "A01", "rule_name": "字段为空", "severity": "error",
             "message": "3 条为空", "field": "name", "layer": "L1",
             "failed_count": 3,
             "samples": [{"feature_index": 10, "value": None},
                         {"feature_index": 11, "value": "x"}]},
        ])
        db = _FakeDB([
            (10, '{"type": "Point", "coordinates": [5, 5]}'),
            (11, '{"type": "Point", "coordinates": [6, 6]}'),
        ])
        out = await export_violations_geojson(report, db=db)
        data = json.loads(out)
        assert len(data["features"]) == 2
        f10 = next(f for f in data["features"] if f["properties"]["feature_index"] == 10)
        assert f10["properties"]["violation_type"] == "attribute_A01"
        assert f10["properties"]["field"] == "name"
        assert f10["properties"]["sample_value"] is None

    async def test_attr_violation_no_samples_summary(self):
        report = _mk_report(constraint_violations=[
            {"rule_code": "A02", "rule_name": "超限", "severity": "warning",
             "message": "2 条超限", "field": "pop", "failed_count": 2},
        ])
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        assert len(data["features"]) == 1
        f = data["features"][0]
        assert f["properties"]["violation_type"] == "attribute_A02"
        assert f["properties"]["failed_count"] == 2
        assert f["geometry"] is None

    async def test_legacy_issues_exported(self):
        report = _mk_report(issues=[
            {"issue_type": "geometry_error", "severity": "error",
             "message": "坐标非法", "field": "", "affected_count": 4},
        ])
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        assert len(data["features"]) == 1
        f = data["features"][0]
        assert f["properties"]["violation_type"] == "geometry_error"
        assert f["properties"]["affected_count"] == 4
        assert f["geometry"] is None

    async def test_legacy_issue_samples_indices_collected_for_geometry(self):
        # 旧版 issues 的 samples.feature_index 也进入 all_indices → 触发几何批量查询
        report = _mk_report(issues=[
            {"issue_type": "geo_x", "severity": "warn", "message": "m",
             "samples": [{"feature_index": 3}, {"feature_index": "bad"}]},
        ])
        db = _FakeDB([
            (3, '{"type": "Point", "coordinates": [9, 9]}'),
        ])
        out = await export_violations_geojson(report, db=db)
        assert db.executed  # 几何查询已执行（索引 3 被收集）
        data = json.loads(out)
        assert data["metadata"]["total_features"] == 1  # 旧版 issues 只出汇总 Feature

    async def test_non_dict_and_non_int_skipped(self):
        report = _mk_report(
            topology_violations=["not-dict", None, {"feature_indices": ["9", 3.5]}],
            constraint_violations=["x", {"samples": [{"feature_index": "bad"}]}],
            issues=["y", 42],
        )
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        # 有效 dict：topology(无效索引→汇总) + attribute(无效 sample→汇总)，issues 非 dict 跳过
        types = sorted(f["properties"]["violation_type"] for f in data["features"])
        assert types == ["attribute_", "topology_unknown"]

    async def test_geometry_fetch_exception_logged_and_continues(self):
        class _BoomDB(_FakeDB):
            async def execute(self, q):
                raise RuntimeError("db down")

        report = _mk_report(topology_violations=[
            {"rule_code": "T01", "feature_indices": [1, 2]},
        ])
        out = await export_violations_geojson(report, db=_BoomDB())
        data = json.loads(out)
        # 几何获取失败 → geometry None，但仍导出汇总/索引 Feature
        assert len(data["features"]) == 2
        assert all(f["geometry"] is None for f in data["features"])
        assert data["metadata"]["with_geometry"] == 0

    async def test_duplicate_sample_indices_deduped(self):
        report = _mk_report(constraint_violations=[
            {"rule_code": "A01", "rule_name": "空",
             "samples": [{"feature_index": 3, "value": "a"},
                          {"feature_index": 3, "value": "b"},
                          {"feature_index": 3, "value": "c"}]},
        ])
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        assert len(data["features"]) == 1
        assert data["features"][0]["properties"]["feature_index"] == 3

    async def test_attr_rules_share_field_name_and_layer(self):
        report = _mk_report(constraint_violations=[
            {"rule_code": "X", "rule_name": "Y", "severity": "warning",
             "message": "m", "field_name": "the_field", "layer": "the_layer",
             "failed_count": 1},
        ])
        out = await export_violations_geojson(report, db=None)
        data = json.loads(out)
        f = data["features"][0]
        assert f["properties"]["field"] == "the_field"
        assert f["properties"]["layer"] == "the_layer"


# ── export_violations_excel ──────────────────────────────────────────────────

class TestExportViolationsExcel:
    def _load(self, path):
        from openpyxl import load_workbook
        return load_workbook(path)

    def test_excel_basic_structure(self, tmp_path):
        report = _mk_report(
            topology_violations=[
                {"rule_code": "T01", "rule_name": "空几何", "severity": "error",
                 "message": "2 条空几何", "layer": "L1", "failed_count": 2,
                 "feature_indices": [1, 2, 3]},
            ],
            constraint_violations=[
                {"rule_code": "A01", "rule_name": "字段为空", "severity": "error",
                 "message": "m", "field": "name", "layer": "L1", "failed_count": 1},
            ],
            issues=[
                {"issue_type": "geo_bad", "severity": "error",
                 "message": "坐标非法", "affected_count": 4},
            ],
        )
        out_path = str(tmp_path / "out.xlsx")
        result = export_violations_excel(report, out_path)
        assert result == out_path

        wb = self._load(out_path)
        ws = wb.active
        assert ws.title == "违规清单"
        # 头部行
        assert "质检报告:" in str(ws["A1"].value)
        assert "综合评分:" in str(ws["A2"].value)
        # 三行违规数据（第 5 行表头开始？行 5 起始 → row 5,6,7）
        headers = [c.value for c in ws[5]]
        assert headers[0] == "违规类型"
        assert headers[1] == "规则码"
        # 拓扑违规行
        row6 = [c.value for c in ws[6]]
        assert row6[0] == "topology_空几何"
        assert row6[1] == "T01"
        assert row6[8] == "1,2,3"  # feature_indices 前 20
        # 属性违规行
        row7 = [c.value for c in ws[7]]
        assert row7[0].startswith("attribute_")
        assert row7[5] == "name"
        # 旧版 issue 行
        row8 = [c.value for c in ws[8]]
        assert row8[0] == "geo_bad"
        assert row8[7] == 4

    def test_excel_empty_report(self, tmp_path):
        report = _mk_report()
        out_path = str(tmp_path / "empty.xlsx")
        export_violations_excel(report, out_path)
        wb = self._load(out_path)
        ws = wb.active
        # 只有头部，无违规行
        headers = [c.value for c in ws[5]]
        assert headers[6] == "图层"  # 表头正常
        assert ws[6][0].value in (None, "")

    def test_excel_non_dict_entries_skipped(self, tmp_path):
        report = _mk_report(topology_violations=["x", None, {"rule_code": "T05", "message": "m"}],
                            constraint_violations=[42, {"rule_code": "A7"}],
                            issues=["nope"])
        out_path = str(tmp_path / "skip.xlsx")
        export_violations_excel(report, out_path)
        wb = self._load(out_path)
        ws = wb.active
        # 只有 2 行违规 (T05 + A7)
        assert ws[6][0].value == "topology_"
        assert ws[7][0].value == "attribute_A7"
        assert ws[8][0].value in (None, "")

    def test_excel_issue_row(self, tmp_path):
        report = _mk_report(issues=[{"issue_type": "issue_x", "severity": "warn",
                                      "message": "m", "field": "f", "affected_count": 9}])
        out_path = str(tmp_path / "issue.xlsx")
        export_violations_excel(report, out_path)
        wb = self._load(out_path)
        ws = wb.active
        row = [c.value for c in ws[6]]
        assert row[0] == "issue_x"
        assert row[5] == "f"
        assert row[7] == 9