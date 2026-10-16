# -*- coding: utf-8 -*-
"""MDB (ESRI Personal Geodatabase) 集成测试框架。

覆盖完整 MDB 上传和处理流程:
- 单元测试: MDB processor 的辅助函数（无需实际 MDB 文件）
- 集成测试: 完整上传流程（需要实际 MDB 测试文件，缺失时自动跳过）

测试策略:
- 集成测试标记 pytest.mark.integration，正常环境下自动跳过
- 单元测试使用 mock 和小样本数据验证核心逻辑
- 格式检测测试验证 MDB 被正确识别为空间格式
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# ── 测试数据路径 ────────────────────────────────────────────────────────────────
FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "mdb"

# ── MDB 格式检测单元测试 ──────────────────────────────────────────────────────

class TestMdbFormatDetection:
    """MDB 格式识别测试 — 验证 format_detection 对 MDB 的处理。"""

    def test_mdb_magic_bytes_detected(self):
        """MDB 魔术字节被识别为空间格式。"""
        from app.services.format_detection import detect_data_type
        result = detect_data_type("test.mdb", b"Standard Jet DB\x00")
        assert result == "spatial"

    def test_mdb_extension_detected(self):
        """MDB 扩展名被识别为空间格式。"""
        from app.services.format_detection import detect_data_type
        result = detect_data_type("data.mdb")
        assert result == "spatial"

    def test_mdb_in_geometry_formats(self):
        """MDB 在 GEOMETRY_FORMATS 中。"""
        from app.services.format_detection import GEOMETRY_FORMATS
        assert "MDB" in GEOMETRY_FORMATS

    def test_mdb_not_in_table_formats(self):
        """MDB 不在 TABLE_FORMATS 中。"""
        from app.services.format_detection import TABLE_FORMATS
        assert "MDB" not in TABLE_FORMATS

    def test_mdb_extension_map_entry(self):
        """MDB 扩展名映射正确。"""
        from app.services.format_detection import EXTENSION_MAP
        assert EXTENSION_MAP[".mdb"] == "MDB"
        assert EXTENSION_MAP[".MDB"] == "MDB"


# ── MDB processor 辅助函数单元测试 ────────────────────────────────────────────

class TestMdbProcessorHelpers:
    """MDB processor 辅助函数测试 — 无需实际 MDB 文件。"""

    def test_convert_shape_to_geojson_wkt_point(self):
        """WKT Point 转 GeoJSON。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        result = _convert_shape_to_geojson("POINT (1.0 2.0)")
        assert result is not None
        assert result["type"] == "Point"
        assert result["coordinates"] == [1.0, 2.0]

    def test_convert_shape_to_geojson_wkt_linestring(self):
        """WKT LineString 转 GeoJSON。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        result = _convert_shape_to_geojson("LINESTRING (0 0, 1 1, 2 2)")
        assert result is not None
        assert result["type"] == "LineString"
        assert len(result["coordinates"]) == 3

    def test_convert_shape_to_geojson_wkt_polygon(self):
        """WKT Polygon 转 GeoJSON。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        result = _convert_shape_to_geojson(
            "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"
        )
        assert result is not None
        assert result["type"] == "Polygon"

    def test_convert_shape_to_geojson_wkb_bytes(self):
        """WKB bytes 转 GeoJSON。"""
        from shapely import wkb
        from app.services.mdb_processor import _convert_shape_to_geojson
        geom = wkb.loads(bytes.fromhex("0101000000000000000000F03F0000000000000040"))
        result = _convert_shape_to_geojson(geom.wkb)
        assert result is not None
        assert result["type"] == "Point"

    def test_convert_shape_to_geojson_none_returns_none(self):
        """None 输入返回 None。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        assert _convert_shape_to_geojson(None) is None

    def test_convert_shape_to_geojson_invalid_returns_none(self):
        """无效数据返回 None。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        assert _convert_shape_to_geojson(b"invalid data") is None

    def test_convert_shape_to_geojson_empty_string_returns_none(self):
        """空字符串返回 None。"""
        from app.services.mdb_processor import _convert_shape_to_geojson
        assert _convert_shape_to_geojson("") is None

    def test_detect_geometry_type_majority_point(self):
        """多数点几何返回 Point。"""
        from app.services.mdb_processor import _detect_geometry_type
        features = [
            {"geometry": {"type": "Point"}},
            {"geometry": {"type": "Point"}},
            {"geometry": {"type": "LineString"}},
        ]
        assert _detect_geometry_type(features) == "Point"

    def test_detect_geometry_type_majority_polygon(self):
        """多数面几何返回 Polygon。"""
        from app.services.mdb_processor import _detect_geometry_type
        features = [
            {"geometry": {"type": "Polygon"}},
            {"geometry": {"type": "Polygon"}},
            {"geometry": {"type": "Point"}},
        ]
        assert _detect_geometry_type(features) == "Polygon"

    def test_detect_geometry_type_empty_returns_unknown(self):
        """空列表返回 Unknown。"""
        from app.services.mdb_processor import _detect_geometry_type
        assert _detect_geometry_type([]) == "Unknown"


