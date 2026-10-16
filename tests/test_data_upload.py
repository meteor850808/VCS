# -*- coding: utf-8 -*-
"""app/api/v1/data.py upload endpoint tests — P2b 覆盖上传解析块。

覆盖策略：
- CSV/XLSX 路径：纯 Python（csv/openpyxl），无需 mock fiona
- GeoJSON 路径：mock spatial_loader.extract_features_from_bytes + write_features_batch
- 压缩包路径：zipfile 解压后走 CSV 检测
- 错误路径：空文件、去重
- auto_create_standard / layer_group_id 关联

依赖 conftest.py 的 test_db / client / api_key_header 夹具。
"""

import io
import json

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.models.stored_file import StoredFile
from app.models.api_key import ApiKey


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _create_key(db_session, name):
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.flush()
    return raw, key


@pytest_asyncio.fixture(autouse=True)
def _disable_rate_limit():
    """禁用 slowapi 限速器，避免测试间 429 冲突。

    slowapi 中间件检查 limiter.enabled 属性，置 False 即可跳过所有限速。
    """
    from app.main import app as _app
    limiter = _app.state.limiter
    old_enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = old_enabled


# ── CSV 上传（纯 Python 路径，不依赖 fiona） ──────────────────

class TestUploadCSV:
    """CSV 上传走 _populate_table_schema（纯 csv 模块）。"""

    async def test_upload_csv_creates_record(self, client, api_key_header, db_session):
        csv_content = b"name,value\ntest_a,1\ntest_b,2\n"
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["original_name"] == "test.csv"
        assert data["data_type"] == "table"
        assert data["file_format"] == "csv"
        # 验证 DB 记录
        sf = await db_session.get(StoredFile, data["id"])
        assert sf is not None
        assert sf.file_hash  # hash 应被填充

    async def test_upload_csv_with_description(self, client, api_key_header, db_session):
        csv_content = b"id,name\n1,alice\n"
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("people.csv", io.BytesIO(csv_content), "text/csv")},
            data={"description": "联系人列表"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # description 存入 DB 但不返回在 response 中，验证 DB
        sf = await db_session.get(StoredFile, data["id"])
        assert sf.description == "联系人列表"


# ── GeoJSON 上传（mock spatial_loader） ──────────────────────

class TestUploadGeoJSON:
    """GeoJSON 上传走 _detect_and_populate + _load_all_features_to_spatial_async。"""

    async def test_upload_geojson_with_mocked_loader(self, client, api_key_header, db_session):
        geojson = json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
                "properties": {"name": "Shanghai"},
            }],
        }).encode("utf-8")

        fake_features = [{
            "geometry_wkt": "POINT (121.47 31.23)",
            "properties": {"name": "Shanghai"},
            "geometry_type": "Point",
        }]

        with patch("app.api.v1.data.extract_features_from_bytes", new_callable=AsyncMock, return_value=fake_features) as mock_extract, \
             patch("app.api.v1.data.write_features_batch", new_callable=AsyncMock, return_value={"written": 1, "geometry_type": "Point"}) as mock_write:
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("test.geojson", io.BytesIO(geojson), "application/geo+json")},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["original_name"] == "test.geojson"
            assert data["feature_count"] == 1
            mock_extract.assert_called_once()
            mock_write.assert_called_once()


# ── 压缩包上传 ─────────────────────────────────────────────

class TestUploadArchive:
    """ZIP 上传走 extract_archive → 分类 → 逐文件处理。"""

    async def test_upload_empty_zip(self, client, api_key_header):
        """空 ZIP → 提取 0 个文件，仍创建记录。"""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            pass  # 空 zip
        buf.seek(0)

        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("empty.zip", buf, "application/zip")},
        )
        # 空 zip 应成功（提取 0 文件，archive_record 仍创建）
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_type"] == "archive"

    async def test_upload_zip_with_csv_inside(self, client, api_key_header):
        """ZIP 包含 CSV → 解压后走 CSV 检测路径。"""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("data.csv", "col1,col2\na,b\n")
        buf.seek(0)

        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("archive.zip", buf, "application/zip")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_type"] == "archive"
        assert data["extracted_count"] >= 1


# ── 错误路径 ──────────────────────────────────────────────────

class TestUploadErrors:
    """上传参数校验错误路径。"""

    async def test_upload_empty_file(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        )
        assert resp.status_code == 400
        assert "空" in resp.json()["detail"]

    async def test_upload_dedup_returns_existing(self, client, api_key_header, db_session):
        """同哈希文件二次上传 → 秒传返回已有记录。"""
        csv_content = b"x,y\n1,2\n"
        # 第一次上传
        resp1 = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("dedup_test.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        # 第二次上传同内容（不同文件名，同内容 → 同 hash）
        resp2 = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("dedup_test2.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp2.status_code == 201
        id2 = resp2.json()["id"]
        # 去重：应返回同一记录 ID
        assert id1 == id2, f"Expected dedup, got {id1} vs {id2}"


# ── auto_create_standard ─────────────────────────────────────

class TestUploadAutoCreateStandard:
    """上传时 auto_create_standard=True → 自动创建标准+层组+表。"""

    async def test_auto_create_standard_from_csv(self, client, api_key_header, db_session):
        csv_content = b"id,name,val\n1,road,10\n2,building,20\n"
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("auto_std.csv", io.BytesIO(csv_content), "text/csv")},
            data={
                "auto_create_standard": "true",
                "standard_name": "自动标准",
                "layer_group_name": "道路层",
                "table_name": "road_table",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["standard_id"] is not None
        assert data["data_standard_name"] == "自动标准"


# ── layer_group_id 关联 ──────────────────────────────────────

class TestUploadLinkLayerGroup:
    """上传时指定 layer_group_id + standard_id → 关联已有标准。"""

    async def test_link_to_existing_layer_group(self, client, api_key_header, db_session):
        from app.models.data_standard import DataStandard
        from app.models.standard_layer_group import StandardLayerGroup
        from app.models.standard_table import StandardTable
        import uuid
        from sqlalchemy import select

        # 获取 api_key ORM 对象
        from app.core.security import hash_api_key
        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        # 创建标准+层组+表（standard_id NOT NULL）
        std_id = str(uuid.uuid4())
        lg_id = str(uuid.uuid4())
        std = DataStandard(id=std_id, api_key_id=api_key_obj.id, name="关联标准", category="national")
        db_session.add(std)
        lg = StandardLayerGroup(id=lg_id, standard_id=std_id, group_name="测试层组", group_code="TLG")
        db_session.add(lg)
        tbl = StandardTable(
            id=str(uuid.uuid4()), layer_id=lg_id, standard_id=std_id,
            table_name="测试表", table_code="TTB", geometry_type="Point",
        )
        db_session.add(tbl)
        await db_session.commit()  # commit so upload endpoint's session can see

        geojson = json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}],
        }).encode()

        with patch("app.api.v1.data.extract_features_from_bytes", new_callable=AsyncMock, return_value=[
            {"geometry_wkt": "POINT (1 2)", "properties": {}, "geometry_type": "Point"}
        ]), patch("app.api.v1.data.write_features_batch", new_callable=AsyncMock, return_value={"written": 1, "geometry_type": "Point"}):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("link_test.geojson", io.BytesIO(geojson), "application/geo+json")},
                data={"standard_id": std_id, "layer_group_id": lg_id},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["standard_id"] == std_id
            assert data["standard_table_id"] == str(tbl.id)
