"""API 集成测试 + 服务单元测试（使用 httpx AsyncClient + PostgreSQL 测试库）。

注意：模型含 PostgreSQL 专属类型（JSONB / PostGIS Geometry / LIST 分区表），
SQLite 无法建表（JSONB 编译失败），故测试使用独立测试库 vcs_test，
不污染生产库 vcs_db。运行前需先建库并启用 PostGIS：
    scripts/create_test_db.py

共享基础设施（test_db/engine/api_key_header/client）见 tests/conftest.py：
- override_get_db 不再于模块顶层设置（消除对全局 app 的 import 副作用），
  由 session 级 test_db fixture 在 yield 前后设置/清除。
"""

import io
import json
import pytest
import pytest_asyncio

from app.main import app
from app.core.security import generate_api_key, hash_api_key, verify_api_key
from app.models.api_key import ApiKey
from app.services.format_detection import detect_format
from app.services.quality_checker import check_quality
from app.services.crs_transform import resolve_crs, get_supported_crs_list
from app.services.script_generation import _safe_where_to_js
from app.services.attribute_processing import _ensure_critical_fields

# Celery 任务同步执行（不依赖 Redis/Worker）
from app.workers.celery_app import celery_app
celery_app.conf.update(task_always_eager=True)


# ── Fixtures（定义见 tests/conftest.py） ───────────────────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    """本模块全部用例依赖 session 级 test_db（建表 → yield → 删表 + 清 override）。"""
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# API 集成测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealth:
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")


class TestAuth:
    async def test_missing_api_key(self, client):
        resp = await client.get("/api/v1/jobs")
        assert resp.status_code == 401

    async def test_invalid_api_key(self, client):
        resp = await client.get("/api/v1/jobs", headers={"X-API-Key": "invalid_key"})
        assert resp.status_code == 401

    async def test_valid_api_key(self, client, api_key_header):
        resp = await client.get("/api/v1/capabilities", headers=api_key_header)
        assert resp.status_code == 200


