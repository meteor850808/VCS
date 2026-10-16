# -*- coding: utf-8 -*-
"""export_service 剩余分支测试（P7 覆盖回跌收口：export_service 92%→100%）。

覆盖 BASELINE §17「覆盖回跌」最后一项的分散 miss：
- `_build_gdb_schema`：跨行 int64 升级 / bool→str / 无几何 raise
- `_gdb_origin`：空坐标跳过 / 全空 → (0,0)
- `_scalar_sql_type`：bool → TEXT
- Jet4 模板链：模板缺失 → None / _is_jet4_mdb OSError / ADOX 回退
- export_mdb / export_csv：shape(g).wkt 异常兜底
- _drop_all_user_tables / export_gpkg 混合几何 wkt 分支
"""
import io
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest

_CUSTOM_WKT = (
    'PROJCS["ShanghaiCity",GEOGCS["GCS_Beijing_1954",DATUM["D_Beijing_1954",'
    'SPHEROID["Krassowsky_1940",6378245,298.3]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",121.275],'
    'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",-3457147.81],UNIT["metre",1]]'
)


def _feat(geom_type: str, coords, layer: str = "WALL", **props):
    return {
        "type": "Feature",
        "geometry": {"type": geom_type, "coordinates": coords},
        "properties": {"layer_name": layer, **props},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


class TestBuildGdbSchema:
    """_build_gdb_schema：OpenFileGDB 专用 schema 推导。"""

    def test_cross_row_int64_upgrade(self):
        """同一属性跨行：首行 int 在 32-bit 内，次行超界 → 整列升 int64。"""
        from app.services.export_service import _build_gdb_schema
        feats = [
            _feat("Point", [1, 1], layer="A", code=100),
            _feat("Point", [2, 2], layer="A", code=2**40),  # 超 32-bit
        ]
        schema, count = _build_gdb_schema(feats)
        assert schema["properties"]["code"] == "int64"
        assert schema["geometry"] == "Point"
        # layer_name 也在 props_schema 中（是普通标量属性）
        assert count == 2

    def test_bool_property_maps_to_str(self):
        """boolean 属性 → "str"（dbf bool 不稳定）。"""
        from app.services.export_service import _build_gdb_schema
        feats = [
            _feat("Point", [1, 1], layer="A", active=True),
        ]
        schema, _ = _build_gdb_schema(feats)
        assert schema["properties"]["active"] == "str"

    def test_no_geometry_raises(self):
        """全部要素无有效几何 → ValueError（无法推导几何类型）。"""
        from app.services.export_service import _build_gdb_schema
        feats = [
            {"type": "Feature", "properties": {"a": 1}, "geometry": None},
            {"type": "Feature", "properties": {"a": 2}, "geometry": {}},
        ]
        with pytest.raises(ValueError, match="无法从转换结果推导几何类型"):
            _build_gdb_schema(feats)

    def test_empty_props_defaults_fid(self):
        """属性全空 → properties 默认 {"fid":"int"}。"""
        from app.services.export_service import _build_gdb_schema
        # 无任何 properties 的要素（不含 layer_name）
        feats = [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "properties": {},
        }]
        schema, count = _build_gdb_schema(feats)
        assert schema["properties"] == {"fid": "int"}
        assert count == 0


class TestGdbOrigin:
    """_gdb_origin：OpenFileGDB 网格原点推导。"""

    def test_empty_coordinates_skipped(self):
        """geometry.coordinates 为空（None/[]）→ 跳过该要素。"""
        from app.services.export_service import _gdb_origin
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": None}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": []}},
            _feat("Point", [10.5, 20.5], layer="A"),
        ]
        # 只取有效要素最小值外扩一格
        assert _gdb_origin(feats) == (9, 19)

    def test_no_valid_coords_returns_zero(self):
        """所有要素坐标均空 → (0, 0) 兜底。"""
        from app.services.export_service import _gdb_origin
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": None}},
            {"type": "Feature", "geometry": None},
        ]
        assert _gdb_origin(feats) == (0, 0)

    def test_negative_coords_floor(self):
        """负数坐标取整外扩（避免网格负偏移编码失败）。"""
        from app.services.export_service import _gdb_origin
        feats = [_feat("Point", [-3.2, -5.7], layer="A")]
        # floor(-3.2)=-4 → -5；floor(-5.7)=-6 → -7
        assert _gdb_origin(feats) == (-5, -7)


