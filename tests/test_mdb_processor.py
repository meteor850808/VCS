# -*- coding: utf-8 -*-
"""MDB processor unit tests."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_mdb_processor_import():
    """Test that mdb_processor module can be imported."""
    from app.services.mdb_processor import extract_mdb_features, extract_mdb_to_stored_files
    assert callable(extract_mdb_features)
    assert callable(extract_mdb_to_stored_files)


def test_mdb_processor_extract_nonexistent_file():
    """extract_mdb_features raises on nonexistent file."""
    from app.services.mdb_processor import extract_mdb_features
    import pyodbc
    import pytest
    with pytest.raises(pyodbc.Error):
        extract_mdb_features("/tmp/nonexistent.mdb")


def test_convert_shape_to_geojson_none():
    """_convert_shape_to_geojson returns None for invalid data."""
    from app.services.mdb_processor import _convert_shape_to_geojson
    assert _convert_shape_to_geojson(None) is None
    assert _convert_shape_to_geojson(b"invalid") is None


def test_convert_shape_to_geojson_wkt():
    """_convert_shape_to_geojson converts WKT string to GeoJSON."""
    from app.services.mdb_processor import _convert_shape_to_geojson
    result = _convert_shape_to_geojson("POINT (1.0 2.0)")
    assert result is not None
    assert result["type"] == "Point"
    assert result["coordinates"] == [1.0, 2.0]


def test_convert_shape_to_geojson_wkb():
    """_convert_shape_to_geojson converts WKB bytes to GeoJSON."""
    from shapely import wkb
    from app.services.mdb_processor import _convert_shape_to_geojson
    geom = wkb.loads(bytes.fromhex("0101000000000000000000F03F0000000000000040"))
    result = _convert_shape_to_geojson(geom.wkb)
    assert result is not None
    assert result["type"] == "Point"
    assert result["coordinates"] == [1.0, 2.0]


def test_detect_geometry_type():
    """_detect_geometry_type returns most common geometry type."""
    from app.services.mdb_processor import _detect_geometry_type
    features = [
        {"geometry": {"type": "Point"}},
        {"geometry": {"type": "Point"}},
        {"geometry": {"type": "LineString"}},
    ]
    assert _detect_geometry_type(features) == "Point"


def test_detect_geometry_type_empty():
    """_detect_geometry_type returns 'Unknown' for empty list."""
    from app.services.mdb_processor import _detect_geometry_type
    assert _detect_geometry_type([]) == "Unknown"


def _make_mock_tables():
    """Helper: return mock table data for extract_mdb_to_stored_files tests."""
    return [
        {
            "table_name": "Buildings",
            "geometry_type": "Polygon",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}, "properties": {"name": "A"}},
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]}, "properties": {"name": "B"}},
            ],
            "fields": ["Shape", "name"],
        },
        {
            "table_name": "Roads",
            "geometry_type": "LineString",
            "features": [
                {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "properties": {}},
            ],
            "fields": ["geom"],
        },
    ]


@patch("app.services.mdb_processor.extract_mdb_features")
def test_extract_mdb_to_stored_files_happy_path(mock_extract):
    """extract_mdb_to_stored_files returns one dict per table when under max_bytes."""
    mock_extract.return_value = _make_mock_tables()

    from app.services.mdb_processor import extract_mdb_to_stored_files
    results = extract_mdb_to_stored_files(
        content=b"\x00" * 10,
        filename="test.mdb",
        api_key_id="key-1",
        max_bytes=10_000_000,
    )

    assert len(results) == 2
    # Verify stored_file is a dict (not StoredFile object)
    assert isinstance(results[0]["stored_file"], dict)
    assert results[0]["stored_file"]["api_key_id"] == "key-1"
    assert results[0]["stored_file"]["source_archive"] == "test.mdb"
    assert results[0]["stored_file"]["data_type"] == "spatial"
    assert results[0]["stored_file"]["file_format"] == "GeoJSON"
    assert "file_hash" in results[0]["stored_file"]
    assert results[0]["features"] == _make_mock_tables()[0]["features"]
    assert results[1]["features"] == _make_mock_tables()[1]["features"]
    assert mock_extract.call_count == 1


@patch("app.services.mdb_processor.extract_mdb_features")
def test_extract_mdb_to_stored_files_max_bytes_skip(mock_extract):
    """extract_mdb_to_stored_files skips tables whose GeoJSON exceeds max_bytes."""
    tables = _make_mock_tables()
    mock_extract.return_value = tables

    from app.services.mdb_processor import extract_mdb_to_stored_files
    results = extract_mdb_to_stored_files(
        content=b"\x00" * 10,
        filename="test.mdb",
        api_key_id="key-1",
        max_bytes=10,  # very small limit
    )

    assert results == []
    assert mock_extract.call_count == 1


@patch("app.services.mdb_processor.extract_mdb_features")
def test_extract_mdb_to_stored_files_empty_tables(mock_extract):
    """extract_mdb_to_stored_files returns [] when no tables have features."""
    mock_extract.return_value = []

    from app.services.mdb_processor import extract_mdb_to_stored_files
    results = extract_mdb_to_stored_files(
        content=b"\x00" * 10,
        filename="test.mdb",
        api_key_id="key-1",
        max_bytes=10_000_000,
    )

    assert results == []


@patch("app.services.mdb_processor.extract_mdb_features")
def test_extract_mdb_to_stored_files_temp_file_cleanup(mock_extract):
    """extract_mdb_to_stored_files cleans up temp file even on extract failure."""
    import tempfile
    import os

    mock_extract.side_effect = RuntimeError("boom")

    from app.services.mdb_processor import extract_mdb_to_stored_files

    # Capture the temp path that was created
    tmp_dir = tempfile.gettempdir()
    before = set(os.listdir(tmp_dir))

    try:
        extract_mdb_to_stored_files(
            content=b"\x00" * 10,
            filename="test.mdb",
            api_key_id="key-1",
            max_bytes=10_000_000,
        )
    except RuntimeError:
        pass

    after = set(os.listdir(tmp_dir))
    new_files = after - before
    mdb_temps = [f for f in new_files if f.endswith(".mdb")]
    assert mdb_temps == [], f"Temp MDB files not cleaned up: {mdb_temps}"
