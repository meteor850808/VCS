"""几何修复 — shapely/GEOS 集成，修复无效几何。"""

from dataclasses import dataclass, field
from enum import Enum


class RepairStrategy(str, Enum):
    AUTO_FIX = "auto_fix"    # 自动修复 + 记录
    ISOLATE = "isolate"      # 分离无效要素
    DROP = "drop"            # 丢弃无效要素
    RAISE = "raise"          # 遇到无效抛出异常


@dataclass
class RepairResult:
    total_input: int = 0
    total_output: int = 0
    total_valid: int = 0
    total_fixed: int = 0
    total_dropped: int = 0
    details: list[str] = field(default_factory=list)


def repair_features(
    features: list[dict],
    strategy: RepairStrategy = RepairStrategy.AUTO_FIX,
) -> RepairResult:
    """使用 shapely 修复 GeoJSON 要素。"""
    result = RepairResult(total_input=len(features))

    if not features:
        return result

    try:
        from shapely.geometry import shape, mapping
        from shapely.validation import make_valid, explain_validity
    except ImportError:
        result.total_output = len(features)
        result.total_valid = len(features)
        result.details.append("shapely 未安装，跳过几何修复")
        return result

    if strategy == RepairStrategy.RAISE:
        for i, feat in enumerate(features):
            geom = shape(feat.get('geometry', {}))
            if not geom.is_valid:
                raise ValueError(f"要素 {i} 几何无效: {explain_validity(geom)}")

    valid_features = []

    for i, feat in enumerate(features):
        geom_data = feat.get('geometry')
        if not geom_data:
            if strategy != RepairStrategy.DROP:
                valid_features.append(feat)
            result.total_dropped += 1
            continue

        try:
            geom = shape(geom_data)
        except Exception:
            result.total_dropped += 1
            result.details.append(f"要素 {i}: 无法解析几何")
            continue

        if geom.is_valid and geom.is_simple:
            result.total_valid += 1
            valid_features.append(feat)
            continue

        # 几何无效 — 尝试修复
        if strategy in (RepairStrategy.AUTO_FIX, RepairStrategy.ISOLATE):
            try:
                # buffer(0) 是最常见的修复方法
                fixed = geom.buffer(0)
                if fixed.is_valid and not fixed.is_empty:
                    result.total_fixed += 1
                    result.total_valid += 1
                    fixed_feat = dict(feat)
                    fixed_feat['geometry'] = mapping(fixed)
                    # 修复多边形环方向（GeoJSON 要求右手规则）
                    fixed_feat['geometry'] = _fix_ring_orientation(fixed_feat['geometry'])
                    valid_features.append(fixed_feat)
                    continue
            except Exception:
                pass

            # 尝试 make_valid（shapely >= 1.8）
            try:
                fixed = make_valid(geom)
                if fixed.is_valid and not fixed.is_empty:
                    result.total_fixed += 1
                    result.total_valid += 1
                    fixed_feat = dict(feat)
                    fixed_feat['geometry'] = mapping(fixed)
                    valid_features.append(fixed_feat)
                    continue
            except Exception:
                pass

        if strategy == RepairStrategy.DROP:
            result.total_dropped += 1
            result.details.append(f"要素 {i}: 无效几何已丢弃")
        else:
            result.total_dropped += 1
            result.details.append(f"要素 {i}: 无法修复，保留原始")
            valid_features.append(feat)

    result.total_output = len(valid_features)

    # 原地替换
    features.clear()
    features.extend(valid_features)

    return result


def compare_feature_counts(before: int, after: int) -> dict:
    if before == after:
        return {"passed": True, "message": f"要素数量一致: {before}"}
    lost = before - after
    return {"passed": False, "message": f"要素数量变化: {before} → {after} (丢失 {lost})"}


def _fix_ring_orientation(geom: dict) -> dict:
    """修正 GeoJSON 多边形环方向为右手定则。"""
    if geom.get('type') == 'Polygon':
        coords = geom['coordinates']
        fixed_coords = []
        for i, ring in enumerate(coords):
            if i == 0:
                fixed_coords.append(_ensure_ccw(list(ring)))
            else:
                fixed_coords.append(_ensure_cw(list(ring)))
        geom['coordinates'] = fixed_coords
    elif geom.get('type') == 'MultiPolygon':
        geom['coordinates'] = [[_fix_ring_in_poly(p)] for p in geom['coordinates']]
    return geom


def _fix_ring_in_poly(coords_list: list) -> list:
    fixed = []
    for i, ring in enumerate(coords_list):
        if i == 0:
            fixed.append(_ensure_ccw(list(ring)))
        else:
            fixed.append(_ensure_cw(list(ring)))
    return fixed


def _ensure_ccw(ring: list) -> list:
    """确保外环为逆时针。"""
    if _ring_is_clockwise(ring):
        ring.reverse()
    return ring


def _ensure_cw(ring: list) -> list:
    """确保内环为顺时针。"""
    if not _ring_is_clockwise(ring):
        ring.reverse()
    return ring


def _ring_is_clockwise(ring: list) -> bool:
    """用 Shoelace 公式判断环方向。"""
    area = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        area += (x2 - x1) * (y2 + y1)
    return area > 0
