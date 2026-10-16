# -*- coding: utf-8 -*-
"""导出服务测试：GeoJSON / Shapefile(zip + 自定义 .prj) / GeoPackage。"""
import io
import json
import zipfile
import sqlite3

import pytest

from app.services.export_service import (
    export_geojson,
    export_gpkg,
    export_shapefile_zip,
)

# 一个 shanghaicity(自定义 CRS 918984) → 目标的多边形 FeatureCollection
_CUSTOM_WKT = (
    'PROJCS["ShanghaiCity",GEOGCS["GCS_Beijing_1954",DATUM["D_Beijing_1954",'
    'SPHEROID["Krassowsky_1940",6378245,298.3]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",121.275],'
    'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",-3457147.81],UNIT["metre",1]]'
)

# 标准 EPSG:4490 (CGCS2000 地理坐标) 的 srtext——历史 bug：标准 SRID 目标漏查 WKT，
# 导致 SHP zip 内缺 .prj
_EPSG4490_WKT = (
    'GEOGCS["China Geodetic Coordinate System 2000",'
    'DATUM["China_2000",SPHEROID["CGCS2000",6378137,298.257222101,AUTHORITY["EPSG","1024"]],'
    'AUTHORITY["EPSG","1043"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4490"]]'
)

_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"fid": 0, "_feature_table": "polygon_features_p_x", "name": "甲"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[(121.28, 30.82), (121.29, 30.82), (121.29, 30.83), (121.28, 30.83), (121.28, 30.82)]],
            },
        },
        {
            "type": "Feature",
            "properties": {"fid": 1, "_feature_table": "polygon_features_p_x", "name": "乙"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[(121.30, 30.84), (121.31, 30.84), (121.31, 30.85), (121.30, 30.85), (121.30, 30.84)]],
            },
        },
    ],
}


def _fc_bytes():
    return json.dumps(_FC, ensure_ascii=False).encode("utf-8")


def test_shapefile_zip_contains_prj_with_custom_wkt():
    out = export_shapefile_zip(_fc_bytes(), _CUSTOM_WKT, "Export_Output_shanghaicity")
    zf = zipfile.ZipFile(io.BytesIO(out))
    names = zf.namelist()
    # 核心 4 文件 + cpg
    assert any(n.endswith(".shp") for n in names)
    assert any(n.endswith(".shx") for n in names)
    assert any(n.endswith(".dbf") for n in names)
    prj = next(n for n in names if n.endswith(".prj"))
    assert "ShanghaiCity" in zf.read(prj).decode("utf-8", "replace")
    # 清洗基名：完整 base 名在 zip 内
    assert names[0].startswith("Export_Output_shanghaicity")


def test_shapefile_reopenable_with_crs():
    import fiona
    import tempfile, os
    out = export_shapefile_zip(_fc_bytes(), _CUSTOM_WKT, "t")
    zf = zipfile.ZipFile(io.BytesIO(out))
    with tempfile.TemporaryDirectory() as td:
        for n in zf.namelist():
            with open(os.path.join(td, n), "wb") as f:
                f.write(zf.read(n))
        shp = os.path.join(td, "t.shp")
        with fiona.open(shp) as src:
            assert src.crs_wkt is not None
            assert "ShanghaiCity" in (src.crs_wkt or "")
            feats = list(src)
            assert len(feats) == 2
            # 属性保留（含 CJK）
            props = feats[0]["properties"]
            assert props.get("name") in ("甲", "乙")


def test_standard_epsg_wkt_produces_prj():
    """回归：标准 EPSG 目标(如 4490)此前漏查 WKT → zip 缺 .prj，必须修复。"""
    out = export_shapefile_zip(_fc_bytes(), _EPSG4490_WKT, "Export_Output_to_EPSG_4490")
    zf = zipfile.ZipFile(io.BytesIO(out))
    names = zf.namelist()
    assert any(n.endswith(".prj") for n in names), f"缺少 .prj: {names}"
    prj = next(n for n in names if n.endswith(".prj"))
    txt = zf.read(prj).decode("utf-8", "replace")
    assert "CGCS2000" in txt or "China Geodetic Coordinate System 2000" in txt


def test_none_wkt_no_prj_but_valid_shp():
    # wkt=None 时无 .prj 是可接受降级（无 CRS 语义），但 shp/dbf 仍存在
    out = export_shapefile_zip(_fc_bytes(), None, "t")
    names = zipfile.ZipFile(io.BytesIO(out)).namelist()
    assert any(n.endswith(".shp") for n in names)
    assert any(n.endswith(".dbf") for n in names)
    assert not any(n.endswith(".prj") for n in names)


def test_gpkg_valid_with_crs():
    out = export_gpkg(_fc_bytes(), _CUSTOM_WKT, "t")
    # GPKG 本质是 SQLite，魔数 "SQLite format 3"
    assert out.startswith(b"SQLite format 3")
    # 可被 fiona 重读且带 CRS
    import fiona
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.gpkg")
        with open(p, "wb") as f:
            f.write(out)
        with fiona.open(p) as src:
            assert len(list(src)) == 2
            # 自定义 CRS 在 GPKG 中以 custom CRS 形式存在 (srs_id 非 4326)
            crs = src.crs_wkt or ""
            assert "ShanghaiCity" in crs


def test_export_geojson_passthrough():
    raw = _fc_bytes()
    assert export_geojson(raw) == raw


def test_empty_features_raises():
    bad = json.dumps({"type": "FeatureCollection", "features": []}).encode()
    with pytest.raises(ValueError):
        export_shapefile_zip(bad, None, "t")


def test_invalid_output_raises():
    with pytest.raises(ValueError):
        export_shapefile_zip(b"not json", None, "t")


def test_safe_stem_sanitizes():
    from app.services.export_service import _safe_stem
    # 非法字符 `< > : " / \ | ? *` → `_`，去扩展名
    assert _safe_stem("abc*?.shp") == "abc__"
    # 冒号（Custom: 前缀常用）转下划线
    assert _safe_stem("Export_Output_Custom:shanghaicity") == "Export_Output_Custom_shanghaicity"
    # 控制字符清理（替换为下划线）
    assert _safe_stem("a\nb\r.shp") == "a_b_"
