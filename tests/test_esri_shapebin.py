# -*- coding: utf-8 -*-
"""ESRI Personal Geodatabase shapebin BLOB decoder unit tests."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.esri_shapebin import decode_esri_shapebin


def _polygon_blob(coords, parts=None):
    """Construct a PGeo polygon/line shapebin BLOB manually.

    Structure: type(4) + bbox(32) + numParts(4) + numPoints(4)
               + parts[nparts*4] + points[npoints*16]
    """
    n_points = len(coords)
    n_parts = len(parts) if parts else 1
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]

    blob = struct.pack('<I', 5)  # SHPT_POLYGON
    # bbox
    blob += struct.pack('<4d', min(xs), min(ys), max(xs), max(ys))
    # counts
    blob += struct.pack('<2i', n_parts, n_points)
    # parts array
    offsets = parts if parts else [0]
    for off in offsets:
        blob += struct.pack('<i', off)
    # points
    for x, y in coords:
        blob += struct.pack('<2d', x, y)
    return blob


def _polyline_blob(coords, parts=None):
    """Construct a PGeo polyline shapebin BLOB (type=3)."""
    blob = _polygon_blob(coords, parts)
    return struct.pack('<I', 3) + blob[4:]


def _point_blob(x, y):
    """Construct a PGeo point shapebin BLOB (type=1)."""
    return struct.pack('<I', 1) + struct.pack('<2d', x, y)


def _multipoint_blob(points):
    """Construct a PGeo multipoint shapebin BLOB (type=8)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    blob = struct.pack('<I', 8)
    blob += struct.pack('<4d', min(xs), min(ys), max(xs), max(ys))
    blob += struct.pack('<i', len(points))
    for x, y in points:
        blob += struct.pack('<2d', x, y)
    return blob


def _general_polygon_blob(coords, parts=None):
    """PGeo general polygon BLOB: 0x20000000 | 0x33."""
    blob = _polygon_blob(coords, parts)
    return struct.pack('<I', 0x20000033) + blob[4:]


# --- 基础解码 ---

def test_decode_point():
    blob = _point_blob(10.5, 20.5)
    geom = decode_esri_shapebin(blob)
    assert geom == {"type": "Point", "coordinates": [10.5, 20.5]}


def test_decode_polygon_single_ring():
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    geom = decode_esri_shapebin(_polygon_blob(ring))
    assert geom["type"] == "Polygon"
    assert geom["coordinates"] == [ring]


def test_decode_polygon_two_rings():
    """两个 part 可解码（外环 + 内环不做内外判定，保留 rings）。"""
    outer = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
    inner = [[25, 25], [75, 25], [75, 75], [25, 75], [25, 25]]
    coords = outer + inner
    parts = [0, 5]
    geom = decode_esri_shapebin(_polygon_blob(coords, parts))
    assert geom["type"] == "Polygon"
    assert len(geom["coordinates"]) == 2
    assert geom["coordinates"][0] == outer
    assert geom["coordinates"][1] == inner


def test_decode_polyline():
    line = [[0, 0], [1, 1], [2, 2]]
    geom = decode_esri_shapebin(_polyline_blob(line))
    assert geom == {"type": "LineString", "coordinates": line}


def test_decode_multiline():
    line1 = [[0, 0], [1, 1]]
    line2 = [[10, 10], [11, 11], [12, 12]]
    coords = line1 + line2
    parts = [0, 2]
    geom = decode_esri_shapebin(_polyline_blob(coords, parts))
    assert geom["type"] == "MultiLineString"
    assert geom["coordinates"] == [line1, line2]


def test_decode_multipoint():
    pts = [[1, 2], [3, 4]]
    geom = decode_esri_shapebin(_multipoint_blob(pts))
    assert geom == {"type": "MultiPoint", "coordinates": pts}


def test_decode_general_polygon():
    """PGeo 曲线面 (0x20000033) 线性部分可解码。"""
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    geom = decode_esri_shapebin(_general_polygon_blob(ring))
    assert geom["type"] == "Polygon"


# --- 容错 ---

def test_decode_none_and_empty():
    assert decode_esri_shapebin(None) is None
    assert decode_esri_shapebin(b"") is None
    assert decode_esri_shapebin(b"\x00" * 4) is None  # SHPT_NULL


def test_decode_truncated():
    """截断的 BLOB 不应抛异常，返回 None。"""
    blob = _polygon_blob([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    assert decode_esri_shapebin(blob[:20]) is None


def test_decode_implausible_counts():
    """异常 counts 不应导致崩溃/超大分配。"""
    blob = struct.pack('<I', 5) + struct.pack('<4d', 0, 0, 1, 1)
    blob += struct.pack('<2i', 10_000_000, 10_000_000)  # 不合理的 part/point 数
    assert decode_esri_shapebin(blob) is None


def test_decode_wkb_not_misparsed():
    """标准 WKB 字节不应被当作 shapebin 解码（返回 None 而非垃圾坐标）。"""
    wkb_point = bytes.fromhex(
        "0101000000000000000000F03F0000000000000040")
    assert decode_esri_shapebin(wkb_point) is None


# --- 真实坐标（米制 GK）---

def test_decode_real_coordinate_scale():
    """坐标保持原始双精度（GK 米制），不做无权缩放。"""
    geom = decode_esri_shapebin(_point_blob(41376997.4719, 3474830.3708))
    assert geom["coordinates"] == [41376997.4719, 3474830.3708]