class TestAdmin:
    async def test_create_api_key(self, client, admin_init_header):
        resp = await client.post(
            "/api/v1/admin/apikeys",
            headers=admin_init_header,
            json={"name": "new_key", "description": "test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new_key"
        assert data["api_key"].startswith("vcs_")

    async def test_create_api_key_unauthorized(self, client):
        resp = await client.post(
            "/api/v1/admin/apikeys",
            json={"name": "hacker_key"},
        )
        assert resp.status_code == 403

    async def test_list_api_keys(self, client, admin_init_header):
        resp = await client.get("/api/v1/admin/apikeys", headers=admin_init_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1


class TestConversion:
    SAMPLE_GEOJSON = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {"name": "北京", "YWBH": "BJ001", "FEATURECODE": "POI"},
        }]
    }

    async def test_capabilities(self, client, api_key_header):
        resp = await client.get("/api/v1/capabilities", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "GeoJSON" in data["output_formats"]
        assert "SHP" in data["input_formats"]

    async def test_submit_conversion(self, client, api_key_header):
        content = json.dumps(self.SAMPLE_GEOJSON).encode()
        resp = await client.post(
            "/api/v1/convert",
            headers=api_key_header,
            data={"dst_crs": "EPSG:4490", "output_format": "GeoJSON"},
            files={"file": ("test.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    async def test_reject_dangerous_filename(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/convert",
            headers=api_key_header,
            data={"dst_crs": "EPSG:4490"},
            files={"file": ("../../etc/passwd", io.BytesIO(b"{}"), "application/json")},
        )
        assert resp.status_code == 400

    async def test_reject_exe_upload(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/convert",
            headers=api_key_header,
            data={"dst_crs": "EPSG:4490"},
            files={"file": ("virus.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
        )
        assert resp.status_code == 400

    async def test_list_jobs(self, client, api_key_header):
        resp = await client.get("/api/v1/jobs", headers=api_key_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_batch_delete_terminal_jobs(self, client, db_session):
        """批量硬删除仅允许终态任务：success/failed/cancelled 可删，running 拒绝。"""
        from app.models.api_key import ApiKey
        from app.models.conversion_job import ConversionJob, JobStatus

        # 创建独立 Key + 三种状态的任务（不经过 /convert，保证状态可控）
        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="del_test_key", is_active=True)
        db_session.add(key)
        await db_session.flush()
        jobs = [
            ConversionJob(original_filename="ok.geojson", status=JobStatus.SUCCESS,
                          dst_crs="EPSG:4490", output_format="GeoJSON", api_key_id=key.id,
                          input_path="", output_path=""),
            ConversionJob(original_filename="fail.geojson", status=JobStatus.FAILED,
                          dst_crs="EPSG:4490", output_format="GeoJSON", api_key_id=key.id,
                          input_path="", output_path=""),
            ConversionJob(original_filename="run.geojson", status=JobStatus.RUNNING,
                          dst_crs="EPSG:4490", output_format="GeoJSON", api_key_id=key.id,
                          input_path="", output_path=""),
        ]
        db_session.add_all(jobs)
        await db_session.commit()

        by_name = {j.original_filename: j.id for j in jobs}
        head = {"X-API-Key": raw}
        # 含 running：running 被拒、success/failed 被删 → 200，deleted_count=2，results 含 1 条失败
        resp = await client.post(
            "/api/v1/jobs/delete", headers=head,
            json={"job_ids": list(by_name.values())},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 2
        by_id = {r["id"]: r for r in data["results"]}
        assert by_id[by_name["run.geojson"]]["success"] is False
        assert by_id[by_name["ok.geojson"]]["success"] is True
        assert by_id[by_name["fail.geojson"]]["success"] is True

        # 终态任务已从列表消失，running 仍在
        resp = await client.get("/api/v1/jobs", headers=head)
        remaining = [j["original_filename"] for j in resp.json()]
        assert "run.geojson" in remaining
        assert "ok.geojson" not in remaining
        assert "fail.geojson" not in remaining

    async def test_detect_format(self, client, api_key_header):
        content = json.dumps(self.SAMPLE_GEOJSON).encode()
        resp = await client.post(
            "/api/v1/detect",
            headers=api_key_header,
            files={"file": ("test.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format_name"] == "GeoJSON"
        assert data["confidence"] >= 0.5


class TestQuality:
    SAMPLE_GEOJSON_COMPLETE = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {"name": "北京", "YWBH": "BJ001", "FEATURECODE": "POI"},
        }, {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [121.5, 31.2]},
            "properties": {"name": "上海", "YWBH": "SH001", "FEATURECODE": "POI"},
        }]
    }

    SAMPLE_GEOJSON_INCOMPLETE = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {"name": "北京", "YWBH": None, "FEATURECODE": None},
        }]
    }

    async def test_quality_check(self, client, api_key_header):
        content = json.dumps(self.SAMPLE_GEOJSON_COMPLETE).encode()
        resp = await client.post(
            "/api/v1/quality/check/upload",
            headers=api_key_header,
            files={"file": ("test.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completeness_score"] >= 0.5
        assert data["detected_format"] == "GeoJSON"
        assert data["total_features"] == 2

    async def test_quality_check_incomplete(self, client, api_key_header):
        content = json.dumps(self.SAMPLE_GEOJSON_INCOMPLETE).encode()
        resp = await client.post(
            "/api/v1/quality/check/upload",
            headers=api_key_header,
            files={"file": ("test.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completeness_score"] < 1.0

    async def test_list_quality_reports(self, client, api_key_header):
        resp = await client.get("/api/v1/quality", headers=api_key_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# 安全单元测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurity:
    def test_hash_api_key(self):
        raw, hashed = generate_api_key()
        computed = hash_api_key(raw)
        assert computed == hashed
        assert verify_api_key(raw, hashed) is True
        assert verify_api_key("wrong_key", hashed) is False

    def test_where_safe_simple(self):
        assert _safe_where_to_js("name = 'test'") == '(props.get("name") == \'test\')'

    def test_where_safe_comparison(self):
        result = _safe_where_to_js("age > 18")
        assert result is not None
        assert ">" in result

    def test_where_safe_and(self):
        result = _safe_where_to_js("city = 'BJ' and status = 1")
        assert result is not None
        assert "and" in result.lower()

    def test_where_safe_in(self):
        result = _safe_where_to_js("code in (1, 2, 3)")
        assert result is not None
        assert "in" in result.lower()

    def test_where_reject_call(self):
        """拒绝函数调用注入。"""
        assert _safe_where_to_js("__import__('os').system('rm')") is None

    def test_where_reject_attr_access(self):
        """拒绝属性访问注入。"""
        assert _safe_where_to_js("().__class__.__bases__") is None

    def test_where_reject_subscript(self):
        """拒绝下标访问注入。"""
        assert _safe_where_to_js("a[0] = 1") is None

    def test_critical_fields_preserved(self):
        fields = _ensure_critical_fields(["name", "code"])
        for f in ["YWBH", "FEATURECODE"]:
            assert f in fields


# ═══════════════════════════════════════════════════════════════════════════════
# 服务层单元测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatDetection:
    def test_detect_geojson_from_content(self, tmp_path):
        f = tmp_path / "test.geojson"
        f.write_text('{"type":"FeatureCollection","features":[]}', encoding='utf-8')
        result = detect_format(str(f))
        assert result.format_name == "GeoJSON"
        assert result.confidence >= 0.5

    def test_detect_unknown_format(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("some random data", encoding='utf-8')
        result = detect_format(str(f))
        assert result.confidence < 0.5

    def test_file_not_exists(self):
        result = detect_format("/nonexistent/file.shp")
        assert result.confidence == 0.0


class TestCRSTransform:
    def test_resolve_epsg(self):
        crs = resolve_crs("EPSG:4326")
        assert crs.epsg == 4326

    def test_resolve_chinese_alias(self):
        crs = resolve_crs("国家2000")
        assert crs.epsg == 4490

        crs = resolve_crs("北京54")
        assert crs.epsg == 4214

        crs = resolve_crs("西安80")
        assert crs.epsg == 4610

    def test_resolve_none(self):
        crs = resolve_crs("")
        assert crs.epsg == 4326

    def test_supported_crs(self):
        crs_list = get_supported_crs_list()
        assert "EPSG:4326" in crs_list
        assert "EPSG:4490" in crs_list
        assert "EPSG:3857" in crs_list


class TestQualityChecker:
    def test_check_quality_complete(self, tmp_path):
        f = tmp_path / "test.geojson"
        f.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116, 40]},
                "properties": {"YWBH": "001", "FEATURECODE": "A"},
            }]
        }), encoding='utf-8')
        result = check_quality(str(f))
        assert result.total_features >= 1
        assert result.completeness_score > 0

    def test_check_quality_missing_critical(self, tmp_path):
        f = tmp_path / "test.geojson"
        f.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116, 40]},
                "properties": {"name": "test"},
            }]
        }), encoding='utf-8')
        result = check_quality(str(f))
        # YWBH and FEATURECODE should be flagged as missing
        for key in ["YWBH", "FEATURECODE"]:
            status = result.critical_fields_status.get(key, {})
            assert not status.get("present", True), f"{key} should be reported as missing"