class TestScalarSqlType:
    """属性 → Access SQL 列类型映射。"""

    def test_bool_maps_to_text(self):
        """bool 属性 → TEXT(16)（Access BOOLEAN 存在互操作问题）。"""
        from app.services.export_service import (
            _ACCESS_TEXT, _scalar_sql_type,
        )
        assert _scalar_sql_type(True) == _ACCESS_TEXT
        assert _scalar_sql_type(False) == _ACCESS_TEXT

    def test_str_maps_to_memo(self):
        """str（含长文本）→ MEMO（TEXT 上限 255 不可靠）。"""
        from app.services.export_service import (
            _ACCESS_MEMO, _scalar_sql_type,
        )
        assert _scalar_sql_type("abc") == _ACCESS_MEMO


class TestJet4Template:
    """Jet4 模板定位与校验分支。"""

    def test_template_path_all_missing_returns_none(self):
        """所有候选模板均不存在 → None（走 ADOX 回退）。"""
        import os
        from app.services import export_service
        with patch.object(os.path, "isfile", return_value=False):
            assert export_service._jet4_template_path() is None

    def test_is_jet4_mdb_oserror_returns_false(self):
        """文件读取抛 OSError → False。"""
        from app.services import export_service
        with patch("builtins.open", side_effect=OSError("perm denied")):
            assert export_service._is_jet4_mdb("/no/such.mdb") is False

    def test_is_jet4_mdb_bad_header_returns_false(self):
        """文件头不符 → False（非 Jet4）。"""
        from app.services import export_service
        with patch("builtins.open",
                   __enter__=lambda s: s, __exit__=lambda *a: None) as mo:
            fh = mo.return_value.__enter__.return_value
            fh.read.return_value = b"\x00" * 64
            assert export_service._is_jet4_mdb("/x.mdb") is False

    @pytest.mark.skipif(sys.platform != "win32", reason="ADOX/win32com 仅 Windows")
    def test_create_empty_mdb_adox_fallback(self, tmp_path):
        """模板不可用 → ADOX(win32com) 建库回退链。"""
        from app.services import export_service
        p = str(tmp_path / "out.mdb")
        fake_cat = MagicMock()
        with patch.object(export_service, "_jet4_template_path",
                          return_value=None), \
             patch("win32com.client.Dispatch",
                   return_value=fake_cat) as dispatch:
            export_service._create_empty_mdb(p)
        dispatch.assert_called_once_with("ADOX.Catalog")
        fake_cat.Create.assert_called_once()
        # 断言 Create 调用含 Provider/ACE 连接串
        assert "ACE.OLEDB.12.0" in fake_cat.Create.call_args[0][0]

    @pytest.mark.skipif(sys.platform != "win32", reason="ADOX/win32com 仅 Windows")
    def test_create_empty_mdb_import_error_raises(self, tmp_path):
        """win32com.client 不可导入 → RuntimeError 说明需 pywin32。"""
        from app.services import export_service
        p = str(tmp_path / "out.mdb")

        real_import = __import__
        def fake_import(name, *a, **kw):
            if name == "win32com.client" or name.startswith("win32com"):
                raise ImportError("No module named 'win32com.client'")
            return real_import(name, *a, **kw)

        with patch.object(export_service, "_jet4_template_path",
                          return_value=None), \
             patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(RuntimeError, match="pywin32"):
                export_service._create_empty_mdb(p)

    def test_create_empty_mdb_template_copy(self, tmp_path):
        """模板是样例夹具 → 复制后调用 _drop_all_user_tables 清表。"""
        import os
        from app.services import export_service
        p = str(tmp_path / "out.mdb")
        fake_copy = MagicMock(side_effect=lambda src, dst: None)
        with patch.object(export_service, "_jet4_template_path",
                          return_value="/pre/sample_migration.mdb"), \
             patch.object(export_service, "_is_jet4_mdb",
                          return_value=True), \
             patch.object(export_service, "_drop_all_user_tables") as drop, \
             patch.object(os.path, "isfile", return_value=True), \
             patch("shutil.copy", fake_copy) as cp:
            export_service._create_empty_mdb(p)
        cp.assert_called_once()
        drop.assert_called_once_with(p)


class TestDropAllUserTables:
    """_drop_all_user_tables：pyodbc 逐表容错 DROP。"""

    def test_connect_and_drop(self):
        """正常路径：连接 → 逐表 DROP（未存在表异常被吞）。"""
        import pyodbc
        from app.services import export_service
        fake_conn = MagicMock()
        fake_cur = fake_conn.cursor.return_value
        # 部分表已不存在：前两个 DROP 抛错，后面的正常执行
        fake_cur.execute.side_effect = [
            Exception("table not exist"),  # 第 1 张表吞掉
            Exception("table not exist"),  # 第 2 张表吞掉
            None,  # 第 3 张表成功
        ]
        with patch.object(pyodbc, "connect", return_value=fake_conn):
            export_service._drop_all_user_tables("/tmp/x.mdb")
        # 表列表长度次 DROP（17 张）
        assert fake_cur.execute.call_count >= 17
        fake_conn.close.assert_called_once()


