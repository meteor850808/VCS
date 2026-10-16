"""P6e-3: export_service / mdb_processor / progress_tracker /
script_generation / quality.reporter / workers.beat_schedule → 100%。

- export_service: 非标量属性丢弃 / bool float 属性 schema / 无几何类型 raise /
  fid 兜底行
- mdb_processor: fake pyodbc 全流程（系统表/无几何列/空 shape/坏 shape/
  表异常/GDB_SpatialRefs、GeomColumns 异常/bytes 与 datetime 属性序列化）
- progress_tracker: fake redis 成功路径 + publish batch
- script_generation: 不支持的布尔操作符
- reporter: 低分颜色 / 未知 severity 兜底
- beat_schedule: stat OSError 跳过条目
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import export_service as exp
from app.services import mdb_processor as mp
from app.services import progress_tracker as pt
from app.services import script_generation as sg
from app.services.quality.models import QualityIssue, QualityResult
from app.services.quality.reporter import generate_html_report, _score_color
from app.workers import beat_schedule as bs


# ── export_service ────────────────────────────────────────────────────────────

class TestExportService:

    def test_scalar_props_drops_non_scalar(self):
        """55-56：非标量属性被丢弃。"""
        keep, dropped = exp._scalar_props(
            {"s": "a", "n": 1, "b": True, "lst": [1, 2], "d": {"k": 1}})
        assert keep == {"s": "a", "n": 1, "b": True}
        assert dropped == ["lst", "d"]

    def test_build_schema_bool_float(self):
        """72/76：bool → str，float → float。"""
        feats = [{"geometry": {"type": "Point"},
                  "properties": {"flag": True, "f": 1.5, "i": 3}}]
        schema, nprops = exp._build_schema(feats)
        assert schema["properties"] == {"flag": "str", "f": "float", "i": "int"}
        assert nprops == 3

    def test_build_schema_no_geometry_raises(self):
        """79-80：无几何类型 → ValueError。"""
        with pytest.raises(ValueError, match="无法从转换结果推导几何类型"):
            exp._build_schema([{"geometry": None, "properties": {}}])

    def test_fiona_records_fid_fallback(self):
        """94-95：无属性 schema → 仅 fid 行。"""
        feats = [{"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [0, 0]},
                  "properties": {}}]
        records = exp._fiona_records(feats, {})
        assert records[0]["properties"] == {"fid": 0}
        assert records[0]["id"] == "0"

    def test_fiona_records_fill_missing_keys(self):
        """97-99：补齐缺失属性键。"""
        feats = [{"type": "Feature", "geometry": None, "properties": {"a": 1}}]
        records = exp._fiona_records(feats, {"a": "int", "b": "str"})
        assert records[0]["properties"] == {"a": 1, "b": None}


# ── mdb_processor ─────────────────────────────────────────────────────────────

class TestMdbProcessor:

    def test_is_system_table(self):
        assert mp._is_esri_system_table("GDB_Items") is True
        assert mp._is_esri_system_table("MSysObjects") is True
        assert mp._is_esri_system_table("ROAD_SHAPE_Index") is True
        assert mp._is_esri_system_table("CITY") is False

    def test_geometry_column(self):
        assert mp._is_geometry_column("Shape") is True
        assert mp._is_geometry_column("NAME") is False

    def test_serializable_properties(self):
        """260/264：bytes → hex，其他复杂类型 → str。"""
        out = mp._serializable_properties({
            "blob": b"\x01\x02",
            "dt": datetime(2020, 1, 2, 3, 4, 5),
            "num": 7,
            "none": None,
        })
        assert out["blob"] == "0102"
        assert out["dt"] == "2020-01-02 03:04:05"
        assert out["num"] == 7 and out["none"] is None

    def test_load_crs_map_errors(self):
        """148-149 / 160-161：系统表读取异常 → 静默降级。"""

        class BoomCursor:
            def execute(self, sql, *a):
                if "GDB_SpatialRefs" in sql:
                    raise RuntimeError("no SpatialRefs")
                if "GDB_GeomColumns" in sql:
                    raise RuntimeError("no GeomColumns")

            def fetchall(self):
                return []

        r = mp._load_crs_map(BoomCursor())
        assert r == {}

    def test_load_crs_map_full(self):
        """144-159：正常读取 + None 行跳过。"""
        calls = {"n": 0}

        class Cursor2:
            def execute(self, sql, *a):
                pass

            def fetchall(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    return [(1001, "WKT1"), (None, None)]
                return [(None, 1001), ("ROAD", 1001), ("CITY", None)]

        r = mp._load_crs_map(Cursor2())
        assert r["ROAD"] == (1001, "WKT1")
        assert r["CITY"] == (None, None)

    def _fake_pyodbc(self):
        """构造 fake pyodbc 驱动模拟一次完整提取。"""
        import struct

        point_blob = struct.pack("<I", 1) + struct.pack("<dd", 1.0, 2.0)

        class Cursor:
            def __init__(self):
                self._mode = None
                self._table = None

            @property
            def description(self):
                return [("SHAPE",), ("NAME",)]

            def tables(self, tableType="TABLE"):
                return [SimpleNamespace(table_name=t) for t in
                        ("GDB_Items", "POINTS", "NODATA", "BADROW")]

            def execute(self, sql, *a):
                if "GDB_SpatialRefs" in sql:
                    self._mode = "crs"
                elif "GDB_GeomColumns" in sql:
                    self._mode = "geom"
                else:
                    self._table = sql

            def fetchall(self):
                if self._mode == "crs":
                    self._mode = None
                    return [(1001, "PROJCS[...]")]   # (srid, srtext)
                if self._mode == "geom":
                    self._mode = None
                    return [("POINTS", 1001)]        # (table_name, srid)
                if self._table and "POINTS" in self._table:
                    # 行顺序与 description 对齐: (SHAPE, NAME)
                    return [(point_blob, "A"),          # 正常
                            (None, "B"),                # 82-83 空 shape
                            (b"\xff\xff\xff", "C"),     # 86-87 decode 失败
                            (point_blob, b"\x01\x02")]  # 属性 bytes → 260 序列化
                if self._table and "NODATA" in self._table:
                    return []
                if self._table and "BADROW" in self._table:
                    raise RuntimeError("broken table")
                return []

        class Conn:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        class FakePyodbc:
            Error = RuntimeError

            @staticmethod
            def connect(conn_str):
                return Conn()

        return FakePyodbc

    def test_extract_mdb_features_full(self, monkeypatch):
        """56 / 63-66 / 82-87 / 116-118 / 89-114 全流程。"""
        monkeypatch.setitem(sys.modules, "pyodbc", self._fake_pyodbc())
        results = mp.extract_mdb_features("C:/fake.mdb")
        # 只有 POINTS 有有效要素输出
        assert len(results) == 1
        t = results[0]
        assert t["table_name"] == "POINTS"
        assert t["geometry_type"] == "Point"
        assert t["crs_srid"] == 1001
        assert len(t["features"]) == 2  # row A 正常 + row(blob, bytes 属性)
        assert t["features"][0]["geometry"]["type"] == "Point"

    def test_extract_connect_error(self, monkeypatch):
        """122-124：连接失败 → pyodbc.Error 抛出。"""

        class Boom:
            Error = RuntimeError

            @staticmethod
            def connect(conn_str):
                raise RuntimeError("cannot connect")

        monkeypatch.setitem(sys.modules, "pyodbc", Boom())
        with pytest.raises(RuntimeError):
            mp.extract_mdb_features("C:/nope.mdb")


# ── progress_tracker ──────────────────────────────────────────────────────────

class TestProgressTracker:

    def _install_fake_redis(self, monkeypatch):
        fake_redis = MagicMock()
        fake_redis.from_url.return_value.ping.return_value = True
        fake_redis.from_url.return_value.publish.return_value = 1

        class Mod:
            from_url = staticmethod(fake_redis.from_url)

        monkeypatch.setitem(sys.modules, "redis", Mod())
        monkeypatch.setattr(pt, "_redis", None)
        return fake_redis

    def test_get_redis_connect_success(self, monkeypatch):
        """16-26：成功连接路径。"""
        fake = self._install_fake_redis(monkeypatch)
        r = pt._get_redis()
        assert r is not None
        fake.from_url.assert_called_once()

    def test_publish_progress_batch(self, monkeypatch):
        """43-61：job + batch 双通道。"""
        fake = self._install_fake_redis(monkeypatch)
        pt.publish_progress("j1", "check", 1, 5, "running", "m", batch_job_id="b1")
        payload = json.loads(fake.from_url.return_value.publish.call_args_list[0][0][1])
        assert payload["job_id"] == "j1" and payload["progress"] == 0.2
        chan_batch = fake.from_url.return_value.publish.call_args_list[1][0][0]
        assert chan_batch == "progress:batch:b1"
        pt._redis = None  # 复位

    def test_publish_batch_update(self, monkeypatch):
        """76-89：批量汇总。"""
        fake = self._install_fake_redis(monkeypatch)
        pt.publish_batch_update("b9", "done", 10, 8, 2)
        ch, payload = fake.from_url.return_value.publish.call_args_list[0][0]
        assert ch == "progress:batch:b9"
        assert json.loads(payload)["success_count"] == 8
        pt._redis = None  # 复位

    def test_publish_disabled_returns(self, monkeypatch):
        monkeypatch.setattr(pt, "_redis", False)
        assert pt.publish_progress("j", "s", 1) is None
        assert pt.publish_batch_update("b", "s", 1, 1, 0) is None
        pt._redis = None  # 复位


# ── script_generation ─────────────────────────────────────────────────────────

class TestScriptGeneration:

    def test_unsupported_bool_op(self):
        """239：按位与（非布尔组合）→ 拒绝返回 None。"""
        assert sg._safe_where_to_js("(a=1) & (b=2)") is None

    def test_nested_where_javascript(self):
        resolved = sg.generate_fiona_script(sg.ConversionParams(
            input_path="a.shp", output_path="b.geojson",
            src_crs="EPSG:4326", dst_crs="EPSG:3857",
            where_clause="name = 'x' and 数量 > 2",
        ))
        code = resolved.code
        assert "((props.get(\"name\") == 'x') and (props.get(\"数量\") > 2))" in code

    def test_select_fields_embedded(self):
        resolved = sg.generate_fiona_script(sg.ConversionParams(
            input_path="a.shp", output_path="b.geojson",
            src_crs="EPSG:4326", dst_crs="EPSG:4326",
            select_fields=["name", "code"],
        ))
        code = resolved.code
        assert 'SELECT_FIELDS = ["name", "code"]' in code
        assert "{k: props.get(k) for k in SELECT_FIELDS if k in props}" in code


# ── quality.reporter ──────────────────────────────────────────────────────────

class TestReporter:

    @staticmethod
    def _result(score: float, severity: str = "warning") -> QualityResult:
        return QualityResult(
            file_name="f.shp", detected_format="SHP",
            total_features=10, total_fields=3,
            completeness_score=score,
            issues=[QualityIssue(severity=severity, field="f1",
                                 issue_type="null_values",
                                 message="有 5 个空值", affected_count=5)],
            null_value_counts={"f1": 5},
            critical_fields_status={},
            constraint_violations=[], topology_violations=[],
        )

    def test_score_color_low(self):
        """11-15：低分红色。"""
        assert _score_color(0.3) == "#e74c3c"
        assert _score_color(0.6) == "#f39c12"
        assert _score_color(0.9) == "#27ae60"

    def test_generate_html_unknown_severity(self, tmp_path):
        """23-24：未知 severity → 灰色兜底。"""
        out = generate_html_report(self._result(0.3, "unknown"),
                                   str(tmp_path / "r.html"))
        html = Path(out).read_text(encoding="utf-8")
        assert "#666" in html and "●" in html
        assert "#e74c3c" in html  # 低分标题色

    def test_generate_html_no_issues(self, tmp_path):
        r = QualityResult(file_name="g.geojson", detected_format="GeoJSON",
                          total_features=0, total_fields=0,
                          completeness_score=1.0)
        out = generate_html_report(r, str(tmp_path / "g.html"))
        assert "未发现质量问题" in Path(out).read_text(encoding="utf-8")


# ── workers.beat_schedule ─────────────────────────────────────────────────────

class TestBeatSchedule:

    def test_cleanup_stat_oserror_skipped(self, tmp_path, monkeypatch):
        """43-44：无法 stat 的条目跳过，不中断清理。"""
        import os
        import time

        def fake_stat(self, *a, **k):
            if self.name == "blocked":
                raise OSError("EACCES")
            return real_stat(self, *a, **k)

        real_is_dir = Path.is_dir

        def fake_is_dir(self, *a, **k):
            # blocked 的 is_dir 绕过 stat 返回 True，使 entry.stat() 抛 → 43-44
            if self.name == "blocked":
                return True
            return real_is_dir(self, *a, **k)

        uploads = tmp_path / "data" / "uploads"
        (uploads / "olddir").mkdir(parents=True)
        (uploads / "blocked").mkdir()
        old = uploads / "olddir"
        past = time.time() - 8 * 24 * 3600
        os.utime(old, (past, past))

        real_stat = Path.stat
        monkeypatch.setattr(Path, "stat", fake_stat)
        monkeypatch.setattr(Path, "is_dir", fake_is_dir)
        monkeypatch.setattr(bs.settings, "upload_dir", str(uploads))

        out = bs.cleanup_temp_files()
        assert out["cleaned_dirs"] == 1  # olddir 被清理，blocked 跳过
        assert not os.path.exists(uploads / "olddir")
        assert os.path.exists(uploads / "blocked")

    def test_missing_dirs_skipped(self, tmp_path, monkeypatch):
        """27-28：uploads/outputs 不存在 → 直接跳过。"""
        monkeypatch.setattr(bs.settings, "upload_dir",
                            str(tmp_path / "data" / "uploads"))
        out = bs.cleanup_temp_files()
        assert out == {"cleaned_dirs": 0}