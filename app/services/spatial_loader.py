"""空间数据加载 — 将矢量文件展开为逐要素记录。

PostGIS 模式：geometry 以 WKT 格式存储，供 geoalchemy2 写入。
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape, Polygon, MultiPolygon, mapping
from shapely.ops import transform
from shapely import wkt
from shapely.validation import make_valid


def _force_2d(geom):
    """移除几何体的 Z 维度（PostGIS 列类型为 2D GEOMETRY，不支持 GEOMETRYZ）。"""
    if geom.has_z:
        return transform(lambda x, y, z=None: (x, y), geom)
    return geom


def geojson_to_wkt(geojson_geom: dict) -> str:
    """将 GeoJSON geometry dict 转为 WKT 字符串（供 PostGIS 存储）。

    自动移除 Z 维度，因为数据库列定义为 2D Geometry(GEOMETRY, 4326)。
    无效几何自动执行 make_valid 修复，避免触发 ST_IsValid CHECK 约束。
    """
    return geojson_to_wkt_status(geojson_geom)[0]


def geojson_to_wkt_status(geojson_geom: dict) -> tuple[str, bool]:
    """将 GeoJSON geometry dict 转为 WKT 字符串，并返回是否可安全写入。

    Returns:
        (wkt_str, ok) — ok=True 表示几何有效（或已修复）；ok=False 表示
        几何无法解析/无法修复，返回 "GEOMETRYCOLLECTION EMPTY"。
        调用方可将 ok=False 的要素以 geometry=NULL 落库（属性保留）。

    S3：shapely 不支持的 3D 复合类型（MultiPatch/PolyhedralSurface/TIN）按面语义
    降维为 2D MultiPolygon/Polygon 入库（shell 外环语义 ≈ MultiPolygon 外环）。
    """
    if not geojson_geom or "type" not in geojson_geom:
        return "GEOMETRYCOLLECTION EMPTY", False
    try:
        gtype = geojson_geom["type"]
        coords = geojson_geom.get("coordinates")
        # 降维：MultiPatch/PolyhedralSurface/TIN → 2D 面集合
        if gtype in ("MultiPatch", "PolyhedralSurface") and coords:
            # 结构 ≈ MultiPolygon：每 surface 首环为外环、其余为内环
            geojson_geom = {"type": "MultiPolygon", "coordinates": coords}
        elif gtype == "TIN" and coords:
            # TIN：每三角形为一 Polygon
            geojson_geom = {"type": "MultiPolygon", "coordinates": [[tri] for tri in coords]}
        shp = shape(geojson_geom)
        shp = _force_2d(shp)
        if not shp.is_valid:
            # 自相交 / 环方向错误等 → 几何修复，避免 CHECK 约束整体回滚
            repaired = make_valid(shp)
            if repaired and not repaired.is_empty and repaired.is_valid:
                shp = repaired
            else:
                return "GEOMETRYCOLLECTION EMPTY", False
        return wkt.dumps(shp), True
    except Exception:
        return "GEOMETRYCOLLECTION EMPTY", False


def extract_features_from_file(file_path: str, file_format: str | None = None) -> list[dict]:
    """从矢量文件中提取所有要素。

    返回 list[dict]，每项包含:
        - geometry: GeoJSON geometry dict
        - properties: dict
        - feature_index: int
        - layer_name: str
    """
    if file_format is None:
        file_format = _guess_format(file_path)

    if file_format == "GeoJSON":
        return _extract_from_geojson(file_path)
    elif file_format == "OBJ":
        # S3：OBJ 3D 模型 → 对象级质心要素（走 obj_parser.py）
        return _extract_from_obj(file_path)
    elif file_format == "DWG":
        # DWG：GDAL 无驱动，经 LibreDWG dwg2dxf 转 DXF 后复用 Fiona 解析
        return _extract_from_dwg(file_path)
    else:
        # 用 Fiona 读取（支持 SHP/GPKG/GML/KML/DXF 等）
        feats = _extract_with_fiona(file_path, file_format)
        if (file_format or "").upper() == "DXF":
            # 【v2.10.5】DXF 直传与 DWG 转换链路同语义：
            # 先归位真实 CAD 图层，再做闭合多段线 → 面还原
            feats = _cad_lines_to_polygons(_promote_cad_layers(feats))
        return feats


def _promote_cad_layers(features: list[dict]) -> list[dict]:
    """CAD 真实图层归位（DXF/DWG，v2.10.5）。

    GDAL/Fiona 的 DXF 驱动把所有实体放进名为 'entities' 的单一 OGR 图层，
    要素的真实 CAD 图层名存于属性 'Layer' 字段。将其提升为要素的 layer_name，
    使多图层 CAD 数据在存储/统计/质检圈定/展示链路与 GDB 多图层同构。
    """
    for f in features:
        props = f.get("properties") or {}
        cad_layer = props.get("Layer") or props.get("layer")
        if isinstance(cad_layer, str) and cad_layer.strip():
            f["layer_name"] = cad_layer.strip()
    return features


def _extract_from_dwg(file_path: str) -> list[dict]:
    """DWG 要素提取：dwg2dxf 转换 → Fiona DXF 解析；工具缺失/失败时优雅降级为空。"""
    from app.services.dwg_converter import dwg_to_dxf, cleanup_converted_dxf
    dxf_path = dwg_to_dxf(file_path)
    if not dxf_path:
        return []
    try:
        return _promote_cad_layers(
            _cad_lines_to_polygons(_extract_with_fiona(dxf_path, "DXF"))
        )
    finally:
        cleanup_converted_dxf(dxf_path)


def _is_closed_ring(coords, abs_tol: float = 1e-6) -> bool:
    """闭合环判定：至少 4 点且首尾坐标在容差内重合。"""
    try:
        if not coords or len(coords) < 4:
            return False
        (x0, y0), (x1, y1) = coords[0][:2], coords[-1][:2]
        return abs(x0 - x1) <= abs_tol and abs(y0 - y1) <= abs_tol
    except Exception:
        return False


def _cad_lines_to_polygons(features: list[dict]) -> list[dict]:
    """CAD 闭合多段线 → 面要素（DWG 语义还原，v2.10.5）。

    背景：GDAL DXF 驱动将闭合 LWPOLYLINE（flag&1）输出为 LineString/
    MultiLineString，而 CAD 制图中闭合多段线通常表达面边界（地块、蓝线/
    红线范围等）。本转换将闭合环提升为 Polygon：
      - LineString 闭合且面积非退化 → Polygon（make_valid 修复自相交）
      - MultiLineString 全部闭合   → 单环 Polygon / 多环 MultiPolygon
      - 混合（部分闭合）           → 拆为一个面要素 + 一个线要素
      - 面积≈0 的退化环 / 转换失败  → 保留原线形态
    """
    out: list[dict] = []
    idx = 0

    def _push(feat, geom):
        nonlocal idx
        out.append({**feat, "geometry": geom, "feature_index": idx})
        idx += 1

    for f in features:
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        converted = False
        try:
            if gtype == "LineString" and _is_closed_ring(coords):
                poly = Polygon(coords)
                if poly.area > 1e-10:
                    if not poly.is_valid:
                        poly = make_valid(poly)
                    if poly.geom_type in ("Polygon", "MultiPolygon") and not poly.is_empty:
                        _push(f, mapping(poly))
                        converted = True
            elif gtype == "MultiLineString" and coords:
                rings = [c for c in coords if _is_closed_ring(c)]
                opens = [c for c in coords if not _is_closed_ring(c)]
                if rings or opens:
                    polys: list[Polygon] = []
                    for c in rings:
                        p = Polygon(c)
                        if p.area <= 1e-10:
                            continue
                        if not p.is_valid:
                            p = make_valid(p)
                        if p.geom_type == "Polygon":
                            polys.append(p)
                        elif p.geom_type == "MultiPolygon":
                            polys.extend(list(p.geoms))
                    if polys:
                        merged = polys[0] if len(polys) == 1 else MultiPolygon(polys)
                        _push(f, mapping(merged))
                        converted = True
                    if opens:
                        _push(f, {"type": "MultiLineString", "coordinates": opens})
                        converted = True
        except Exception:
            converted = False
        if not converted:
            out.append({**f, "feature_index": idx})
            idx += 1
    return out


def extract_features_from_bytes(content: bytes, filename: str) -> list[dict]:
    """从文件二进制内容中提取要素。"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".tmp", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        fmt = _guess_format(tmp_path)
        return extract_features_from_file(tmp_path, fmt)
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _extract_from_geojson(file_path: str) -> list[dict]:
    """从 GeoJSON 文件提取要素；解析失败（非 UTF-8/损坏 JSON）优雅降级为空。"""
    try:
        with open(file_path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"GeoJSON 解析失败 ({file_path}): {e}")
        return []

    features = []
    raw_features = []

    if data.get("type") == "FeatureCollection":
        raw_features = data.get("features", [])
    elif data.get("type") == "Feature":
        raw_features = [data]
    else:
        return []

    for idx, feat in enumerate(raw_features):
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if geom and geom.get("type"):
            features.append({
                "geometry": geom,
                "properties": props,
                "feature_index": idx,
                "layer_name": Path(file_path).stem,
            })
    return features


