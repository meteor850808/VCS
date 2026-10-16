# -*- coding: utf-8 -*-
"""数据下载 → 导出（geojson/shp/gpkg/csv/mdb）的 TDD 测试。

设计语义（v2.19.0）：
- format 缺省且 file_data 非空 → 返回原始字节（向后兼容历史下载）
- 其余情况走导出管线：按 stored_file_id 从三张特征分区表聚合成
  FeatureCollection → geojson/shp/gpkg/csv/mdb 实时导出
- 无原始字节且无特征 → 410；ZIP 壳（is_archive）→ 400 提示展开导出
"""
import json
import os
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.stored_file import StoredFile
from app.models.spatial_feature import PointFeature

# MDB/Access 写入依赖 ADOX/ACE OLEDB + Access ODBC 驱动，仅 Windows 可用
_WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="MDB/Access 写入仅 Windows")

# 同步单元测试（TestExportMdb/TestExportCsv）不标 asyncio；
# 异步端点测试类 TestDownloadExportEndpoint 单独标记。


# ── 单元：export_service.mdb / csv ──────────────────────────────────────────

def _fc(features):
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _feat(geom, layer, **props):
    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {"layer_name": layer, **props},
    }


class TestExportMdb:
    """export_mdb：按 layer_name 拆分多表（MEMO 存几何 JSON），pyodbc 往返可读。"""

    pytestmark = _WIN_ONLY

    def test_export_mdb_layered_tables(self):
        from app.services.export_service import export_mdb
        fc = _fc([
            _feat({"type": "Point", "coordinates": [121.47, 31.23]}, "道路", kind="Q"),
            _feat({"type": "Polygon",
                   "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "用地", kind="A"),
        ])
        data = export_mdb(bytes(__import__("json").dumps(fc), "utf-8"), "测试")
        assert data[:1] == b"\x00"  # Access/Jet 文件头
        assert len(data) > 100_000
        self._assert_mdb_layers(data, {"lyr_道路", "lyr_用地"})

    def test_export_mdb_no_layer_single_table(self):
        from app.services.export_service import export_mdb
        import json
        fc = _fc([
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
             "properties": {}},
        ])
        data = export_mdb(json.dumps(fc).encode(), "a")
        self._assert_mdb_layers(data, {"features"})

    @staticmethod
    def _assert_mdb_layers(data: bytes, expected_tables: set):
        import pyodbc
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"vcs_export_{uuid.uuid4().hex}.mdb")
        try:
            with open(path, "wb") as f:
                f.write(data)
            conn = pyodbc.connect(
                r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + path,
                autocommit=True,
            )
            try:
                cur = conn.cursor()
                for t in expected_tables:
                    cur.execute(f"SELECT COUNT(*) FROM [{t}]")
                    assert cur.fetchone()[0] >= 1
            finally:
                conn.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestExportAccessLiteral:
    """v2.19.3：Access 长文本不能参数绑定（HYC00/106），改内联字面量转义。"""

    def test_literal_scalars(self):
        from app.services.export_service import _access_literal
        assert _access_literal(None) == "NULL"
        assert _access_literal(True) == "1"
        assert _access_literal(False) == "0"
        assert _access_literal(7) == "7"
        assert _access_literal(1.5) == "1.5"
        assert _access_literal("abc") == "'abc'"

    def test_literal_escapes_single_quote(self):
        from app.services.export_service import _access_literal
        assert _access_literal("O'Brien") == "'O''Brien'"

    def test_literal_long_text(self):
        from app.services.export_service import _access_literal
        long = "x" * 10_000
        lit = _access_literal(long)
        assert lit == "'" + long + "'"  # 长文本内联（参数绑定会 HYC00）

    def test_merge_access_type(self):
        from app.services.export_service import (
            _ACCESS_DOUBLE, _ACCESS_INT, _ACCESS_MEMO, _merge_access_type,
        )
        assert _merge_access_type(_ACCESS_INT, 1) == _ACCESS_INT
        assert _merge_access_type(_ACCESS_INT, 1.5) == _ACCESS_DOUBLE
        assert _merge_access_type(_ACCESS_DOUBLE, 3) == _ACCESS_DOUBLE
        assert _merge_access_type(_ACCESS_INT, "abc") == _ACCESS_MEMO
        assert _merge_access_type(_ACCESS_MEMO, 5) == _ACCESS_MEMO
        assert _merge_access_type(_ACCESS_DOUBLE, "5") == _ACCESS_MEMO

    def test_scalar_sql_type_bigint_to_memo(self):
        # v2.19.3：Access INTEGER 为 32-bit，超范围（地籍编号）必须转文本列
        from app.services.export_service import (
            _ACCESS_INT, _ACCESS_MEMO, _scalar_sql_type,
        )
        assert _scalar_sql_type(2**31 - 1) == _ACCESS_INT
        assert _scalar_sql_type(2**31) == _ACCESS_MEMO
        assert _scalar_sql_type(-2**31) == _ACCESS_INT
        assert _scalar_sql_type(-2**31 - 1) == _ACCESS_MEMO
        assert _scalar_sql_type(11_0023_0546_425258) == _ACCESS_MEMO

    @_WIN_ONLY
    def test_export_mdb_bigint_preserved(self):
        # 真实 Access 回读：大整数以 MEMO 文本保整，不回退 DOUBLE 丢精度
        import pyodbc
        from app.services.export_service import export_mdb, _is_jet4_mdb
        props = {
            "layer_name": "大整数",
            "单元编号": 1100230546425258,
            "面积": 81546.82743997556,
            "状态": "历史",
        }
        fc = _fc([_feat({"type": "Point", "coordinates": [1, 2]}, "大整数",
                        **props)])
        blob = export_mdb(json.dumps(fc).encode(), "t")
        import tempfile
        p = tempfile.mktemp(suffix=".mdb")
        with open(p, "wb") as f:
            f.write(blob)
        try:
            # v2.19.4：产物必须是 Jet4 / Access 2000 格式（否则 Access 打不开）
            assert _is_jet4_mdb(p), "导出 .mdb 不是 Jet4 格式（Access 将无法打开）"
            conn = pyodbc.connect(
                r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + p)
            cur = conn.cursor()
            cur.execute(
                "SELECT [f_单元编号], [f_面积], [f_状态] FROM [lyr_大整数]")
            rows = cur.fetchall()
            conn.close()
            assert rows[0][0] == "1100230546425258", f"大整数保整失败: {rows[0][0]!r}"
            assert abs(rows[0][1] - 81546.82743997556) < 1e-6
            assert rows[0][2] == "历史"
        finally:
            os.remove(p)

    @_WIN_ONLY
    def test_is_jet4_mdb(self):
        """_is_jet4_mdb 识别 Jet4 头（Standard Jet DB + ver 0x01）。"""
        import tempfile
        from app.services.export_service import (
            _is_jet4_mdb, export_mdb,
        )
        fc = _fc([_feat({"type": "Point", "coordinates": [1, 2]}, "A")])
        p = tempfile.mktemp(suffix=".mdb")
        with open(p, "wb") as f:
            f.write(export_mdb(json.dumps(fc).encode(), "t"))
        try:
            assert _is_jet4_mdb(p) is True
            # 空文件 / 任意字节 → False
            with open(p, "wb") as f:
                f.write(b"\x00" * 64)
            assert _is_jet4_mdb(p) is False
        finally:
            os.remove(p)

    def test_export_mdb_uses_inline_literals(self, monkeypatch):
        """INSERT 必须为内联字面量（无 '?' 参数占位），长文本含 ''' 转义。"""
        import sys
        from unittest.mock import MagicMock

        import app.services.export_service as es
        monkeypatch.setattr(es, "is_windows", lambda: True)
        def _fake_create(path):
            open(path, "wb").write(b"\x00")
        monkeypatch.setattr(es, "_create_empty_mdb", _fake_create)

        fake_pyodbc = MagicMock()
        fake_cur = fake_pyodbc.connect.return_value.cursor.return_value
        monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)

        long_attr = "线" * 4000  # 超 Access 参数绑定限制的长文本
        fc = _fc([
            _feat({"type": "Point", "coordinates": [121.47, 31.23]},
                  "道路", note="O'Reilly", desc=long_attr),
        ])
        es.export_mdb(json.dumps(fc).encode(), "t")

        sqls = [c.args[0] for c in fake_cur.execute.call_args_list]
        insert_sql = next(s for s in sqls if s.strip().upper().startswith("INSERT"))
        assert "?" not in insert_sql
        assert "O''Reilly" in insert_sql          # 单引号转义
        assert "'" + long_attr + "'" in insert_sql  # 长文本内联
        create_sql = next(s for s in sqls if "CREATE TABLE" in s)
        assert "[f_note]" in create_sql and "[f_desc]" in create_sql
        assert "[f_desc] MEMO" in create_sql