class TestShapeFallback:
    """export_mdb / export_csv 的 shapely shape() 异常兜底。"""

    def test_export_mdb_shape_exception_wkt_none(self, monkeypatch):
        """export_mdb：shape(g) 抛异常 → wkt_txt=None（仍能导出 JSON 几何列）。"""
        import json as _json
        import pyodbc
        from app.services.export_service import export_mdb
        from app.services import export_service as _es

        monkeypatch.setattr(_es, "is_windows", lambda: True)
        fc = _fc([_feat("Point", [1, 2], layer="A", name="x")])
        fake_conn = MagicMock()

        def _fake_create(path):
            # 真实落盘：后续 open(mdb_path,"rb") 需要文件存在
            with open(path, "wb") as f:
                f.write(b"DUMMY-MDB")

        with patch("shapely.geometry.shape",
                   side_effect=ValueError("invalid geometry")) as shp_mock, \
             patch("app.services.export_service._create_empty_mdb",
                   side_effect=_fake_create), \
             patch.object(pyodbc, "connect", return_value=fake_conn):
            result = export_mdb(_json.dumps(fc).encode(), "t")
        shp_mock.assert_called_once()
        assert isinstance(result, bytes)
        assert result == b"DUMMY-MDB"

    def test_export_csv_shape_exception_json_fallback(self):
        """export_csv：shape(g) 抛异常 → 几何 JSON 兜底写 WKT 列。"""
        import json as _json
        from app.services.export_service import export_csv
        fc = _fc([_feat("Point", [1, 2], layer="A", name="x")])
        with patch("shapely.geometry.shape",
                   side_effect=ValueError("bad geom")):
            out = export_csv(_json.dumps(fc).encode(), "t")
        assert out.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
        text = out.decode("utf-8-sig")
        assert "geometry" in text
        # 兜底：JSON 串被写入 geometry 列（WKT 失败时）
        assert '"type"' in text


class TestExportGpkgMixed:
    """export_gpkg 混合几何 zip 分支（wkt 传递）。"""

    def test_mixed_geometry_with_wkt(self):
        """混合几何类型 → 每类型独立 .gpkg 并带自定义 CRS 打包为 zip。"""
        from app.services.export_service import export_gpkg
        fc = _fc([
            _feat("Point", [1, 2], layer="A"),
            _feat("LineString", [[0, 0], [1, 1]], layer="A"),
        ])
        out = export_gpkg(__import__("json").dumps(fc).encode(), _CUSTOM_WKT, "t")
        # 混合几何 → zip 容器
        zf = zipfile.ZipFile(io.BytesIO(out))
        names = zf.namelist()
        assert any("Point.gpkg" in n for n in names)
        assert any("LineString.gpkg" in n for n in names)


class TestExportMdbMergeType:
    """export_mdb 跨行混型 → _merge_access_type 列类型升格（L653）。"""

    def test_same_column_mixed_numeric_upgrade(self, monkeypatch):
        """同一属性首行 int、次行 float → 该列升 DOUBLE，不中断导出。"""
        import json as _json
        import pyodbc
        from app.services.export_service import export_mdb
        from app.services import export_service as _es

        monkeypatch.setattr(_es, "is_windows", lambda: True)
        fc = _fc([
            _feat("Point", [1, 2], layer="A", area=100),
            _feat("Point", [3, 4], layer="A", area=100.5),  # int→float 跨行
        ])
        fake_conn = MagicMock()
        fake_cur = fake_conn.cursor.return_value

        def _fake_create(path):
            with open(path, "wb") as f:
                f.write(b"DUMMY-MDB")

        with patch("app.services.export_service._create_empty_mdb",
                   side_effect=_fake_create), \
             patch.object(pyodbc, "connect", return_value=fake_conn):
            result = export_mdb(_json.dumps(fc).encode(), "t")
        assert isinstance(result, bytes)
        # 两次 INSERT（同表两要素）→ 第二次 area 列已升 DOUBLE 再写入
        inserts = [c[0][0] for c in fake_cur.execute.call_args_list if "INSERT" in c[0][0]]
        assert len(inserts) == 2
        # area 列类型在 CREATE 后已是 DOUBLE（合并升格），INSERT 成功
        creates = [c[0][0] for c in fake_cur.execute.call_args_list if "CREATE TABLE" in c[0][0]]
        assert len(creates) == 1
        assert "DOUBLE" in creates[0]