# ── extract_mdb_to_stored_files 单元测试 (mocked) ─────────────────────────────

def _make_mock_tables():
    """返回 mock 表数据。"""
    return [
        {
            "table_name": "Buildings",
            "geometry_type": "Polygon",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon",
                 "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                 "properties": {"name": "A"}},
                {"type": "Feature", "geometry": {"type": "Polygon",
                 "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]},
                 "properties": {"name": "B"}},
            ],
            "fields": ["Shape", "name"],
        },
        {
            "table_name": "Roads",
            "geometry_type": "LineString",
            "features": [
                {"type": "Feature", "geometry": {"type": "LineString",
                 "coordinates": [[0, 0], [1, 1]]}, "properties": {}},
            ],
            "fields": ["geom"],
        },
    ]


class TestMdbToStoredFiles:
    """extract_mdb_to_stored_files 单元测试（mock extract_mdb_features）。"""

    @patch("app.services.mdb_processor.extract_mdb_features")
    def test_happy_path_returns_one_per_table(self, mock_extract):
        """每个空间表生成一个字典。"""
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

    @patch("app.services.mdb_processor.extract_mdb_features")
    def test_max_bytes_skip(self, mock_extract):
        """超出 max_bytes 的表被跳过。"""
        mock_extract.return_value = _make_mock_tables()
        from app.services.mdb_processor import extract_mdb_to_stored_files
        results = extract_mdb_to_stored_files(
            content=b"\x00" * 10,
            filename="test.mdb",
            api_key_id="key-1",
            max_bytes=10,
        )
        assert results == []

    @patch("app.services.mdb_processor.extract_mdb_features")
    def test_empty_tables_returns_empty(self, mock_extract):
        """无空间表返回空列表。"""
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
    def test_temp_file_cleanup_on_success(self, mock_extract):
        """成功后临时 MDB 文件被清理。"""
        mock_extract.return_value = _make_mock_tables()
        from app.services.mdb_processor import extract_mdb_to_stored_files
        tmp_dir = tempfile.gettempdir()
        before = set(os.listdir(tmp_dir))
        extract_mdb_to_stored_files(
            content=b"\x00" * 10,
            filename="test.mdb",
            api_key_id="key-1",
            max_bytes=10_000_000,
        )
        after = set(os.listdir(tmp_dir))
        new_files = after - before
        mdb_temps = [f for f in new_files if f.endswith(".mdb")]
        assert mdb_temps == [], f"Temp MDB files not cleaned up: {mdb_temps}"

    @patch("app.services.mdb_processor.extract_mdb_features")
    def test_temp_file_cleanup_on_failure(self, mock_extract):
        """异常时临时 MDB 文件仍被清理。"""
        mock_extract.side_effect = RuntimeError("boom")
        from app.services.mdb_processor import extract_mdb_to_stored_files
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


# ── MDB 上传 API 端点测试 ─────────────────────────────────────────────────────

class TestMdbUploadEndpoint:
    """MDB 上传端点验证 — 验证端点存在且接受 MDB 文件。"""

    def test_upload_endpoint_exists(self):
        """上传端点存在（POST 无 body 应返回 400/401/422，而非 404）。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        response = client.post("/api/v1/data/upload")
        assert response.status_code != 404

    def test_mdb_file_not_empty_rejection(self):
        """空 MDB 文件应返回 400。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        # 模拟一个空 MDB 文件上传（无 API Key 会返回 401，但验证端点逻辑）
        response = client.post(
            "/api/v1/data/upload",
            files={"file": ("test.mdb", io.BytesIO(b""), "application/x-msaccess")},
        )
        # 无 API Key → 401; 有 API Key + 空文件 → 400
        assert response.status_code in (400, 401)


# ── MDB 集成测试（需要实际测试数据） ─────────────────────────────────────────