class TestShpAsciiFields:
    """v2.19.3：shapefile .dbf 列名仅允许 ASCII≤10 —— GDB 中文列名映射。"""

    def test_field_map_chinese_names(self):
        from app.services.export_service import _shapefile_field_map
        pmap = _shapefile_field_map(["地块名称(项目名称)", "REMARKS", "VERSION"])
        names = list(pmap.values())
        for n in names:
            assert n.isascii() and len(n) <= 10
        # 稳定可复现
        assert _shapefile_field_map(["地块名称(项目名称)", "REMARKS", "VERSION"]) == pmap

    def test_field_map_conflict_dedup(self):
        from app.services.export_service import _shapefile_field_map
        pmap = _shapefile_field_map(["NameAAA", "nameaaa", "NameAAAB"])
        names = list(pmap.values())
        assert len(set(n.lower() for n in names)) == len(names)  # 无冲突

    def test_field_map_all_chinese_fallback(self):
        from app.services.export_service import _shapefile_field_map
        pmap = _shapefile_field_map(["地块", "道路", "用地"])
        names = list(pmap.values())
        assert names == ["F1", "F2", "F3"]

    def test_export_shapefile_sanitizes_columns(self, monkeypatch):
        """export_shapefile_zip 传给 fiona 的 schema/记录列名必须全 ASCII。"""
        import sys
        from unittest.mock import MagicMock

        import app.services.export_service as es

        captured = {}

        class FakeDst:
            def __init__(self, path, mode, **kwargs):
                captured["schema"] = kwargs.get("schema")
                captured["records"] = None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def writerecords(self, records):
                captured["records"] = records

        fake_fiona = MagicMock()
        fake_fiona.open = FakeDst
        monkeypatch.setitem(sys.modules, "fiona", fake_fiona)

        fc = _fc([
            _feat({"type": "Polygon",
                   "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                  "地块", **{"地块名称(项目名称)": "C06-0203", "REMARKS": "ok"}),
        ])
        es.export_shapefile_zip(json.dumps(fc).encode(), None, "t")

        for col in captured["schema"]["properties"]:
            assert col.isascii() and len(col) <= 10
        # 记录键与 schema 对齐
        rec_props = captured["records"][0]["properties"]
        assert set(rec_props) <= set(captured["schema"]["properties"])
        assert "地块名称(项目名称)" not in rec_props


class TestExportCsv:
    """export_csv：CSV 文本含表头与要素行（几何 → WKT）。"""

    def test_export_csv_basic(self):
        from app.services.export_service import export_csv
        fc = _fc([
            _feat({"type": "Point", "coordinates": [121.47, 31.23]}, "道路", name="甲"),
        ])
        import json
        csv_bytes = export_csv(json.dumps(fc).encode(), "t")
        csv_text = csv_bytes.decode("utf-8")
        lines = csv_text.strip().splitlines()
        header = lines[0]
        assert "geometry" in header and "layer_name" in header and "name" in header
        assert "POINT (121.47 31.23)" in csv_text
        assert "道路" in csv_text


# ── v2.19.5：File Geodatabase (.gdb) 导出 ──────────────────────────────────

class TestExportGdb:
    """export_gdb：OpenFileGDB(GDAL 3.6+) 写 .gdb 目录 → zip 交付。

    File Geodatabase 是 ArcGIS Pro 原生格式（Pro 不支持个人地理数据库 .mdb），
    用 GDAL 的 OpenFileGDB 驱动纯 Python 写入（无 arcpy 依赖）。
    """

    # 可复用的 CRS WKT（EPSG:4527 投影，测试坐标系往返）；
    # 用 pyproj 生成标准 GDAL WKT——生产环境来自 spatial_ref_sys.srtext
    WKT4527 = None

    def _wkt4527(self) -> str:
        if TestExportGdb.WKT4527 is None:
            from pyproj import CRS
            TestExportGdb.WKT4527 = CRS.from_epsg(4527).to_wkt()
        return TestExportGdb.WKT4527

    def _unzip_to_tmp(self, blob: bytes) -> str:
        """把 export_gdb 的 zip 解压到临时目录，返回 .gdb 目录路径。"""
        import tempfile, zipfile, io
        td = tempfile.mkdtemp(prefix="vcs_gdb_test_")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            zf.extractall(td)
        gdb_dirs = [os.path.join(td, n) for n in os.listdir(td) if n.endswith(".gdb")]
        assert len(gdb_dirs) == 1, gdb_dirs
        return gdb_dirs[0]

    def test_export_gdb_layered_feature_classes(self):
        """点层 + 面层 → .gdb 内两个 Feature Class，fiona 读回要素/几何。

        注意：投影 CRS 下多边形坐标也必须是合法投影坐标（OpenFileGDB 写入
        默认带 CHECK_WITH_INVERT_PROJ 域校验，非法域坐标会触发
        "Point outside of projection domain"）。
        """
        from app.services.export_service import export_gdb
        fc = _fc([
            _feat({"type": "Point", "coordinates": [418000.0, 3280000.0]},
                  "道路", kind="Q"),
            _feat({"type": "Polygon",
                   "coordinates": [[[418000.0, 3280000.0], [418100.0, 3280000.0],
                                    [418100.0, 3280100.0], [418000.0, 3280000.0]]]},
                  "用地", kind="A"),
        ])
        import json
        blob = export_gdb(json.dumps(fc).encode(), self._wkt4527(), "测试导出")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb) as src:
            layer_names = sorted(fiona.listlayers(gdb))
            assert layer_names == ["lyr_用地", "lyr_道路"], layer_names
            with fiona.open(gdb, layer="lyr_道路") as lyr:
                assert len(lyr) == 1
                feat = next(iter(lyr))
                assert feat["geometry"]["type"] == "Point"
                assert feat["properties"]["kind"] == "Q"
            with fiona.open(gdb, layer="lyr_用地") as lyr:
                assert len(lyr) == 1
                # OpenFileGDB 存储 Polygon 为 MultiPolygon（OGR 行为）
                assert next(iter(lyr))["geometry"]["type"] == "MultiPolygon"
                # layer_name 是表维度列，不冗余进属性
                assert "layer_name" not in lyr.schema["properties"]

    def test_export_gdb_mixed_geometry_same_layer(self):
        """同一 layer 内点+线混合 → 按几何类型拆两个 Feature Class。"""
        from app.services.export_service import export_gdb
        fc = _fc([
            _feat({"type": "Point", "coordinates": [1, 2]}, "混合", k=1),
            _feat({"type": "LineString", "coordinates": [[0, 0], [5, 5]]},
                  "混合", k=2),
        ])
        import json
        blob = export_gdb(json.dumps(fc).encode(), None, "mix")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb) as src:
            layer_names = sorted(fiona.listlayers(gdb))
            assert layer_names == ["lyr_混合_LineString", "lyr_混合_Point"], layer_names

    def test_export_gdb_negative_coords(self):
        """负坐标（AutoCAD 图纸坐标，坐标含负数）可正常写入。

        回归 v2.19.5：OpenFileGDB 默认网格原点 XORIGIN/YORIGIN 为 0/负偏置，
        负坐标触发 "Cannot encode Y value" 写层中断。export_gdb 按数据 bbox
        自动计算原点后，图纸坐标（如 DWG -10665~13548 范围）可正常落盘。
        """
        from app.services.export_service import export_gdb
        fc = _fc([
            _feat({"type": "Point", "coordinates": [13548.9, -10665.3]},
                  "图纸", k=1),
            _feat({"type": "Polygon",
                   "coordinates": [[[-10890.2, -10667.7], [13607.1, -10667.7],
                                    [13607.1, 13550.6], [-10890.2, -10667.7]]]},
                  "图纸", k=2),
        ])
        import json
        blob = export_gdb(json.dumps(fc).encode(), None, "dwg_neg")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb) as src:
            layer_names = sorted(fiona.listlayers(gdb))
            assert layer_names == ["lyr_图纸_Point", "lyr_图纸_Polygon"], layer_names
            # 读回验证坐标保真（MultiPolygon 壳坐标与写入一致）
            with fiona.open(gdb, layer="lyr_图纸_Polygon") as lyr:
                coords = next(iter(lyr))["geometry"]["coordinates"]
                flat = []
                def _walk(o):
                    if isinstance(o, (int, float)):
                        flat.append(o)
                    elif isinstance(o, (list, tuple)):
                        for i in o:
                            _walk(i)
                _walk(coords)
                # 坐标经 GDB 网格量化（默认 1e-9 弧度 / 1e-4 米）存在
                # 1e-12 级浮点残差，属正常（ArcGIS 打开无感知）
                assert min(flat) == pytest.approx(-10890.2, abs=1e-6), min(flat)
                assert max(flat) == pytest.approx(13607.1, abs=1e-6), max(flat)

    def test_export_gdb_no_layer_fallback(self):
        """无 layer_name 属性 → 单一 features Feature Class。"""
        from app.services.export_service import export_gdb
        fc = _fc([
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [1, 2]},
             "properties": {}},
        ])
        import json
        blob = export_gdb(json.dumps(fc).encode(), None, "nolayer")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb) as src:
            assert fiona.listlayers(gdb) == ["features"]

    def test_export_gdb_crs_roundtrip(self):
        """传入 WKT → Feature Class 的 CRS 可读回且含投影定义。"""
        from app.services.export_service import export_gdb
        fc = _fc([_feat({"type": "Point", "coordinates": [418000.0, 3280000.0]},
                        "A", name="测")])
        import json
        blob = export_gdb(json.dumps(fc).encode(), self._wkt4527(), "crs_test")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb, layer="lyr_A") as lyr:
            crs = lyr.crs
            wkt = lyr.crs_wkt or ""
            assert crs.to_epsg() == 4527 or "CGCS2000" in wkt, (crs, wkt[:200])
            assert "PROJCS" in wkt

    def test_export_gdb_bigint_preserved(self):
        """大整数属性（地籍编号 1100230546425258）以 int64 保整回读。"""
        from app.services.export_service import export_gdb
        props = {
            "layer_name": "大整数",
            "单元编号": 1100230546425258,
            "面积": 81546.82743997556,
            "状态": "历史",
        }
        fc = _fc([_feat({"type": "Point", "coordinates": [1, 2]}, "大整数",
                        **props)])
        import json
        blob = export_gdb(json.dumps(fc).encode(), None, "bigint")
        gdb = self._unzip_to_tmp(blob)
        import fiona
        with fiona.open(gdb, layer="lyr_大整数") as lyr:
            # OpenFileGDB 经 TARGET_ARCGIS_VERSION=PRO_3_2 存 Integer64；
            # fiona 读回 schema 类型显示 "int"（64-bit 已无损）
            assert lyr.schema["properties"]["单元编号"] == "int", lyr.schema
            feat = next(iter(lyr))
            assert feat["properties"]["单元编号"] == 1100230546425258
            assert feat["properties"]["状态"] == "历史"
            # layer_name 是表维度列，不应冗余出现在属性中
            assert "layer_name" not in lyr.schema["properties"]

    def test_export_gdb_zip_structure(self):
        """zip 根含 <base>.gdb/ 目录与 gdbtable/gdb 系统文件。"""
        from app.services.export_service import export_gdb
        fc = _fc([_feat({"type": "Point", "coordinates": [1, 2]}, "A")])
        import json, zipfile, io
        blob = export_gdb(json.dumps(fc).encode(), None, "结构测试")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
        assert any(n.startswith("结构测试.gdb/") for n in names), names[:10]
        assert any(n.endswith(".gdbtable") for n in names), names[:10]
        assert any(n.endswith("gdb") for n in names), names[:10]  # gdb 系统目录


