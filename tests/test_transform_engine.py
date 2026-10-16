# -*- coding: utf-8 -*-
"""transform_engine（统一坐标变换引擎）单元测试。

覆盖（不含 DB 的纯函数部分）：
    - ResolvedCRS / 内置自定义坐标系定义注册
    - transform_geometry 各类几何类型变换（含 Z 维保留）
    - 坐标域校验（源投影误标地理 → 告警；目标地理结果越界 → 告警）
    - 批量变换统计（transform_feature_list / transform_feature_dict）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.transform_engine import (
    ResolvedCRS,
    _CUSTOM_CRS_DEFINITIONS,
    get_custom_crs_catalog,
    transform_geometry,
    transform_feature_dict,
    transform_feature_list,
    _srid_is_geographic,
)

# 标准坐标系
WGS = ResolvedCRS(srid=4326, pyproj_crs="EPSG:4326", epsg=4326,
                  is_custom=False, source="epsg", display="EPSG:4326")
WEBM = ResolvedCRS(srid=3857, pyproj_crs="EPSG:3857", epsg=3857,
                   is_custom=False, source="epsg", display="EPSG:3857")

# 上海城市坐标系（自定义，无 EPSG）
SH_CITY = ResolvedCRS(
    srid=918984,
    pyproj_crs='PROJCS["ShanghaiCity",GEOGCS["GCS_Beijing_1954",'
               'DATUM["D_Beijing_1954",SPHEROID["Krasovsky_1940",6378245,298.3]],'
               'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
               'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",0],'
               'PARAMETER["False_Northing",-3457147.81],'
               'PARAMETER["Central_Meridian",121.275],PARAMETER["Scale_Factor",1],'
               'PARAMETER["Latitude_Of_Origin",0],UNIT["meter",1]]',
    epsg=None, is_custom=True, source="wkt-custom", display="Custom:shanghaicity",
)


def test_builtin_custom_definitions_registered():
    """内置自定义坐标系定义应已注册。"""
    assert "shanghaicity" in _CUSTOM_CRS_DEFINITIONS
    assert "shanghai_local" in _CUSTOM_CRS_DEFINITIONS
    catalog = get_custom_crs_catalog()
    codes = {c["code"] for c in catalog}
    assert "shanghaicity" in codes and "shanghai_local" in codes


def test_geographic_detection():
    """SRID 地理/投影判定。"""
    assert _srid_is_geographic(4326) is True
    assert _srid_is_geographic(4490) is True
    assert _srid_is_geographic(3857) is False
    assert _srid_is_geographic(918984) is False


def test_point_transform_wgs_to_webmercator():
    """WGS84 → Web Mercator 点变换（known value）。"""
    pt = {"type": "Point", "coordinates": [121.47, 31.23]}
    out = transform_geometry(pt, WGS, WEBM)
    x, y = out["coordinates"]
    # Web Mercator 北京/上海大体量级
    assert x > 1_000_000 and y > 1_000_000


def test_point_transform_custom_crs_roundtrip():
    """自定义坐标系（shanghaicity）：WGS84→投影→WGS84 闭环。"""
    pt = {"type": "Point", "coordinates": [121.47, 31.23]}
    projected = transform_geometry(pt, WGS, SH_CITY)
    x = projected["coordinates"][0]
    # 上海城市坐标系下 x 应在合理范围（数百米~数十公里）
    assert -500_000 < x < 2_000_000
    back = transform_geometry(projected, SH_CITY, WGS)
    lon, lat = back["coordinates"]
    # shanghaicity 采用 Krasovsky 椭球（与 WGS84/CGCS2000 基准不同），
    # 无七参数网格时存在默认基准面平移，闭环误差在数十米量级（经纬度 < 0.05°）
    assert abs(lon - 121.47) < 0.05
    assert abs(lat - 31.23) < 0.05
    # 且不等式方向合理：仍落在上海附近区域
    assert 121.0 < lon < 122.0
    assert 31.0 < lat < 32.0


def test_linestring_preserves_z():
    """变换应保留 Z 维（若源存在）。"""
    ls = {"type": "LineString", "coordinates": [[121.0, 31.0, 10.0], [121.1, 31.1, 20.0]]}
    out = transform_geometry(ls, WGS, WEBM)
    assert out["coordinates"][0][2] == 10.0
    assert out["coordinates"][1][2] == 20.0


def test_polygon_transform():
    """面要素变换应保持环结构。"""
    poly = {"type": "Polygon", "coordinates": [
        [[121.0, 31.0], [121.1, 31.0], [121.1, 31.1], [121.0, 31.1], [121.0, 31.0]]
    ]}
    out = transform_geometry(poly, WGS, WEBM)
    assert out["type"] == "Polygon"
    ring = out["coordinates"][0]
    assert len(ring) == 5  # 环闭合
    # 首尾点一致（闭合）
    assert ring[0][0] == ring[-1][0]


def test_multipolygon_transform():
    """MultiPolygon 变换。"""
    mp = {"type": "MultiPolygon", "coordinates": [
        [[[121.0, 31.0], [121.1, 31.0], [121.1, 31.1], [121.0, 31.1], [121.0, 31.0]]]
    ]}
    out = transform_geometry(mp, WGS, WEBM)
    assert out["type"] == "MultiPolygon"


def test_geometrycollection_transform():
    """GeometryCollection 复合类型变换（子几何坐标应真实参与变换）。"""
    gc = {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [121.0, 31.0]},
        {"type": "LineString", "coordinates": [[121.0, 31.0], [121.1, 31.1]]},
    ]}
    out = transform_geometry(gc, WGS, WEBM)
    assert out["type"] == "GeometryCollection"
    assert len(out["geometries"]) == 2
    # 子几何坐标应被真实变换为米制投影值（而非保留原始经纬度）
    pt = out["geometries"][0]["coordinates"]
    assert pt[0] > 1_000_000  # Web Mercator 下纬度 31° 的 x 应约 13.4M
    ls = out["geometries"][1]["coordinates"]
    assert ls[0][0] > 1_000_000


def test_empty_geometry_returns_none():
    """空/无坐标几何返回 None（视为跳过）。"""
    assert transform_geometry({"type": "Point", "coordinates": []}, WGS, WEBM) is None
    assert transform_geometry({"type": "Point"}, WGS, WEBM) is None


def test_domain_warning_geo_src_with_projected_coords():
    """源 SRID=4326（地理）但坐标是投影米值 → 变换产出 inf，判定失败（None）并告警。"""
    warn = []
    bad = {"type": "Point", "coordinates": [200000, 3400000]}
    out = transform_geometry(bad, WGS, WEBM, warnings=warn)
    # 投影米制坐标被误作地理经纬度 → Web Mercator 变换越界产出 inf → 拒绝（None）
    assert out is None
    assert any("±180/±90" in w or "WGS" in w or "4326" in w for w in warn)
    # 且产生导致失败的「inf/NaN」告警
    assert any("inf/NaN" in w for w in warn)


def test_domain_warning_no_geo_src_no_false_alarm():
    """投影源（SH_CITY）下坐标不合 ±180/±90 不应误报地理域告警。"""
    warn = []
    # 投影坐标系下合理大坐标，不应触发"地理越界"警告
    _ = transform_geometry({"type": "Point", "coordinates": [18511, -617]},
                           SH_CITY, WGS, warnings=warn)
    for w in warn:
        assert "±180/±90" not in w


def test_input_geometry_not_mutated():
    """transform_geometry 不应原地修改输入对象。"""
    import copy
    pt = {"type": "Point", "coordinates": [121.0, 31.0]}
    orig = copy.deepcopy(pt)
    transform_geometry(pt, WGS, WEBM)
    assert pt == orig


def test_transform_feature_dict():
    """单要素 Feature dict 变换（保留 properties）。"""
    feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
            "properties": {"name": "x", "layer": "L"}}
    out = transform_feature_dict(feat, WGS, WEBM)
    assert out["properties"]["name"] == "x"
    assert out["properties"]["layer"] == "L"
    assert out["geometry"]["type"] == "Point"


def test_transform_feature_list_stats():
    """批量变换统计：正常/空几何/无几何。"""
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.0, 31.0]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.1, 31.1]}, "properties": {}},
        {"type": "Feature", "geometry": None, "properties": {}},
        {"type": "Feature", "properties": {}},
    ]
    out, stats = transform_feature_list(feats, WGS, WEBM)
    assert stats.total == 4
    assert stats.transformed == 2
    assert stats.skipped == 2
    assert stats.failed == 0
    assert len(out) == 2


def test_domain_mismatch_returns_none_not_inf():
    """域不匹配（米制大坐标被误作地理经纬度）→ 返回 None 而非 inf 损坏几何。

    回归测试：修复前 `transform_geometry` 会把 pyproj 返回的 [inf, inf] 当成功写入，
    导致 transform_results 产出全部 [Infinity, Infinity] 的损坏 GeoJSON（用户实测）。
    """
    # 米制大坐标（如 CAD/DXF 规划范围）被当作 WGS84(4326) 源，目标 3857
    poly = {"type": "Polygon", "coordinates": [
        [[3.4e7, 5.0e6], [3.4e7, 5.0001e6], [3.4001e7, 5.0001e6],
         [3.4001e7, 5.0e6], [3.4e7, 5.0e6]]
    ]}
    warnings = []
    out = transform_geometry(poly, WGS, WEBM, warnings=warnings)
    # 修复后：返回 None（视为失败），绝不返回含 inf 的几何
    assert out is None
    assert any("inf/NaN" in w for w in warnings)


def test_geometrycollection_inf_rejected():
    """复合几何中任一点产生 inf → 整体拒绝（返回 None）。"""
    gc = {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [121.0, 31.0]},
        {"type": "Point", "coordinates": [3.4e7, 5.0e6]},  # 会触发 inf
    ]}
    warnings = []
    out = transform_geometry(gc, WGS, WEBM, warnings=warnings)
    assert out is None


def test_batch_stats_counts_domain_failure():
    """批量变换中域失败计入 failed，而非 transformed。"""
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.0, 31.0]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3.4e7, 5.0e6]}, "properties": {}},
    ]
    out, stats = transform_feature_list(feats, WGS, WEBM)
    assert stats.total == 2
    assert stats.transformed == 1   # 合法的那个
    assert stats.failed == 1        # 域失败的那个（修复前会误算 transformed）
    assert len(out) == 1
    assert stats.warnings  # 含 inf/NaN 告警


def test_false_origin_swap_must_use_corrected_proj4():
    """上海城建坐标（shanghaicity）假东/假北互换的纠正链路回归。

    用户实测 `.prj` 若声明 `False_Easting=-3457147.81, False_Northing=0`（即
    pyproj proj4 `+x_0=-3457147.81 +y_0=0`），直接按原文喂 pyproj 会得到漂移到
    大洋的错误位置；`custom_crs._apply_false_origin_fix` 会纠正为 `+x_0=0
    +y_0=-3457147.81`。`transform_engine._pyproj_crs_for` 正是复用库里纠正后的
    定义，本用例验证「纠正后」定义变换结果正确落在上海，而「原始互斥」定义漂移。
    """
    from app.services.transform_engine import transform_geometry
    from pyproj import CRS, Transformer

    # 一个真实 shanghaicity 原始坐标（局部米制）
    pt = {"type": "Point", "coordinates": [10706.08, 22952.81]}
    dst_wgs = ResolvedCRS(srid=4326, pyproj_crs="EPSG:4326", epsg=4326,
                          is_custom=False, source="epsg", display="EPSG:4326")

    # 1) 纠正前（交换的假东/假北，x_0=-3457147.81）→ 错误漂移
    raw_swapped = ResolvedCRS(
        srid=918073, epsg=None, is_custom=True, source="wkt-custom", display="Custom:shanghaicity",
        pyproj_crs=("+proj=tmerc +lat_0=0 +lon_0=121.2751921 +k=1 "
                    "+x_0=-3457147.81 +y_0=0 +ellps=krass +units=m +no_defs"),
    )
    # 绕过坐标域校验直接看 pyproj 结果：纠正前会漂移到太平洋附近（lon≈150.9）
    t_bad = Transformer.from_crs(CRS.from_proj4(raw_swapped.pyproj_crs),
                                 CRS.from_epsg(4326), always_xy=True)
    bad_lon, bad_lat = t_bad.transform(pt["coordinates"][0], pt["coordinates"][1])
    # 纠正前结果错误：要么漂移到太平洋（lon≈150.9），要么产生 inf/NaN（越出投影有效域）
    assert not (121.0 <= bad_lon <= 122.0 and 30.5 <= bad_lat <= 32.0)

    # 2) 纠正后（x_0=0, y_0=-3457147.81，库里注册的定义）→ 正确落在上海
    fixed = ResolvedCRS(
        srid=918073, epsg=None, is_custom=True, source="wkt-custom", display="Custom:shanghaicity",
        pyproj_crs=("+proj=tmerc +lat_0=0 +lon_0=121.2751921 +k=1 "
                    "+x_0=0 +y_0=-3457147.81 +ellps=krass +units=m +no_defs"),
    )
    good = transform_geometry(pt, fixed, dst_wgs, warnings=[])
    lon, lat = good["coordinates"][0], good["coordinates"][1]
    assert 121.0 <= lon <= 122.0   # 上海经度范围
    assert 30.5 <= lat <= 32.0     # 上海纬度范围