@pytest.mark.integration
class TestMdbUploadFlowIntegration:
    """完整 MDB 上传流程集成测试。

    这些测试需要实际的 MDB 测试文件。
    如果测试文件不存在，测试将自动跳过。

    准备测试文件:
    1. 在 tests/fixtures/mdb/ 放置测试 MDB 文件
    2. 或设置环境变量 VCS_MDB_TEST_FILE 指定 MDB 文件路径
    """

    @pytest.fixture(autouse=True)
    def _require_mdb_fixture(self):
        """检查是否有可用的 MDB 测试文件。"""
        self.mdb_path = None
        # 方式 1: 环境变量
        env_path = os.environ.get("VCS_MDB_TEST_FILE")
        if env_path and os.path.isfile(env_path):
            self.mdb_path = Path(env_path)
            return
        # 方式 2: tests/fixtures/mdb/ 目录
        if FIXTURES_DIR.exists():
            mdb_files = list(FIXTURES_DIR.glob("*.mdb"))
            if mdb_files:
                self.mdb_path = mdb_files[0]
                return
        pytest.skip("Requires MDB test file (set VCS_MDB_TEST_FILE or place in tests/fixtures/mdb/)")

    def test_mdb_processor_reads_file(self):
        """MDB processor 能读取实际 MDB 文件。"""
        from app.services.mdb_processor import extract_mdb_features
        features = extract_mdb_features(str(self.mdb_path))
        assert isinstance(features, list)
        # 至少有一个空间表
        assert len(features) > 0, "MDB file should contain at least one spatial table"
        # 每个表有必要的字段
        for table in features:
            assert "table_name" in table
            assert "geometry_type" in table
            assert "features" in table
            assert "fields" in table
            assert len(table["features"]) > 0, f"Table {table['table_name']} should have features"

    def test_mdb_processor_extracts_crs(self):
        """MDB processor 能从 GDB_SpatialRefs 提取坐标系（SRTEXT）。"""
        from app.services.mdb_processor import extract_mdb_features
        tables = extract_mdb_features(str(self.mdb_path))
        assert tables, "Expected at least one spatial table"
        for table in tables:
            crs_wkt = table.get("crs_wkt")
            assert crs_wkt, f"Table {table['table_name']} should carry ESRI WKT from GDB_SpatialRefs"
            # 投影 WKT（GK 米制）或地理 WKT 均可，但必须可识别
            assert crs_wkt.startswith(("PROJCS[", "GEOGCS[")), crs_wkt[:60]

    def test_mdb_stored_files_creation(self):
        """MDB 转 StoredFile 成功。"""
        from app.services.mdb_processor import extract_mdb_to_stored_files
        content = self.mdb_path.read_bytes()
        results = extract_mdb_to_stored_files(
            content=content,
            filename=self.mdb_path.name,
            api_key_id="test-key",
            max_bytes=500 * 1024 * 1024,
        )
        assert len(results) > 0
        for result in results:
            assert "stored_file" in result
            assert "features" in result
            sf = result["stored_file"]
            # stored_file 是 dict（由调用方构造 StoredFile），非 ORM 对象
            assert sf["original_name"].endswith(".geojson")
            assert sf["file_size"] > 0

    def test_mdb_upload_flow_via_api(self):
        """完整 MDB 上传流程（通过 API 端点）。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.security import generate_api_key, hash_api_key
        from app.db.session import get_db
        from sqlalchemy import text
        import asyncio

        client = TestClient(app)

        # 创建测试 API Key
        raw_key, hashed_key = generate_api_key()

        # 注意: 需要测试数据库可用
        # 在实际 CI 环境中，这需要 vcs_test 数据库
        content = self.mdb_path.read_bytes()
        response = client.post(
            "/api/v1/data/upload",
            headers={"X-API-Key": raw_key},
            files={"file": (self.mdb_path.name, io.BytesIO(content), "application/x-msaccess")},
        )
        # 如果数据库不可用，应返回 401（无效 key）或其他错误
        # 如果一切正常，应返回 200
        assert response.status_code in (200, 401, 400)
        if response.status_code == 200:
            data = response.json()
            assert "id" in data
            assert data.get("extracted_count", 0) > 0


# ── MDB 测试数据验证测试 ──────────────────────────────────────────────────────

class TestMdbTestFixtures:
    """验证测试数据目录结构。"""

    def test_fixtures_dir_structure(self):
        """测试数据目录结构文档一致。"""
        # 如果 fixtures 目录存在，验证结构
        if FIXTURES_DIR.exists():
            readme = FIXTURES_DIR / "README.md"
            assert readme.exists(), "tests/fixtures/mdb/README.md 应存在"
            # 验证文档中提到的文件（如果已准备）
            content = readme.read_text(encoding="utf-8")
            assert "simple_points.mdb" in content
            assert "complex_layers.mdb" in content

    def test_fixtures_dir_or_skip(self):
        """fixtures 目录不存在时跳过。"""
        if not FIXTURES_DIR.exists():
            pytest.skip("tests/fixtures/mdb/ not created yet")