# ── v2.19.1：混合几何类型分组导出 ──────────────────────────────────────────

class TestMixedGeometryExport:
    """DWG/GDB 真实数据常混合 Point+LineString+Polygon；SHP/GPKG 单层限一种类型。"""

    def test_shapefile_zip_split_by_geom_type(self):
        from app.services.export_service import export_shapefile_zip
        import zipfile, io
        fc = _fc([
            _feat({"type": "Point", "coordinates": [1, 2]}, "L1"),
            _feat({"type": "Point", "coordinates": [3, 4]}, "L2"),
            _feat({"type": "LineString", "coordinates": [[0, 0], [5, 5]]}, "L3"),
            _feat({"type": "MultiPolygon", "coordinates": [[[[0, 0], [0, 1], [1, 1], [0, 0]]]]}, "L4"),
        ])
        z = zipfile.ZipFile(io.BytesIO(export_shapefile_zip(json.dumps(fc).encode(), None, "混")))
        shps = sorted(n for n in z.namelist() if n.endswith(".shp"))
        assert set(shps) == {"混_Point.shp", "混_LineString.shp", "混_MultiPolygon.shp"}, shps
        for n in shps:
            assert ("混_" + n.split(".")[0].split("_")[-1] + ".prj" in z.namelist()) is False  # 无 wkt 不写 prj

    def test_gpkg_layers_per_geom_type(self):
        from app.services.export_service import export_gpkg
        import zipfile, io
        fc = _fc([
            _feat({"type": "Point", "coordinates": [1, 2]}, "L1"),
            _feat({"type": "LineString", "coordinates": [[0, 0], [5, 5]]}, "L2"),
        ])
        z = zipfile.ZipFile(io.BytesIO(export_gpkg(json.dumps(fc).encode(), None, "mix_geom")))
        names = z.namelist()
        assert set(names) == {"mix_geom_Point.gpkg", "mix_geom_LineString.gpkg"}, names
        for n in names:
            with z.open(n) as fh:
                assert fh.read()[:4] == b"SQLi"  # sqlite magic
            # 读回要素数
            import tempfile, os
            import fiona
            p = os.path.join(tempfile.gettempdir(), n)
            with open(p, "wb") as f:
                f.write(z.read(n))
            try:
                with fiona.open(p) as src:
                    assert len(src) == 1
            finally:
                os.remove(p)

    def test_single_geom_keeps_legacy_name(self):
        from app.services.export_service import export_shapefile_zip
        import zipfile, io
        fc = _fc([_feat({"type": "Point", "coordinates": [1, 2]}, "L1")])
        z = zipfile.ZipFile(io.BytesIO(export_shapefile_zip(json.dumps(fc).encode(), None, "单")))
        assert any(n == "单.shp" for n in z.namelist())


