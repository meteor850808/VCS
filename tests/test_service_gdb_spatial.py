# -*- coding: utf-8 -*-
"""P5l: gdb_processor.py + spatial_loader.py 服务级纯逻辑全分支。

动机（对齐 BASELINE §17 / ADR-0001 P5 序列）：
P5k 把 data.py 里的 GDB/SHP/MDB 加载链路压到 98%，但全程用 monkeypatch
钉死的三个边界服务本体仍是安全网真空：gdb_processor 27%、spatial_loader 33%。
本轮按 P5j 体术（纯服务直调 + 边界 monkeypatch）补齐，全部冷启动无 DB 依赖。

覆盖面：
- gdb_processor: gdb_read_lock（进程级互斥锁惰性创建）；prepare_gdb
  （空文件集/无 .gdb 目录/外部 tmp_dir 不清理/happy 多 layer/层元数据失败跳过/
  schema 异常降级/无 layer 自动清理/外层异常兜底）;read_layer_features
  （流式 yield / 打开失败向上传播）。
- spatial_loader: _force_2d Z 降维；geojson_to_wkt_status
  （缺 type/自相交修复/修复失败/MultiPatch/PolyhedralSurface/TIN 降维/异常）;
  extract_features_from_file（扩展名嗅探/GeoJSON/OBJ/DWG 降级/DXF CAD 管道/Fiona
  通用/异常）;_promote_cad_layers；_cad_lines_to_polygons（闭合环 Polygon/无效环
  修复/MultiLineString 拆面+线/退化环保留/异常保留）;_is_closed_ring；
  extract_features_from_bytes（临时文件往返/清理）；_extract_from_geojson
  （损坏/FeatureCollection/Feature/其他/无几何）；_extract_from_obj 成功/失败；
  _extract_with_fiona 成功/异常；_guess_format 各扩展名 + 未知。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import gdb_processor as gdb_mod
from app.services import spatial_loader as sl


# ══════════════════════════════════════════════════════════════════════════════
# gdb_processor: gdb_read_lock / prepare_gdb / read_layer_features
# ══════════════════════════════════════════════════════════════════════════════

class _Src:
    """fake fiona 数据源：crs + schema。"""

    def __init__(self, crs=None, schema=None):
        self.crs = crs
        self.schema = schema

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeFiona:
    """最小 fake fiona 模块：listlayers + open 可编程。"""

    def __init__(self, layers=(), srcs=None, open_error=None, list_error=None):
        self._layers = list(layers)
        self._srcs = srcs or {}
        self._open_error = open_error
        self._list_error = list_error

    def listlayers(self, path):
        if self._list_error:
            raise self._list_error
        return self._layers

    def open(self, path, layer=None):
        if self._open_error:
            raise self._open_error
        return self._srcs[layer]


class _Feat:
    def __init__(self, geom, props):
        self.geometry = geom
        self.properties = props

    def get(self, key, default=None):
        if key == "geometry":
            return self.geometry
        if key == "properties":
            return self.properties
        return default


class _FeatSrc:
    def __init__(self, feats):
        self._feats = feats

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._feats)


def _ef(name, content=b"x"):
    return SimpleNamespace(original_name=name, content=content)


class TestGdbReadLock:

    def test_same_path_same_lock(self):
        lock1 = gdb_mod.gdb_read_lock("/tmp/a.gdb")
        lock2 = gdb_mod.gdb_read_lock("/tmp/a.gdb")
        assert lock1 is lock2

    def test_diff_path_diff_lock(self):
        lock1 = gdb_mod.gdb_read_lock("/tmp/a.gdb")
        lock2 = gdb_mod.gdb_read_lock("/tmp/b.gdb")
        assert lock1 is not lock2
        assert isinstance(lock1, type(lock2))


class TestPrepareGdb:

    def test_no_files_empty(self):
        info = gdb_mod.prepare_gdb([], "x.gdb")
        assert info.gdb_path == "" and info.gdb_basename == "" and info.tmp_dir == ""

    def test_no_gdb_dir_auto_cleanup(self, tmp_path, monkeypatch):
        """写文件后找不到 .gdb 目录 → 告警 + 自动创建目录被清理。"""
        monkeypatch.setattr(gdb_mod, "fiona", _FakeFiona())
        info = gdb_mod.prepare_gdb([_ef("a.txt", b"1")], "x/a.txt")
        assert info.gdb_path == ""
        # 自动创建的临时目录已被清理（避免残留）
        assert info.tmp_dir == ""

    def test_external_tmp_dir_not_cleaned(self, tmp_path, monkeypatch):
        """调用方传入 tmp_dir（own_tmp=False）→ 无 .gdb 时目录保留。"""
        ext = tmp_path / "ext"
        ext.mkdir()
        monkeypatch.setattr(gdb_mod, "fiona", _FakeFiona())
        info = gdb_mod.prepare_gdb([_ef("a.txt", b"1")], "x/a.txt", tmp_dir=str(ext))
        assert info.gdb_path == ""
        assert ext.exists()  # 外部目录不代清理

    def test_happy_two_layers(self, tmp_path, monkeypatch):
        """fake fiona 2 层：gdb_path 解析 + crs_wkt + 3D 归一化。"""
        gdb_dir = tmp_path / "city.gdb"
        gdb_dir.mkdir()
        (gdb_dir / "a.gdbtable").write_bytes(b"g")
        fake = _FakeFiona(
            layers=["roads", "parcels"],
            srcs={
                "roads": _Src(crs=SimpleNamespace(to_wkt=lambda: "EPSG:4326 WKT"),
                              schema={"geometry": "3D MultiPolygon"}),
                "parcels": _Src(crs=None, schema={"geometry": "Point"}),
            },
        )
        monkeypatch.setattr(gdb_mod, "fiona", fake)
        info = gdb_mod.prepare_gdb(
            [_ef("city.gdb/a.gdbtable")], "dir/city.gdb")
        assert Path(info.gdb_path).name == "city.gdb"  # 自动 tmp 目录内重建
        assert Path(info.gdb_path).is_dir()
        assert info.gdb_basename == "city"
        assert [l.name for l in info.layers] == ["roads", "parcels"]
        assert info.layers[0].crs_wkt == "EPSG:4326 WKT"
        # 3D MultiPolygon → 归一化降为 Polygon（format_detection 归一语义）
        assert info.layers[0].geometry_type == "Polygon"
        assert info.layers[1].geometry_type == "Point"

    def test_layer_open_failure_skipped(self, tmp_path, monkeypatch):
        """某 layer fiona.open 抛错 → 跳过并继续下一层。"""
        gdb_dir = tmp_path / "city.gdb"
        gdb_dir.mkdir()
        fake = _FakeFiona(layers=["bad", "good"],
                          srcs={"good": _Src(crs=None, schema={"geometry": "Point"})},
                          open_error=None)
        # bad 层 open 抛错：伪造 open 按 layer 分派
        real_open = fake.open

        def _open(path, layer=None):
            if layer == "bad":
                raise RuntimeError("layer broken")
            return real_open(path, layer=layer)

        fake.open = _open
        monkeypatch.setattr(gdb_mod, "fiona", fake)
        info = gdb_mod.prepare_gdb([_ef("city.gdb/a.gdbtable")], "x.gdb")
        assert [l.name for l in info.layers] == ["good"]

    def test_schema_exception_degrades(self, tmp_path, monkeypatch):
        """schema.get 抛错被内层 try 吞 → 仍 append（geometry_type=None）。"""
        gdb_dir = tmp_path / "city.gdb"
        gdb_dir.mkdir()
        src = _Src(crs=None, schema=None)

        def _get(junk):
            raise RuntimeError("schema boom")

        src.schema = SimpleNamespace(get=_get)
        fake = _FakeFiona(layers=["l1"], srcs={"l1": src})
        monkeypatch.setattr(gdb_mod, "fiona", fake)
        info = gdb_mod.prepare_gdb([_ef("city.gdb/a.gdbtable")], "x.gdb")
        assert len(info.layers) == 1
        assert info.layers[0].geometry_type is None

    def test_no_layers_cleanup(self, tmp_path, monkeypatch):
        """listlayers 为空 → layers 空 → 自动清理 + tmp_dir 置空。"""
        monkeypatch.setattr(gdb_mod, "fiona", _FakeFiona(layers=[]))
        info = gdb_mod.prepare_gdb([_ef("city.gdb/a.gdbtable")], "x.gdb")
        assert info.layers == []
        assert info.tmp_dir == ""

    def test_outer_exception_cleanup(self, tmp_path, monkeypatch):
        """写入阶段抛错 → 外层 except 兜底返回空对象。"""
        monkeypatch.setattr(gdb_mod, "fiona", _FakeFiona())

        def _broken_makedirs(path, exist_ok=False):
            raise OSError("no space")

        monkeypatch.setattr(gdb_mod.os, "makedirs", _broken_makedirs)
        info = gdb_mod.prepare_gdb([_ef("city.gdb/a.gdbtable")], "x.gdb")
        assert info.gdb_path == "" and info.tmp_dir == ""

    def test_cleanup_rmtree_raise_swallowed(self, tmp_path, monkeypatch):
        """_cleanup 内 rmtree 抛错被吞（108-109），进程不崩。"""
        monkeypatch.setattr(gdb_mod, "fiona", _FakeFiona())

        def _broken_rmtree(path, ignore_errors=False):
            raise OSError("locked")

        monkeypatch.setattr(gdb_mod.shutil, "rmtree", _broken_rmtree)
        info = gdb_mod.prepare_gdb([_ef("a.txt", b"1")], "x/a.txt")
        assert info.gdb_path == ""


class TestReadLayerFeatures:

    def test_streams_only_geometries(self, monkeypatch):
        """两要素（1 有几何 1 无）→ 仅 yield 有几何者 + feature_index/layer_name。"""
        feats = [_Feat({"type": "Point", "coordinates": [1, 2]}, {"n": "a"}),
                 _Feat(None, {"n": "b"})]
        fake = _FakeFiona(layers=["roads"], srcs={"roads": _FeatSrc(feats)})
        monkeypatch.setattr(gdb_mod, "fiona", fake)
        out = list(gdb_mod.read_layer_features("/tmp/city.gdb", "roads"))
        assert len(out) == 1
        assert out[0]["geometry"]["type"] == "Point"
        assert out[0]["properties"] == {"n": "a"}
        assert out[0]["feature_index"] == 0
        assert out[0]["layer_name"] == "roads"

    def test_open_failure_propagates(self, monkeypatch):
        """fiona.open 抛错 → 迭代向外传播（不吞）。"""
        fake = _FakeFiona(open_error=RuntimeError("permission denied"))
        monkeypatch.setattr(gdb_mod, "fiona", fake)
        with pytest.raises(RuntimeError, match="permission denied"):
            list(gdb_mod.read_layer_features("/tmp/city.gdb", "roads"))


# ══════════════════════════════════════════════════════════════════════════════
# spatial_loader: 几何转换 / 要素提取管道
# ══════════════════════════════════════════════════════════════════════════════

class TestGeojsonToWkt:

    def test_wrapper_returns_str(self):
        out = sl.geojson_to_wkt({"type": "Point", "coordinates": [1.0, 2.0]})
        assert isinstance(out, str) and out.startswith("POINT")

    def test_point_happy(self):
        wkt_str, ok = sl.geojson_to_wkt_status(
            {"type": "Point", "coordinates": [121.47, 31.23]})
        assert ok is True
        assert wkt_str.startswith("POINT (") and str(31.23) in wkt_str

    def test_z_dropped(self):
        wkt_str, ok = sl.geojson_to_wkt_status(
            {"type": "Point", "coordinates": [1.0, 2.0, 99.0]})
        assert ok is True
        assert " 99" not in wkt_str  # Z 已剥

    def test_no_type_empty(self):
        assert sl.geojson_to_wkt_status({}) == ("GEOMETRYCOLLECTION EMPTY", False)

    def test_self_intersecting_repaired(self):
        # 领结自相交多边形 → make_valid → 修复后有效
        coords = [[(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)]]
        wkt_str, ok = sl.geojson_to_wkt_status(
            {"type": "Polygon", "coordinates": coords})
        assert ok is True
        assert wkt_str.startswith(("POLYGON", "MULTIPOLYGON"))

    def test_unrepairable_empty(self):
        # make_valid 返回空/无效 → 64 行 else → GEOMETRYCOLLECTION EMPTY
        with patch("app.services.spatial_loader.make_valid", return_value=None):
            wkt_str, ok = sl.geojson_to_wkt_status(
                {"type": "Polygon",
                 "coordinates": [[(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)]]})
        assert ok is False
        assert wkt_str == "GEOMETRYCOLLECTION EMPTY"

    def test_multipatch_downgraded(self):
        wkt_str, ok = sl.geojson_to_wkt_status(
            {"type": "MultiPatch",
             "coordinates": [[[(0, 0), (1, 0), (1, 1), (0, 0)], [(3, 3), (4, 3), (4, 4), (3, 3)]]]})
        assert ok is True
        assert wkt_str.startswith("MULTIPOLYGON")

    def test_tin_downgraded(self):
        tri = [(0, 0), (2, 0), (1, 2), (0, 0)]  # 平铺环点列
        wkt_str, ok = sl.geojson_to_wkt_status({"type": "TIN", "coordinates": [tri]})
        assert ok is True
        assert wkt_str.startswith("MULTIPOLYGON")

    def test_garbage_coords_false(self):
        # shape() 对无坐标/错类型 → 异常 → False
        wkt_str, ok = sl.geojson_to_wkt_status(
            {"type": "Point", "coordinates": "not-coords"})
        assert ok is False


class TestForce2d:

    def test_force_2d_no_z(self):
        from shapely.geometry import Point
        assert sl._force_2d(Point(1, 2)).has_z is False

    def test_force_2d_drops_z(self):
        from shapely.geometry import Point
        p = sl._force_2d(Point(1, 2, 3))
        assert p.has_z is False
        assert p.coords[0] == (1.0, 2.0)


class TestGuessFormat:

    @pytest.mark.parametrize("name,expected", [
        ("a.geojson", "GeoJSON"), ("a.json", "GeoJSON"), ("a.shp", "ESRI Shapefile"),
        ("a.gpkg", "GPKG"), ("a.gml", "GML"), ("a.kml", "KML"), ("a.dxf", "DXF"),
        ("a.dgn", "DGN"), ("a.dwg", "DWG"), ("a.tab", "MapInfo File"),
        ("a.csv", "CSV"), ("a.obj", "OBJ"), ("a.xyz", None),
    ])
    def test_extensions(self, name, expected):
        assert sl._guess_format(name) == expected


class TestExtractFromGeoJson:

    def test_broken_json_empty(self, tmp_path, caplog):
        p = tmp_path / "bad.geojson"
        p.write_bytes(b"\xff\xfe not-json")
        out = sl._extract_from_geojson(str(p))
        assert out == []

    def test_feature_collection(self, tmp_path):
        p = tmp_path / "fc.geojson"
        p.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
                 "properties": {"a": 1}},
                {"type": "Feature", "geometry": None, "properties": {}},
            ],
        }), encoding="utf-8")
        out = sl._extract_from_geojson(str(p))
        assert len(out) == 1  # 无几何的 Feature 被跳过
        assert out[0]["feature_index"] == 0
        assert out[0]["layer_name"] == "fc"

    def test_single_feature(self, tmp_path):
        p = tmp_path / "one.geojson"
        p.write_text(json.dumps({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3, 4]},
            "properties": {},
        }), encoding="utf-8")
        out = sl._extract_from_geojson(str(p))
        assert len(out) == 1

    def test_unknown_type_empty(self, tmp_path):
        p = tmp_path / "x.geojson"
        p.write_text(json.dumps({"type": "GeometryCollection", "geometries": []}),
                     encoding="utf-8")
        assert sl._extract_from_geojson(str(p)) == []


class TestExtractFromBytes:

    def test_roundtrip_and_temp_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "_extract_with_fiona", lambda *a, **k: [{"p": 1}])
        monkeypatch.setattr(sl, "_guess_format", lambda p: "GPKG")
        out = sl.extract_features_from_bytes(b"fake-bytes", "x.gpkg")
        assert out == [{"p": 1}]

    def test_unlink_failure_swallowed(self, monkeypatch):
        """finally 中临时文件删除失败被吞（216-217）。"""
        monkeypatch.setattr(sl, "_guess_format", lambda p: "GeoJSON")
        monkeypatch.setattr(sl, "_extract_from_geojson", lambda p: [{"ok": 1}])
        with patch("os.unlink", side_effect=OSError("busy")):
            out = sl.extract_features_from_bytes(b"{}", "x.json")
        assert out == [{"ok": 1}]

    def test_geojson_bytes_real(self):
        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {}},
        ]}
        out = sl.extract_features_from_bytes(json.dumps(data).encode(), "pts.geojson")
        assert len(out) == 1
        # 临时文件后缀 .geojson → layer_name 取临时文件 stem（tmp 前缀）
        assert out[0]["layer_name"].startswith("tm")


class TestExtractFromObj:

    def test_happy(self, tmp_path):
        fake_parser = SimpleNamespace(
            parse_obj_file=lambda p: {"objects": 1},
            obj_to_geojson_features=lambda parsed: [
                {"geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}},
            ],
        )
        with patch("app.services.obj_parser.parse_obj_file", fake_parser.parse_obj_file), \
             patch("app.services.obj_parser.obj_to_geojson_features",
                   fake_parser.obj_to_geojson_features):
            out = sl._extract_from_obj(str(tmp_path / "m.obj"))
        assert len(out) == 1
        assert out[0]["feature_index"] == 0
        assert out[0]["layer_name"] == "m"

    def test_failure_empty(self, tmp_path):
        with patch("app.services.obj_parser.parse_obj_file",
                   side_effect=RuntimeError("obj broken")), \
             patch("app.services.obj_parser.obj_to_geojson_features",
                   side_effect=RuntimeError("obj broken")):
            assert sl._extract_from_obj(str(tmp_path / "m.obj")) == []


class TestExtractFromDwg:

    def test_converter_none_empty(self):
        with patch("app.services.dwg_converter.dwg_to_dxf", return_value=None):
            assert sl._extract_from_dwg("x.dwg") == []

    def test_converter_full_pipeline(self, tmp_path):
        # dwg_to_dxf 返回临时 dxf 路径 → fiona 解析 → CAD 提升 + 面还原 → cleanup
        dxf_path = str(tmp_path / "out.dxf")
        with patch("app.services.dwg_converter.dwg_to_dxf", return_value=dxf_path), \
             patch("app.services.spatial_loader._extract_with_fiona",
                   return_value=[
                       {"geometry": {"type": "LineString",
                                     "coordinates": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]},
                        "properties": {"Layer": "地块线"}, "feature_index": 0},
                   ]), \
             patch("app.services.dwg_converter.cleanup_converted_dxf") as cleanup:
            out = sl._extract_from_dwg("x.dwg")
        assert cleanup.called
        assert len(out) == 1
        feat = out[0]
        assert feat["geometry"]["type"] == "Polygon"  # 闭合线 → 面
        assert feat["layer_name"] == "地块线"


class TestFionaGeneric:

    def test_multi_layer(self, monkeypatch):
        class _FSrc:
            def __init__(self, feats):
                self._feats = feats

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(self._feats)

        feats = [_Feat({"type": "Point", "coordinates": [1, 2]}, {"n": "a"})]
        fake = _FakeFiona(layers=["L1"], srcs={"L1": _FSrc(feats)})
        import fiona as fiona_mod
        monkeypatch.setattr(fiona_mod, "listlayers", fake.listlayers)
        monkeypatch.setattr(fiona_mod, "open", fake.open)
        out = sl._extract_with_fiona("/tmp/x.shp", "ESRI Shapefile")
        assert len(out) == 1
        assert out[0]["layer_name"] == "L1"
        assert out[0]["feature_index"] == 0
        assert out[0]["properties"] == {"n": "a"}

    def test_fiona_error_empty(self, monkeypatch):
        def _raise(*a, **k):
            raise RuntimeError("driver broken")
        import fiona as fiona_mod
        monkeypatch.setattr(fiona_mod, "listlayers", _raise)
        monkeypatch.setattr(fiona_mod, "open", _raise)
        assert sl._extract_with_fiona("/tmp/x.shp") == []


class TestExtractFromFile:

    def test_guess_format_branch(self, tmp_path):
        p = tmp_path / "pts.geojson"
        p.write_text(json.dumps({"type": "Feature", "geometry": None, "properties": {}}),
                     encoding="utf-8")
        assert sl.extract_features_from_file(str(p)) == []

    def test_geojson_branch(self, tmp_path):
        p = tmp_path / "pts.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {}}]}), encoding="utf-8")
        out = sl.extract_features_from_file(str(p))
        assert len(out) == 1

    def test_obj_branch(self):
        with patch("app.services.spatial_loader._guess_format", return_value="OBJ"), \
             patch("app.services.obj_parser.parse_obj_file", lambda p: {}), \
             patch("app.services.obj_parser.obj_to_geojson_features",
                   lambda parsed: [{"geometry": {"type": "Point", "coordinates": [1, 1]}}]):
            out = sl.extract_features_from_file("m.obj")
        assert len(out) == 1 and out[0]["feature_index"] == 0

    def test_dwg_branch_none(self):
        with patch("app.services.spatial_loader._guess_format", return_value="DWG"), \
             patch("app.services.dwg_converter.dwg_to_dxf", return_value=None):
            assert sl.extract_features_from_file("m.dwg") == []

    def test_fiona_branch(self, monkeypatch):
        monkeypatch.setattr(sl, "_guess_format", lambda p: "ESRI Shapefile")
        monkeypatch.setattr(sl, "_extract_with_fiona", lambda p, f: [{"g": 1}])
        assert sl.extract_features_from_file("m.shp") == [{"g": 1}]

    def test_dxf_pipeline(self, monkeypatch):
        monkeypatch.setattr(sl, "_guess_format", lambda p: "DXF")
        monkeypatch.setattr(sl, "_extract_with_fiona",
                            lambda p, f: [
                                {"geometry": {"type": "LineString",
                                              "coordinates": [[0, 0], [2, 0], [2, 2], [0, 0]]},
                                 "properties": {"Layer": "红线"}, "feature_index": 0},
                            ])
        out = sl.extract_features_from_file("m.dxf")
        assert len(out) == 1
        assert out[0]["geometry"]["type"] == "Polygon"
        assert out[0]["layer_name"] == "红线"


class TestCadConversion:

    def test_promote_cad_layers(self):
        feats = [
            {"properties": {"Layer": "A"}},
            {"properties": {"layer": "B"}},
            {"properties": {"Layer": "  "}},
            {"properties": {}},
        ]
        out = sl._promote_cad_layers(feats)
        assert out[0]["layer_name"] == "A"
        assert out[1]["layer_name"] == "B"
        assert "layer_name" not in out[2]
        assert "layer_name" not in out[3]

    def test_closed_line_to_polygon(self):
        feats = [{
            "geometry": {"type": "LineString",
                         "coordinates": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert out[0]["geometry"]["type"] == "Polygon"
        assert out[0]["feature_index"] == 0

    def test_open_line_kept(self):
        feats = [{
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [2, 0], [2, 2]]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert out[0]["geometry"]["type"] == "LineString"

    def test_self_intersecting_closed_repaired(self):
        feats = [{
            "geometry": {"type": "LineString",
                         "coordinates": [[0, 0], [1, 1], [2, 0], [1, -1], [0, 0]]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert out[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_multiline_mixed(self):
        feats = [{
            "geometry": {"type": "MultiLineString", "coordinates": [
                [[0, 0], [2, 0], [2, 2], [0, 0]],          # 闭合环
                [[5, 5], [6, 5]],                          # 开放线
                [[1, 1], [1, 1], [1, 1]],                  # 退化环（面积 0 → 丢弃）
            ]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        types = [f["geometry"]["type"] for f in out]
        assert "Polygon" in types
        assert "MultiLineString" in types  # opens 走 MultiLineString 输出

    def test_degenerate_zero_area_ring_dropped(self):
        """面积=0 退化环 → 181 continue；无产出时保底保留原要素（197）。"""
        feats = [{
            "geometry": {"type": "MultiLineString", "coordinates": [
                [[1, 1], [1, 1], [1, 1], [1, 1]],
            ]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert len(out) == 1
        assert out[0]["geometry"]["type"] == "MultiLineString"  # 未转换原样保留

    def test_degenerate_ring_kept_as_line(self):
        feats = [{
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 0], [0, 0], [0, 0]]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert out[0]["geometry"]["type"] == "LineString"

    def test_bad_geometry_kept(self):
        feats = [{"geometry": {"type": "Nope", "coordinates": "x"}, "properties": {}, "feature_index": 0}]
        out = sl._cad_lines_to_polygons(feats)
        assert len(out) == 1
        assert out[0]["geometry"]["type"] == "Nope"

    def test_invalid_ring_repaired_and_multipoly_expand(self, monkeypatch):
        """哨兵驱动：area>0 但 is_valid=False 的环 → make_valid 修复；
        MultiPolygon 修复结果 → extend 展开多部件（169-172 / 181-189）。"""
        class _BadPoly:
            def __init__(self, coords):
                self.area = 3.0
                self.is_valid = False
                self.is_empty = False
                self.geom_type = "Polygon"

        class _ReusePoly:
            def __init__(self, coords):
                self.area = 3.0
                self.is_valid = True
                self.is_empty = False
                self.geom_type = "Polygon"

        class _Repaired:
            def __init__(self, p):
                self.geom_type = "MultiPolygon"
                self.is_empty = False
                self.geoms = [_ReusePoly(None), _ReusePoly(None)]

        monkeypatch.setattr(sl, "Polygon", _BadPoly)          # 所有环都是 invalid
        monkeypatch.setattr(sl, "make_valid", _Repaired)      # 修复成 MultiPolygon(2 部件)
        monkeypatch.setattr(sl, "MultiPolygon", lambda parts: {"mparts": len(parts)})
        monkeypatch.setattr(sl, "mapping", lambda g: g)

        # LineString 分支：make_valid → MultiPolygon → mapping 后推入
        out1 = sl._cad_lines_to_polygons([{
            "geometry": {"type": "LineString",
                         "coordinates": [[0, 0], [1, 0], [1, 1], [0, 0]]},
            "properties": {}, "feature_index": 0,
        }])
        assert out1[0]["geometry"] is not None

        # MultiLineString 分支：环 expand 成 2 部件 + 开放线保留
        out2 = sl._cad_lines_to_polygons([{
            "geometry": {"type": "MultiLineString", "coordinates": [
                [[0, 0], [2, 0], [2, 2], [0, 0]],
                [[5, 5], [6, 5]],
            ]},
            "properties": {}, "feature_index": 0,
        }])
        types = [f["geometry"].get("type") for f in out2]
        assert "MultiLineString" in types  # opens 保留输出
        assert any(f["geometry"] == {"mparts": 2} for f in out2)  # 环 expand 成 2 部件

    def test_invalid_ring_repaired_single_polygon(self, monkeypatch):
        """make_valid 返回单个 Polygon → 181-182 直接 append（不 expand）。"""
        class _BadPoly:
            def __init__(self, coords):
                self.area = 3.0
                self.is_valid = False
                self.is_empty = False
                self.geom_type = "Polygon"

        class _FixedPoly:
            def __init__(self, p):
                self.geom_type = "Polygon"
                self.is_empty = False
                self.is_valid = True

        monkeypatch.setattr(sl, "Polygon", _BadPoly)
        monkeypatch.setattr(sl, "make_valid", _FixedPoly)
        # MultiLineString 分支：make_valid 单 Polygon 修复 → 181-182 append
        out = sl._cad_lines_to_polygons([{
            "geometry": {"type": "MultiLineString", "coordinates": [
                [[0, 0], [2, 0], [2, 2], [0, 0]],
            ]},
            "properties": {}, "feature_index": 0,
        }])
        assert len(out) == 1
        assert "geometry" in out[0]

    def test_malformed_ring_coords_kept(self):
        """闭合环坐标损坏 → Polygon 构造抛错 → except 保留原要素（195-196）。"""
        feats = [{
            "geometry": {"type": "LineString",
                         "coordinates": [[0, 0], [1, "x"], [2, 0], [0, 0]]},
            "properties": {}, "feature_index": 0,
        }]
        out = sl._cad_lines_to_polygons(feats)
        assert len(out) == 1
        assert out[0]["geometry"]["type"] == "LineString"
        assert out[0]["feature_index"] == 0


class TestIsClosedRing:

    def test_closed_true(self):
        assert sl._is_closed_ring([[0, 0], [1, 0], [1, 1], [0, 0]])

    def test_short_false(self):
        assert sl._is_closed_ring([[0, 0], [1, 0]]) is False

    def test_open_false(self):
        assert sl._is_closed_ring([[0, 0], [1, 0], [2, 0]]) is False

    def test_bad_input_false(self):
        assert sl._is_closed_ring(None) is False
        assert sl._is_closed_ring("junk") is False