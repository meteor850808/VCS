# -*- coding: utf-8 -*-
"""转换结果 GeoJSON 的 CRS 声明测试。

验证：转换落库/下载的 FeatureCollection 需带 `crs` 成员声明目标坐标系
（RFC 7946 缺省为 WGS84/4326，若缺失，4490/4519 等结果会被下游 GIS 工具
误判为 WGS84 显示 —— 用户实测问题）。
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.v1.spatial import _inject_crs_member


def _fc() -> bytes:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.28, 30.82]}, "properties": {}}
    ]}
    return json.dumps(fc).encode("utf-8")


def test_injects_crs_for_target_srid():
    out = _inject_crs_member(_fc(), 4490)
    fc = json.loads(out)
    assert fc["crs"] == {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4490"}}
    # 要素内容不被破坏
    assert fc["features"][0]["geometry"]["coordinates"] == [121.28, 30.82]


def test_no_change_when_srid_none():
    assert _inject_crs_member(_fc(), None) == _fc()


def test_pristine_when_crs_already_present():
    fc = json.loads(_fc())
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4519"}}
    raw = json.dumps(fc).encode("utf-8")
    # 已含 crs 时保持原样（不覆盖）
    assert _inject_crs_member(raw, 4519) == raw


def test_invalid_payload_returned_as_is():
    bad = b"not json"
    assert _inject_crs_member(bad, 4490) == bad
    assert _inject_crs_member(b"", 4490) == b""


def test_custom_srid_uses_vcs_namespace_not_bogus_epsg():
    """自定义 SRID（如 shanghaicity=918984）不在 EPSG 库中，
    `urn:ogc:def:crs:EPSG::918984` 是无效 URN，应改用 VCS-CUSTOM 命名空间。"""
    out = _inject_crs_member(_fc(), 918984)
    fc = json.loads(out)
    assert fc["crs"]["properties"]["name"] == "urn:ogc:def:crs:VCS-CUSTOM::918984"
    # 绝不假装是 EPSG 编码
    assert "EPSG::918984" not in out.decode()


def test_boundary_standard_srid_keeps_epsg():
    # CUSTOM_SRID_BASE=910000 之下的 SRID 仍用 EPSG 命名空间
    out = _inject_crs_member(_fc(), 909999)
    fc = json.loads(out)
    assert fc["crs"]["properties"]["name"] == "urn:ogc:def:crs:EPSG::909999"
    # 标准边界值 910000 起转 VCS
    out2 = _inject_crs_member(_fc(), 910000)
    fc2 = json.loads(out2)
    assert fc2["crs"]["properties"]["name"] == "urn:ogc:def:crs:VCS-CUSTOM::910000"


def test_bogus_epsg_crs_on_custom_srid_corrected():
    """早期版本可能已把自定义 SRID 写成无效的 EPSG::918984，下载时应纠正为 VCS-CUSTOM。"""
    fc = json.loads(_fc())
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::918984"}}
    raw = json.dumps(fc).encode("utf-8")
    out = _inject_crs_member(raw, 918984)
    fc2 = json.loads(out)
    assert fc2["crs"]["properties"]["name"] == "urn:ogc:def:crs:VCS-CUSTOM::918984"


def test_standard_crs_with_existing_name_kept():
    """标准 SRID 下已有 crs 时保持原样（不覆盖用户手动写的 EPSG URN）。"""
    fc = json.loads(_fc())
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4490"}}
    raw = json.dumps(fc).encode("utf-8")
    assert _inject_crs_member(raw, 4490) == raw