# ── 端点：/data/{file_id}/download?format=... ───────────────────────────────

@pytest_asyncio.fixture
async def _file_with_point_feature(db_session):
    """造一个无原始字节、带 1 条 Point 特征的文件。"""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey

    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name="exp_a", is_active=True)
    db_session.add(key)
    await db_session.flush()

    sf = StoredFile(
        id=str(uuid.uuid4()),
        original_name="测试点.geojson",
        api_key_id=key.id,
        data_type="geometry",
        geometry_type="Point",
        file_format="geojson",
        feature_count=1,
        content_type="application/geo+json",
        file_size=0,
        file_data=None,
        detected_crs="EPSG:4326",
        detected_crs_srid=4326,
        crs_is_custom=False,
        layer_count=1,
        point_count=1,
        line_count=0,
        polygon_count=0,
        field_schema=[{"name": "name", "field_type": "string"}],
    )
    db_session.add(sf)
    await db_session.flush()
    sid = sf.id
    safe = sid.replace("-", "_")
    await db_session.execute(text(
        f'CREATE TABLE IF NOT EXISTS point_features_p_{safe} '
        f"PARTITION OF point_features FOR VALUES IN ('{sid}')"
    ))
    await db_session.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_pt_p_{safe}_idx '
        f'ON point_features_p_{safe} (stored_file_id)'
    ))
    db_session.add(PointFeature(
        id=str(uuid.uuid4()), stored_file_id=sid, api_key_id=key.id,
        layer_name="测试点", source_name="测试点.geojson",
        feature_index=0, geometry_type="Point",
        properties={"name": "A点"},
        geometry=text("ST_GeomFromText('POINT(121.47 31.23)', 4326)"),
    ))
    await db_session.commit()
    return {"id": sid, "key": raw, "object": sf}