def _extract_from_obj(file_path: str) -> list[dict]:
    """从 OBJ 3D 模型文件提取要素（对象级质心，属性含顶点/面片统计）。

    S3：接入 obj_parser.py；每个 o 对象生成一个质心 Point 要素，
    Z 维度在写入时由 _force_2d 降维（数据库为 2D 存储）。
    """
    from app.services.obj_parser import parse_obj_file, obj_to_geojson_features
    try:
        parsed = parse_obj_file(file_path)
        features = obj_to_geojson_features(parsed)
        for i, feat in enumerate(features):
            feat["feature_index"] = i
            feat["layer_name"] = Path(file_path).stem
        return features
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"OBJ 解析失败 ({file_path}): {e}")
        return []


def _extract_with_fiona(file_path: str, file_format: str | None = None) -> list[dict]:
    """用 Fiona 读取矢量文件提取要素。"""
    import fiona
    features = []
    feat_idx = 0
    try:
        for layer_name in fiona.listlayers(file_path):
            layer_feat_count = 0
            with fiona.open(file_path, layer=layer_name) as src:
                for feat in src:
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}
                    if geom and geom.get("type"):
                        features.append({
                            "geometry": geom,
                            "properties": dict(props),
                            "feature_index": feat_idx,
                            "layer_name": layer_name,
                        })
                        feat_idx += 1
                        layer_feat_count += 1
            # 【修复】移除 early-break：即使当前图层为空，也继续读取后续图层（GDB 多图层场景）
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Fiona读取失败 ({file_path}): {e}")
    return features


def _guess_format(file_path: str) -> str | None:
    """从文件扩展名推断格式；未知扩展名返回 None（走 Fiona 驱动嗅探）。

    【修复 v2.10.5】此前未知扩展名兜底返回 'GeoJSON'，导致任何未识别的二进制
    文件（如 .dwg）被当作 GeoJSON 做严格 UTF-8 json.load 而抛 UnicodeDecodeError。
    """
    ext = Path(file_path).suffix.lower()
    return {
        '.geojson': 'GeoJSON',
        '.json': 'GeoJSON',
        '.shp': 'ESRI Shapefile',
        '.gpkg': 'GPKG',
        '.gml': 'GML',
        '.kml': 'KML',
        '.dxf': 'DXF',
        '.dgn': 'DGN',
        '.dwg': 'DWG',
        '.tab': 'MapInfo File',
        '.csv': 'CSV',
        '.obj': 'OBJ',
    }.get(ext)
