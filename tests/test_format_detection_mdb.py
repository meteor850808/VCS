# -*- coding: utf-8 -*-
"""MDB (ESRI Personal Geodatabase) 格式分类测试。

验证 MDB 被归类为空间格式而非表格格式。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.format_detection import detect_data_type, TABLE_FORMATS, GEOMETRY_FORMATS


def test_mdb_not_in_table_formats():
    """MDB 不应该在 TABLE_FORMATS 中。"""
    assert "MDB" not in TABLE_FORMATS


def test_mdb_in_geometry_formats():
    """MDB 应该在 GEOMETRY_FORMATS 中。"""
    assert "MDB" in GEOMETRY_FORMATS


def test_mdb_data_type_returns_spatial():
    """detect_data_type 对 MDB 应返回 spatial。"""
    result = detect_data_type("test.mdb", b"Standard Jet DB\x00")
    assert result == "spatial"


def test_mdb_data_type_by_extension():
    """仅扩展名 .mdb 也能检测为 spatial。"""
    result = detect_data_type("data.mdb")
    assert result == "spatial"


def test_csv_still_table():
    """CSV 仍应为 table 格式（回归测试）。"""
    result = detect_data_type("data.csv")
    assert result == "table"
    assert "CSV" in TABLE_FORMATS


def test_xlsx_still_table():
    """XLSX 仍应为 table 格式（回归测试）。"""
    result = detect_data_type("data.xlsx")
    assert result == "table"
    assert "XLSX" in TABLE_FORMATS


def test_shp_still_geometry():
    """SHP 仍应为 geometry 格式（回归测试）。"""
    result = detect_data_type("data.shp")
    assert result == "geometry"
    assert "SHP" in GEOMETRY_FORMATS