@pytest_asyncio.fixture
async def _file_with_point_feature_custom_crs(db_session):
    """带自定义坐标系(918984 / PROJCS) + 1 条 Point 特征的文件。"""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey

    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name="exp_crs", is_active=True)
    db_session.add(key)
    await db_session.flush()

    projcs_wkt = (
        'PROJCS["Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
        'UNIT["degree",0.0174532925199433]],PROJECTION["Mercator_1SP"],'
        'PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],'
        'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
        'UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
    )
    sf = StoredFile(
        id=str(uuid.uuid4()),
        original_name="自定义系.shp",
        api_key_id=key.id,
        data_type="geometry",
        geometry_type="Point",
        file_format="shp",
        feature_count=1,
        content_type="application/octet-stream",
        file_size=0,
        file_data=None,
        detected_crs=projcs_wkt,
        detected_crs_srid=918984,
        crs_is_custom=True,
        layer_count=1,
        point_count=1,
    )
    db_session.add(sf)
    await db_session.flush()
    sid = sf.id
    safe = sid.replace("-", "_")
    await db_session.execute(text(
        f'CREATE TABLE IF NOT EXISTS point_features_p_{safe} '
        f"PARTITION OF point_features FOR VALUES IN ('{sid}')"
    ))
    db_session.add(PointFeature(
        id=str(uuid.uuid4()), stored_file_id=sid, api_key_id=key.id,
        layer_name="图层A", source_name="自定义系.shp",
        feature_index=0, geometry_type="Point",
        properties={"name": "A点"},
        geometry=text("ST_GeomFromText('POINT(121.47 31.23)', 918984)"),
    ))
    await db_session.commit()
    return {"id": sid, "key": raw}


