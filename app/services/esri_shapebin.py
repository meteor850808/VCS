"""Decode ESRI Personal Geodatabase shapebin BLOB → GeoJSON geometry.

PGeo 的 SHAPE 列存储的是 "shapefile binary fragments"（ESRI shapebin 格式），
不是 OGC WKB。GDAL 通过 OGRCreateFromShapeBin() 解码。

格式（全部 little-endian）：
  bytes 0-3  : uint32 shape type
  bytes 4-35 : bbox (4 × float64: Xmin, Ymin, Xmax, Ymax)
  bytes 36+  : 依类型而定（见下）

shape type 取值（可附加 0x20000000 曲线标志 / 0x80000000 等）：
  0 = Null, 1 = Point, 3 = PolyLine, 5 = Polygon, 8 = MultiPoint
  11 = PointZ, 13 = PolyLineZ, 15 = PolygonZ, 18 = MultiPointZ
  21 = PointM, 23 = PolyLineM, 25 = PolygonM, 28 = MultiPointM
  50 = General PolyLine, 51 = General Polygon (PGeo 特有)
"""
import json
import logging
import struct
from typing import Any

logger = logging.getLogger(__name__)

# shapebin 类型常量
SHPT_NULL = 0
SHPT_POINT = 1
SHPT_ARC = 3
SHPT_POLYGON = 5
SHPT_MULTIPOINT = 8
SHPT_POINTZ = 11
SHPT_ARCZ = 13
SHPT_POLYGONZ = 15
SHPT_MULTIPOINTZ = 18
SHPT_POINTM = 21
SHPT_ARCM = 23
SHPT_POLYGONM = 25
SHPT_MULTIPOINTM = 28
SHPT_GENERALPOLYLINE = 50  # 0x32
SHPT_GENERALPOLYGON = 51   # 0x33

# 附加标志位
FLAG_CURVES = 0x20000000  # 0x20000000：PGeo 曲线几何（general shape），
                          # 同时保留通用线/面的类型本体 E.g. 0x20000033

# 有效的 shapebin 类型本体（低 8 位）
_VALID_SHAPE_TYPES = {
    SHPT_NULL, SHPT_POINT, SHPT_ARC, SHPT_POLYGON, SHPT_MULTIPOINT,
    SHPT_POINTZ, SHPT_ARCZ, SHPT_POLYGONZ, SHPT_MULTIPOINTZ,
    SHPT_POINTM, SHPT_ARCM, SHPT_POLYGONM, SHPT_MULTIPOINTM,
    SHPT_GENERALPOLYLINE, SHPT_GENERALPOLYGON,
}

# 可接受的高位标志（曲线 0x20000000、Z 0x80000000、M 0x40000000 及组合）
_VALID_FLAGS = {
    0x00000000, 0x20000000, 0x40000000, 0x80000000,
    0x60000000, 0xA0000000, 0xC0000000, 0xE0000000,
}


def _read_uint32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from('<I', data, offset)[0], offset + 4


def _read_int32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from('<i', data, offset)[0], offset + 4


def _read_double(data: bytes, offset: int) -> tuple[float, int]:
    return struct.unpack_from('<d', data, offset)[0], offset + 8


def decode_esri_shapebin(blob: bytes) -> dict | None:
    """解码 ESRI PGeo shapebin BLOB → GeoJSON geometry dict。

    Args:
        blob: SHAPE 列的二进制数据

    Returns:
        GeoJSON geometry dict（坐标 XY），无法解析返回 None
    """
    if not blob or len(blob) < 8:
        return None

    shape_type_raw, offset = _read_uint32(blob, 0)

    # 类型本体取低 8 位：0x20000033 → 0x33 (SHPT_GENERALPOLYGON)
    shape_type = shape_type_raw & 0xFF
    flags = shape_type_raw & 0xFFFFFF00
    has_curves = bool(shape_type_raw & FLAG_CURVES)

    # 校验：类型本体 + 高位标志必须合法，否则拒绝（可能是 WKB 等其它格式）
    if shape_type not in _VALID_SHAPE_TYPES or flags not in _VALID_FLAGS:
        return None

    try:
        if shape_type == SHPT_NULL:
            return None
        elif shape_type == SHPT_POINT:
            return _decode_point(blob, offset)
        elif shape_type in (SHPT_ARC, SHPT_POLYGON,
                            SHPT_GENERALPOLYLINE, SHPT_GENERALPOLYGON):
            return _decode_linestring_or_polygon(blob, offset, shape_type,
                                                 has_curves)
        elif shape_type in (SHPT_ARCZ, SHPT_POLYGONZ,
                            SHPT_ARCM, SHPT_POLYGONM):
            return _decode_linestring_or_polygon(blob, offset, shape_type,
                                                 has_curves)
        elif shape_type in (SHPT_MULTIPOINT, SHPT_MULTIPOINTZ,
                            SHPT_MULTIPOINTM):
            return _decode_multipoint(blob, offset)
        else:
            logger.debug(f"Unsupported PGeo shape type: {shape_type}")
            return None
    except (struct.error, IndexError) as e:
        logger.debug(f"PGeo shapebin decode error: {e}")
        return None


def _decode_point(blob: bytes, offset: int) -> dict:
    x, offset = _read_double(blob, offset)
    y, _ = _read_double(blob, offset)
    return {"type": "Point", "coordinates": [x, y]}


