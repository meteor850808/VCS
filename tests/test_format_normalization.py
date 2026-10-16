# -*- coding: utf-8 -*-
"""normalize_stored_geometry_type 归一化函数单元测试（v2.10.2 GDB 元数据修复）。

覆盖:
- fiona schema 维度声明前缀/后缀剥离（"3D MultiPolygon" → "Polygon"）
- Multi* 复合类型归一到父类（"MultiPolygon" → "Polygon"）
- 3D 复合类型归一到 "3d"（MultiPatch/PolyhedralSurface/TIN）
- 空值/边界
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.format_detection import normalize_stored_geometry_type


def test_strip_3d_prefix_gdb_schema():
    """GDB 最常见的脏值：fiona schema 报告的维度前缀。"""
    assert normalize_stored_geometry_type("3D MultiPolygon") == "Polygon"
    assert normalize_stored_geometry_type("3D Polygon") == "Polygon"
    assert normalize_stored_geometry_type("3D LineString") == "LineString"
    assert normalize_stored_geometry_type("3D Point") == "Point"
    assert normalize_stored_geometry_type("3D MultiLineString") == "LineString"


def test_strip_dim_suffix():
    """带维度后缀的值（Z/ZM/M 等）。"""
    assert normalize_stored_geometry_type("Polygon Z") == "Polygon"
    assert normalize_stored_geometry_type("Polygon ZM") == "Polygon"
    assert normalize_stored_geometry_type("Polygon M") == "Polygon"
    assert normalize_stored_geometry_type("LineString Z") == "LineString"
    assert normalize_stored_geometry_type("Point Z") == "Point"


def test_combo_prefix_and_suffix():
    """前缀 + 后缀同时出现。"""
    assert normalize_stored_geometry_type("3D MultiLineString ZM") == "LineString"
    assert normalize_stored_geometry_type("3D MultiPolygon Z") == "Polygon"


def test_multi_normalize_to_parent():
    """未归一化的 Multi* 复合类型归一到父类。"""
    assert normalize_stored_geometry_type("MultiPolygon") == "Polygon"
    assert normalize_stored_geometry_type("MultiLineString") == "LineString"
    assert normalize_stored_geometry_type("MultiPoint") == "Point"


def test_3d_composite_types():
    """3D 复合类型归一到 3d。"""
    assert normalize_stored_geometry_type("MultiPatch") == "3d"
    assert normalize_stored_geometry_type("PolyhedralSurface") == "3d"
    assert normalize_stored_geometry_type("TIN") == "3d"
    assert normalize_stored_geometry_type("3d") == "3d"
    assert normalize_stored_geometry_type("3D") == "3d"
    # 纯 "2D" 声明无形状信息 → None（不产生误导标签）
    assert normalize_stored_geometry_type("2D") is None


def test_already_normalized_values_unchanged():
    """已归一化的值保持不变。"""
    assert normalize_stored_geometry_type("Point") == "Point"
    assert normalize_stored_geometry_type("LineString") == "LineString"
    assert normalize_stored_geometry_type("Polygon") == "Polygon"
    assert normalize_stored_geometry_type("table") == "table"


def test_empty_and_none():
    """空值/None 返回 None。"""
    assert normalize_stored_geometry_type(None) is None
    assert normalize_stored_geometry_type("") is None
    assert normalize_stored_geometry_type("   ") is None


def test_geometry_collection():
    """GeometryCollection 归一到 Polygon（与 _GEOM_NORMALIZE 一致）。"""
    assert normalize_stored_geometry_type("GeometryCollection") == "Polygon"