class TestDownloadExportEndpoint:
    pytestmark = pytest.mark.asyncio

    async def test_export_geojson_from_features(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=geojson",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        assert body["features"][0]["properties"]["name"] == "A点"
        assert body["crs"]["properties"]["name"] == "urn:ogc:def:crs:EPSG::4326"

    async def test_export_shp_zip(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=shp",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        import zipfile, io
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert any(n.endswith(".shp") for n in names)
        assert any(n.endswith(".prj") for n in names)

    async def test_export_gpkg(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=gpkg",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        assert resp.content[:4] == b"SQLi"  # sqlite magic

    @_WIN_ONLY
    async def test_export_mdb(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=mdb",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        assert resp.content[:1] == b"\x00"
        assert "application/msaccess" in resp.headers.get("content-type", "")

    async def test_export_gdb(self, client, _file_with_point_feature):
        """format=gdb → 200，zip 内含 .gdb 目录（ArcGIS Pro 原生格式）。"""
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=gdb",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        assert resp.content[:2] == b"PK"  # zip magic
        assert "application/zip" in resp.headers.get("content-type", "")
        assert "gdb" in resp.headers.get("content-disposition", "")
        import zipfile, io
        names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
        assert any(n.endswith(".gdbtable") for n in names), names[:10]

    async def test_export_csv(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=csv",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "POINT" in resp.text

    async def test_raw_download_when_file_data_and_no_format(
        self, client, db_session
    ):
        """向后兼容：有原始字节且省略 format → 原样返回原始字节。"""
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="exp_raw", is_active=True)
        db_session.add(key)
        await db_session.flush()
        sf = StoredFile(
            id=str(uuid.uuid4()), original_name="raw.geojson", api_key_id=key.id,
            file_data=b'{"type":"FeatureCollection"}', file_size=34,
            content_type="application/geo+json", data_type="geometry",
            geometry_type="Point", file_format="geojson", feature_count=0,
            detected_crs_srid=4326, crs_is_custom=False,
        )
        db_session.add(sf)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/data/{sf.id}/download", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b'{"type":"FeatureCollection"}'

    async def test_410_no_data_no_features(self, client, db_session):
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="exp_410", is_active=True)
        db_session.add(key)
        await db_session.flush()
        sf = StoredFile(
            id=str(uuid.uuid4()), original_name="empty.geojson", api_key_id=key.id,
            file_data=None, data_type="geometry", geometry_type="Point",
            file_format="geojson", feature_count=0, detected_crs_srid=4326,
            crs_is_custom=False,
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sf.id}/download?format=geojson",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 410, resp.text

    async def test_zip_archive_400(self, client, db_session):
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="exp_zip", is_active=True)
        db_session.add(key)
        await db_session.flush()
        sf = StoredFile(
            id=str(uuid.uuid4()), original_name="a.zip", api_key_id=key.id,
            file_data=None, is_archive=True, data_type="archive",
            file_format="zip", feature_count=0, detected_crs_srid=4326,
            crs_is_custom=False,
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sf.id}/download?format=geojson",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 400, resp.text
        assert "压缩包" in resp.json()["detail"]

    async def test_invalid_format_400(self, client, _file_with_point_feature):
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=kmz",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 400, resp.text

    async def test_not_found_404(self, client, _file_with_point_feature):
        raw = _file_with_point_feature["key"]
        resp = await client.get(
            "/api/v1/data/deadbeef-dead-beef-dead-beefdeadbeef/download?format=geojson",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 404, resp.text

    async def test_export_shp_custom_crs_projcs(self, client, _file_with_point_feature_custom_crs):
        """自定义 SRID(≥910000) + PROJCS → srtext 无行时回落 detected_crs(WKT)。"""
        fid = _file_with_point_feature_custom_crs["id"]
        raw = _file_with_point_feature_custom_crs["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=shp",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        import zipfile, io
        names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
        assert any(n.endswith(".prj") for n in names)

    async def test_export_shp_custom_crs_no_projcs(self, client, db_session):
        """自定义 SRID 且 detected_crs 非 PROJCS → 返回 None，shp 无 .prj。"""
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="exp_nop", is_active=True)
        db_session.add(key)
        await db_session.flush()
        sf = StoredFile(
            id=str(uuid.uuid4()), original_name="noprj.geojson", api_key_id=key.id,
            data_type="geometry", geometry_type="Point", file_format="geojson",
            feature_count=1, file_data=None, content_type="application/geo+json",
            file_size=0, detected_crs="未知坐标系", detected_crs_srid=918985,
            crs_is_custom=True, point_count=1, layer_count=1,
        )
        db_session.add(sf)
        await db_session.flush()
        sid = sf.id
        safe = sid.replace("-", "_")
        await db_session.execute(text(
            f'CREATE TABLE IF NOT EXISTS point_features_p_{safe} '
            f"PARTITION OF point_features FOR VALUES IN ('{sid}')"
        ))
        db_session.add(PointFeature(
            id=str(uuid.uuid4()), stored_file_id=sid, api_key_id=key.id,
            layer_name="L", source_name="noprj.geojson",
            feature_index=0, geometry_type="Point", properties={},
            geometry=text("ST_GeomFromText('POINT(121.47 31.23)', 918985)"),
        ))
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sid}/download?format=shp",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        import zipfile, io
        names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
        assert not any(n.endswith(".prj") for n in names)

    async def test_export_gpkg_pyproj_fallback(self, client, db_session):
        """标准 SRID 但 spatial_ref_sys 无行 → pyproj CRS 兜底（异常则回落 None）。"""
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="exp_pyr", is_active=True)
        db_session.add(key)
        await db_session.flush()
        sf = StoredFile(
            id=str(uuid.uuid4()), original_name="pyr.gpkg", api_key_id=key.id,
            data_type="geometry", geometry_type="Point", file_format="geojson",
            feature_count=1, file_data=None, content_type="application/geo+json",
            file_size=0, detected_crs="EPSG:9999", detected_crs_srid=9999,
            crs_is_custom=False, point_count=1, layer_count=1,
        )
        db_session.add(sf)
        await db_session.flush()
        sid = sf.id
        safe = sid.replace("-", "_")
        await db_session.execute(text(
            f'CREATE TABLE IF NOT EXISTS point_features_p_{safe} '
            f"PARTITION OF point_features FOR VALUES IN ('{sid}')"
        ))
        db_session.add(PointFeature(
            id=str(uuid.uuid4()), stored_file_id=sid, api_key_id=key.id,
            layer_name="L", source_name="pyr.gpkg",
            feature_index=0, geometry_type="Point", properties={},
            geometry=text("ST_GeomFromText('POINT(1 1)', 9999)"),
        ))
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sid}/download?format=gpkg",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200, resp.text
        assert resp.content[:4] == b"SQLi"  # sqlite magic