class TestStandardV2Stats:
    """数据标准 v2 统计端点（任务看板汇总）：scope 隔离 + 各类计数。"""

    async def test_standard_v2_stats(self, client, db_session):
        from app.models.api_key import ApiKey
        from app.models.data_standard import DataStandard
        from app.models.standard_layer_group import StandardLayerGroup
        from app.models.standard_table import StandardTable
        from app.models.standard_field import StandardField
        from app.models.standard_dictionary import StandardDictionary
        from app.models.dictionary_item import DictionaryItem
        from app.models.source_data import StandardVersion

        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="stats_test_key", is_active=True)
        db_session.add(key)
        await db_session.flush()

        # 标准（custom / draft）+ 层组 + 表 + 字段 + 版本 + 标准级字典 + 全局字典
        std = DataStandard(
            id="11111111-1111-1111-1111-111111111111",
            api_key_id=key.id, name="统计测试标准", code="ST1",
            category="custom", status="draft",
        )
        db_session.add(std)
        layer = StandardLayerGroup(id="22222222-2222-2222-2222-222222222222",
                                   standard_id=std.id, group_name="根图层")
        db_session.add(layer)
        table = StandardTable(id="33333333-3333-3333-3333-333333333333",
                              standard_id=std.id, layer_id=layer.id,
                              table_name="房屋面", table_type="spatial",
                              geometry_type="Polygon")
        db_session.add(table)
        db_session.add(StandardField(id="44444444-4444-4444-4444-444444444444",
                                     standard_id=std.id, table_id=table.id,
                                     field_name="楼层", data_type="int"))
        db_session.add(StandardDictionary(id="55555555-5555-5555-5555-555555555555",
                                          standard_id=std.id, scope="standard",
                                          dict_name="标准字典", dict_code="SD"))
        db_session.add(StandardDictionary(id="66666666-6666-6666-6666-666666666666",
                                          standard_id=None, scope="global",
                                          dict_name="全局字典", dict_code="GD"))
        db_session.add(DictionaryItem(id="77777777-7777-7777-7777-777777777777",
                                      dict_id="55555555-5555-5555-5555-555555555555",
                                      item_code="A", item_name="甲"))
        db_session.add(DictionaryItem(id="88888888-8888-8888-8888-888888888888",
                                      dict_id="66666666-6666-6666-6666-666666666666",
                                      item_code="B", item_name="乙"))
        db_session.add(StandardVersion(id="99999999-9999-9999-9999-999999999999",
                                       standard_id=std.id, version_number=1))
        await db_session.commit()

        resp = await client.get("/api/v1/data-standards-v2/stats",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        data = resp.json()
        assert data["standards_total"] == 1
        assert data["by_category"] == {"custom": 1}
        assert data["by_status"] == {"draft": 1}
        assert data["layers_total"] == 1
        assert data["tables_total"] == 1
        assert data["fields_total"] == 1
        assert data["standard_dicts"] == 1
        assert data["global_dicts"] == 1
        assert data["dict_items_total"] == 2
        assert data["versions_total"] == 1

    async def test_standard_v2_stats_scope_isolated(self, client, db_session):
        """其他 Key 的标准不计入本 Key 的统计。"""
        from app.models.api_key import ApiKey
        from app.models.data_standard import DataStandard

        rawa, hasha = generate_api_key()
        keya = ApiKey(key_hash=hasha, name="stats_scope_a", is_active=True)
        db_session.add(keya)
        await db_session.flush()
        db_session.add(DataStandard(id="aaaaaaaa-0000-0000-0000-000000000001",
                                    api_key_id=keya.id, name="别的Key的标准",
                                    category="industry", status="published"))
        await db_session.commit()

        rawb, hashb = generate_api_key()
        keyb = ApiKey(key_hash=hashb, name="stats_scope_b", is_active=True)
        db_session.add(keyb)
        await db_session.flush()
        await db_session.commit()

        resp = await client.get("/api/v1/data-standards-v2/stats",
                                headers={"X-API-Key": rawb})
        assert resp.status_code == 200
        data = resp.json()
        assert data["standards_total"] == 0
        assert data["by_category"] == {}
        assert data["by_status"] == {}
        assert data["layers_total"] == 0
        assert data["tables_total"] == 0