def _decode_multipoint(blob: bytes, offset: int) -> dict:
    # 跳过 bbox (4 doubles)
    offset += 32
    if len(blob) < offset + 4:
        raise struct.error("multipoint: truncated header")
    n_points, offset = _read_int32(blob, offset)
    coords = []
    for _ in range(n_points):
        x, offset = _read_double(blob, offset)
        y, offset = _read_double(blob, offset)
        coords.append([x, y])
    return {"type": "MultiPoint", "coordinates": coords}


def _ring_is_cw(ring: list[list[float]]) -> bool:
    """用 shoelace 公式判断环方向。shapefile 约定：外环 CW（负面积）。"""
    if len(ring) < 3:
        return True
    twice_area = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        twice_area += x1 * y2 - x2 * y1
    return twice_area < 0


def _decode_linestring_or_polygon(blob: bytes, offset: int, shape_type: int,
                                  has_curves: bool) -> dict:
    """解码 PolyLine/Polygon（及其 Z/M/general 变体）几何。

    PGeo 的线面 BLOB 结构：
      bbox(32) + numParts(4) + numPoints(4) + parts[numParts×4]
      + points[numPoints×16]
    其中点数组在 parts 数组之后连续存放。

    General shapes (0x32/0x33 + 0x20000000) 在 points 之后还附加
    曲线段定义（sub-shape 数组），线性部分不变，可直接忽略尾部。
    """
    # 跳过 bbox
    offset += 32
    if len(blob) < offset + 8:
        raise struct.error("line/poly: truncated header")

    n_parts, offset = _read_int32(blob, offset)
    n_points, offset = _read_int32(blob, offset)

    if n_parts < 0 or n_points < 0 or n_parts * n_points > 100_000_000:
        raise struct.error("line/poly: implausible counts")

    # parts 数组
    parts = []
    for _ in range(n_parts):
        p, offset = _read_int32(blob, offset)
        parts.append(p)

    # points 数组
    points = []
    for _ in range(n_points):
        x, offset = _read_double(blob, offset)
        y, offset = _read_double(blob, offset)
        points.append([x, y])

    is_polygon = shape_type in (SHPT_POLYGON, SHPT_POLYGONZ, SHPT_POLYGONM,
                                SHPT_GENERALPOLYGON)

    if is_polygon:
        # 每个 part 是一个 ring
        parts_end = list(parts[1:]) + [n_points]
        rings = [points[start:end] for start, end in zip(parts, parts_end)]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            return None

        # 按 shapefile 约定区分外环(CW)与内环(CCW)
        cw_rings = [r for r in rings if _ring_is_cw(r)]
        ccw_rings = [r for r in rings if not _ring_is_cw(r)]

        if cw_rings:
            # 有顺时针环：作为外环，逆时针环作为内环
            outer_rings = cw_rings
            holes = ccw_rings
        elif len(rings) == 1:
            # 仅一个环：不论方向都作为外环
            outer_rings = rings
            holes = []
        else:
            # 方向信息缺失/不规范：按 PGeo 顺序（首环为外环，余为洞）
            outer_rings = [rings[0]]
            holes = rings[1:]

        if len(outer_rings) == 1:
            return {"type": "Polygon",
                    "coordinates": [outer_rings[0]] + holes}
        # 多外环 → MultiPolygon（每个外环 + 落入其内环的匹配由后续
        # make_valid 处理，这里退化为一环一多边形）
        return {"type": "MultiPolygon",
                "coordinates": [[r] for r in outer_rings]}
    else:
        # PolyLine：每 part 一个 linestring
        parts_end = list(parts[1:]) + [n_points]
        linestrings = []
        for start, end in zip(parts, parts_end):
            line = points[start:end]
            if line:
                linestrings.append(line)
        if not linestrings:
            return None
        if len(linestrings) == 1:
            return {"type": "LineString", "coordinates": linestrings[0]}
        return {"type": "MultiLineString", "coordinates": linestrings}


def shapebin_to_geojson(blob: bytes) -> dict | None:
    """便捷封装：shapebin BLOB → GeoJSON geometry (dict)。"""
    return decode_esri_shapebin(blob)


if __name__ == "__main__":
    import sys
    import pyodbc

    from app.services._odbc import access_conn_str

    path = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\Administrator\Desktop\AI项目\空间质检\data\uploads\quality"
        r"\7e230648-1922-42e1-88a5-90d1937357c8\1779864305770_Migration_KCDJ.mdb")
    conn = pyodbc.connect(access_conn_str(path))
    cursor = conn.cursor()
    for tbl in ("LANDSURVEY_DEMARCATE_RANGE", "LANDSURVEY_DETAIL_PARCEL",
                "LANDSURVEY_PLANNING_RANGE"):
        try:
            cursor.execute(f"SELECT TOP 3 SHAPE FROM [{tbl}]")
            rows = cursor.fetchall()
            print(f"\n=== {tbl} ===")
            for i, row in enumerate(rows):
                g = decode_esri_shapebin(bytes(row[0]))
                print(f"  feature {i}: {json.dumps(g, ensure_ascii=False)[:160]}")
        except Exception as e:
            print(f"  {tbl} failed: {e}")
    conn.close()