class TestResolveExportWkt:
    """_resolve_export_wkt 分支（P7 覆盖回跌收口）。"""

    async def test_projcs_invalid_wkt_returns_none(self, monkeypatch):
        """detected_crs 含 PROJCS 但 pyproj 校验失败 → 回落 None（L1589-1590）。"""
        import pyproj
        from app.api.v1.data import _resolve_export_wkt

        class _BoomCRS:
            @staticmethod
            def from_wkt(_wkt):
                raise pyproj.CRSException("invalid WKT")

        monkeypatch.setattr(pyproj, "CRS", _BoomCRS)
        # srid=0 → 跳过第一条 if（falsy）；检测 CRS 含 PROJCS 进入校验分支
        result = await _resolve_export_wkt(object(), 0, 'PROJCS["X"]')
        assert result is None


class TestDownloadExport500:
    pytestmark = pytest.mark.asyncio

    async def test_export_internal_error_500(
        self, client, _file_with_point_feature, monkeypatch
    ):
        """导出管线抛异常 → 500（L1709-1711）。"""
        import app.api.v1.data as data_mod

        async def _boom(*_a, **_k):
            raise RuntimeError("simulated export failure")

        monkeypatch.setattr(data_mod, "_run_export_sync", _boom)
        fid, raw = _file_with_point_feature["id"], _file_with_point_feature["key"]
        resp = await client.get(
            f"/api/v1/data/{fid}/download?format=geojson",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 500, resp.text
        assert "导出失败" in resp.json()["detail"]