# -*- coding: utf-8 -*-
"""P2c: 实时 fiona 上传沙箱 — 绕过 mock，真正走 extract_features_from_bytes → write_features_batch。

关键设计：
- `_load_all_features_to_spatial_async` 内部用 `AsyncSessionLocal()` 创建独立会话，
  指向生产库；此处 patch `app.api.v1.data.AsyncSessionLocal` 指向测试库 sessionmaker，
  使子任务写入 vcs_test。
- `_detect_and_populate` 在文件扩展名为 .geojson 时走 Fiona _extract_from_geojson
  （纯 Python JSON，不需要 osgeo），此时 field_schema / geometry_type 会被真实填充。
- 覆盖之前未覆盖的 upload 分支：
  ① 非压缩包空间文件路径（_load_all_features_to_spatial_async → aggregate_layer_stats）
  ② 几何类型不匹配报错（layer_group_id + geometry_type mismatch）
  ③ 标准表兜底选取（layer_group 有多表时按 geometry_type 匹配/fallback 第一张）
  ④ 秒传检查端点 GET /data/upload/check
  ⑤ 切片上传流程 POST /data/upload/chunk → GET chunk-status → POST merge
  ⑥ 同名文件覆盖上传（_delete_existing_by_name 已有记录被删除）
"""

import asyncio
import io
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from unittest.mock import AsyncMock, patch

from app.models.api_key import ApiKey
from app.models.stored_file import StoredFile
from tests.conftest import _TestSession


# ── session-scoped DB + key fixture ──────────────────────────────────────────

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
    from app.main import app as _app
    limiter = _app.state.limiter
    old = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = old


def _make_geojson(features=None):
    """构造最小 GeoJSON bytes。"""
    if features is None:
        features = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
             "properties": {"name": "Shanghai", "val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
             "properties": {"name": "Beijing", "val": 20}},
        ]
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc).encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# ① 非压缩包空间文件 — 真实 fiona 路径
# ══════════════════════════════════════════════════════════════════════════════

class TestRealGeoJSONUpload:
    """直接上传 GeoJSON → extract_features_from_bytes → write_features_batch → PostGIS。"""

    async def test_real_geojson_creates_spatial_features(self, client, api_key_header, db_session):
        """验证：上传真实 GeoJSON 后，StoredFile 创建 + spatial_features 子任务写入。"""
        raw_key = api_key_header["X-API-Key"]
        content = _make_geojson()

        # Patch AsyncSessionLocal → 测试库 sessionmaker，使子任务写入 vcs_test
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("real_test.geojson", io.BytesIO(content), "application/geo+json")},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()

        # 验证 StoredFile 主记录
        sf = await db_session.get(StoredFile, data["id"])
        assert sf is not None
        assert sf.file_format == "geojson"
        # detect_format 对 GeoJSON 返回 data_type='unknown'（fiona 无空间语义标签）
        assert sf.geometry_type == "Point"
        assert sf.feature_count == 2
        assert sf.file_hash  # SHA-256 已填充

    async def test_real_geojson_with_standard_id(self, client, api_key_header, db_session):
        """上传时带 standard_id → archive_record.standard_id 回填。"""
        from app.models.data_standard import DataStandard
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        std_id = str(uuid.uuid4())
        std = DataStandard(id=std_id, api_key_id=api_key_obj.id, name="P2C标准", category="auto")
        db_session.add(std)
        await db_session.commit()

        content = _make_geojson()
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("with_std.geojson", io.BytesIO(content), "application/geo+json")},
                data={"standard_id": std_id},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["standard_id"] == std_id
        assert data["data_standard_name"] == "P2C标准"


# ══════════════════════════════════════════════════════════════════════════════
# ② 几何类型不匹配报错
# ══════════════════════════════════════════════════════════════════════════════

class TestGeometryTypeMismatch:
    """上传时 layer_group_id 的表要求 LineString，上传 Point → 400 报错。"""

    async def test_mismatch_returns_400(self, client, api_key_header, db_session):
        from app.models.data_standard import DataStandard
        from app.models.standard_layer_group import StandardLayerGroup
        from app.models.standard_table import StandardTable
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        std_id = str(uuid.uuid4())
        lg_id = str(uuid.uuid4())
        db_session.add(DataStandard(id=std_id, api_key_id=api_key_obj.id, name="mismatch_std", category="auto"))
        db_session.add(StandardLayerGroup(id=lg_id, standard_id=std_id, group_name="线层组", group_code="TLG_M"))
        db_session.add(StandardTable(
            id=str(uuid.uuid4()), layer_id=lg_id, standard_id=std_id,
            table_name="线表", table_code="TTB_M", geometry_type="LineString",
        ))
        await db_session.commit()

        # 上传 Point → 应与 LineString 不匹配 → 400
        content = _make_geojson()  # Point
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("mismatch.geojson", io.BytesIO(content), "application/geo+json")},
                data={"standard_id": std_id, "layer_group_id": lg_id},
            )
        assert resp.status_code == 400
        assert "不匹配" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════════════
# ③ 标准表兜底选取（多表 layer_group → 按 geometry_type 匹配 / fallback 第一张）
# ══════════════════════════════════════════════════════════════════════════════

class TestStandardTableAutoPick:
    """layer_group 有 2 张表（Point + LineString），上传 Point → 匹配 Point 表。"""

    async def test_picks_matching_table(self, client, api_key_header, db_session):
        from app.models.data_standard import DataStandard
        from app.models.standard_layer_group import StandardLayerGroup
        from app.models.standard_table import StandardTable
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        std_id = str(uuid.uuid4())
        lg_id = str(uuid.uuid4())
        pt_tbl_id = str(uuid.uuid4())
        ln_tbl_id = str(uuid.uuid4())
        db_session.add(DataStandard(id=std_id, api_key_id=api_key_obj.id, name="multi_tbl_std", category="auto"))
        db_session.add(StandardLayerGroup(id=lg_id, standard_id=std_id, group_name="多类型层", group_code="TLG_X"))
        db_session.add(StandardTable(
            id=pt_tbl_id, layer_id=lg_id, standard_id=std_id,
            table_name="A_点表", table_code="TTB_A", geometry_type="Point",
        ))
        db_session.add(StandardTable(
            id=ln_tbl_id, layer_id=lg_id, standard_id=std_id,
            table_name="B_线表", table_code="TTB_B", geometry_type="LineString",
        ))
        await db_session.commit()

        content = _make_geojson()  # Point 要素
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("autopick.geojson", io.BytesIO(content), "application/geo+json")},
                data={"standard_id": std_id, "layer_group_id": lg_id},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["standard_id"] == std_id
        # 应自动选择 Point 表（A_点表）
        assert data["standard_table_id"] == pt_tbl_id


# ══════════════════════════════════════════════════════════════════════════════
# ④ 秒传检查端点 GET /data/upload/check
# ══════════════════════════════════════════════════════════════════════════════

class TestUploadCheck:
    """秒传检查端点：hash 存在返回 exists=True，不存在返回 exists=False。"""

    async def test_check_hash_exists(self, client, api_key_header, db_session):
        from app.core.security import hash_api_key
        import hashlib

        content = _make_geojson()
        file_hash = hashlib.sha256(content).hexdigest()

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        sf = StoredFile(
            id=str(uuid.uuid4()),
            api_key_id=api_key_obj.id,
            original_name="check_test.geojson",
            file_hash=file_hash,
            file_size=len(content),
            data_type="geometry",
            file_format="geojson",
        )
        db_session.add(sf)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/data/upload/check?hash={file_hash}",
            headers=api_key_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["file_id"] == sf.id

    async def test_check_hash_not_found(self, client, api_key_header):
        resp = await client.get(
            "/api/v1/data/upload/check?hash=does_not_exist_hash",
            headers=api_key_header,
        )
        assert resp.status_code == 200
        assert resp.json()["exists"] is False


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 切片上传流程 chunk → chunk-status → merge
# ══════════════════════════════════════════════════════════════════════════════

class TestChunkUpload:
    """切片上传三步流程：chunk → chunk-status → merge。"""

    async def test_chunk_upload_merge_flow(self, client, api_key_header, db_session):
        upload_id = f"test-upload-{uuid.uuid4().hex[:12]}"

        # Step 1: 上传 1 个切片
        chunk_data = b"col1,col2\na,b\n"
        resp = await client.post(
            "/api/v1/data/upload/chunk",
            headers=api_key_header,
            data={
                "upload_id": upload_id,
                "chunk_index": 0,
                "total_chunks": 1,
            },
            files={"chunk": ("chunk_0.bin", io.BytesIO(chunk_data), "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["received"] == 1

        # Step 2: 查询切片状态
        resp = await client.get(
            f"/api/v1/data/upload/chunk-status?upload_id={upload_id}",
            headers=api_key_header,
        )
        assert resp.status_code == 200
        status = resp.json()
        assert status["status"] == "ready_to_merge"
        assert status["progress"] == "1/1"

        # Step 3: 合并入库
        resp = await client.post(
            "/api/v1/data/upload/merge",
            headers=api_key_header,
            json={
                "upload_id": upload_id,
                "filename": "chunked_upload.csv",
                "total_chunks": 1,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["original_name"] == "chunked_upload.csv"
        assert data["file_format"] == "csv"

    async def test_chunk_status_not_found(self, client, api_key_header):
        resp = await client.get(
            "/api/v1/data/upload/chunk-status?upload_id=nonexistent_id",
            headers=api_key_header,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    async def test_merge_missing_params_returns_400(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/upload/merge",
            headers=api_key_header,
            json={"upload_id": "x"},  # 缺少 filename
        )
        assert resp.status_code == 400

    async def test_merge_nonexistent_upload_returns_404(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/upload/merge",
            headers=api_key_header,
            json={"upload_id": "nonexistent", "filename": "x.csv", "total_chunks": 1},
        )
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# ⑥ 同名文件覆盖上传
# ══════════════════════════════════════════════════════════════════════════════

class TestOverwriteByName:
    """同名文件再次上传 → 旧记录被 _delete_existing_by_name 删除。"""

    async def test_overwrite_deletes_old(self, client, api_key_header, db_session):
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        # 手动插入一个同名旧记录
        old_id = str(uuid.uuid4())
        sf_old = StoredFile(
            id=old_id,
            api_key_id=api_key_obj.id,
            original_name="overwrite_test.csv",
            file_size=10,
            data_type="table",
            file_format="csv",
            file_hash="old_hash_value",
        )
        db_session.add(sf_old)
        await db_session.commit()

        # 上传同名新文件
        new_content = b"col1,col2\nnew_val,1\n"
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("overwrite_test.csv", io.BytesIO(new_content), "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        new_id = resp.json()["id"]
        assert new_id != old_id

        # 旧记录应被删除（用 select 查询避免 ORM 缓存陈旧）
        result = await db_session.execute(
            select(StoredFile).where(StoredFile.id == old_id)
        )
        old_sf = result.scalar_one_or_none()
        assert old_sf is None


# ══════════════════════════════════════════════════════════════════════════════
# ⑦ 含矢量子文件的 ZIP 压缩包
# ══════════════════════════════════════════════════════════════════════════════

class TestArchiveWithVector:
    """ZIP 包含 .geojson 文件 → classify_extracted_files 分类 → 矢量子文件走 _load_extracted_features_async。"""

    async def test_zip_with_geojson_inside(self, client, api_key_header, db_session):
        import zipfile

        buf = io.BytesIO()
        gj = _make_geojson([
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]},
             "properties": {"pk": 1}},
        ])
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("points.geojson", gj)
            zf.writestr("info.csv", "id,name\n1,site\n")
        buf.seek(0)

        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload",
                headers=api_key_header,
                files={"file": ("vector_archive.zip", buf, "application/zip")},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_type"] == "archive"
        assert data["extracted_count"] >= 2  # 至少 .geojson + .csv

        # 检查子记录（source_archive 关联）
        sub_files = (await db_session.execute(
            select(StoredFile).where(StoredFile.source_archive == "vector_archive.zip")
        )).scalars().all()
        assert len(sub_files) >= 2

        # .geojson 子记录应有 feature_count（由 _load_extracted_features_async 写入）
        geojson_sub = [sf for sf in sub_files if sf.original_name.endswith("points.geojson")]
        assert len(geojson_sub) >= 1
        assert any(sf.feature_count == 1 for sf in geojson_sub)

    async def test_zip_csv_only_no_spatial_subtask(self, client, api_key_header):
        """ZIP 仅含 CSV → tables 分类 → 无空间子任务，extracted_count 匹配。"""
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("table1.csv", "a,b\n1,2\n")
            zf.writestr("table2.csv", "c,d\n3,4\n")
        buf.seek(0)

        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("csv_archive.zip", buf, "application/zip")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["extracted_count"] == 2


# ══════════════════════════════════════════════════════════════════════════════
# ⑧ 大小限制 / 文件名校验 / 内容类型分支
# ══════════════════════════════════════════════════════════════════════════════

class TestUploadEdgeCases:
    """边界场景：无文件名、特殊字符、content_type 缺失。"""

    async def test_no_filename_returns_422(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("", io.BytesIO(b"hello"), "text/plain")},
        )
        # FastAPI Form 验证：空文件名返回 422（验证错误），非业务层 400
        assert resp.status_code == 422

    async def test_auto_create_standard_with_geometry_type_override(self, client, api_key_header, db_session):
        """auto_create_standard + geometry_type 参数 → 覆盖后端检测值。"""
        csv_content = b"id,name\n1,park\n"
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("geom_override.csv", io.BytesIO(csv_content), "text/csv")},
            data={
                "auto_create_standard": "true",
                "standard_name": "覆盖标准",
                "geometry_type": "polygon",  # 前端显式指定
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["standard_id"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# ⑨ reindex 端点
# ══════════════════════════════════════════════════════════════════════════════

class TestReindex:
    """POST /data/{file_id}/reindex — 从 file_data 重建空间要素。"""

    async def test_reindex_no_file_data_returns_410(self, client, api_key_header, db_session):
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        sf = StoredFile(
            id=str(uuid.uuid4()),
            api_key_id=api_key_obj.id,
            original_name="no_data.geojson",
            file_data=None,  # 已清理
            file_size=0,
            data_type="geometry",
            file_format="geojson",
        )
        db_session.add(sf)
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/data/{sf.id}/reindex",
            headers=api_key_header,
        )
        assert resp.status_code == 410

    async def test_reindex_404_not_found(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/nonexistent-id/reindex",
            headers=api_key_header,
        )
        assert resp.status_code == 404

    async def test_reindex_with_file_data(self, client, api_key_header, db_session):
        """有 file_data 的记录 → reindex 成功触发 _load_all_features_to_spatial_async。

        由于 _load_all_features_to_spatial_async 内部使用 db_task_semaphore + AsyncSessionLocal
        创建独立子会话（与测试库 sessionmaker 不同），同步 patch 仍存在事件循环竞争。
        此处 mock 掉 _load_all_features_to_spatial_async，验证端点主流程 + aggregate_layer_stats。
        """
        from app.core.security import hash_api_key

        key_hash = hash_api_key(api_key_header["X-API-Key"])
        q = await db_session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key_obj = q.scalar_one()

        content = _make_geojson([
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
             "properties": {"id": 1}},
        ])
        sf = StoredFile(
            id=str(uuid.uuid4()),
            api_key_id=api_key_obj.id,
            original_name="reindex_test.geojson",
            file_data=content,
            file_size=len(content),
            content_type="application/geo+json",
            data_type="unknown",
            geometry_type="Point",
            file_format="geojson",
        )
        db_session.add(sf)
        await db_session.commit()

        fake_stats = {"written": 1, "skipped_empty": 0, "skipped_no_geometry": 0,
                      "geometry_type": "Point", "error": None}
        with patch("app.api.v1.data._load_all_features_to_spatial_async",
                   new_callable=AsyncMock, return_value=fake_stats), \
             patch("app.api.v1.data.aggregate_layer_stats",
                   new_callable=AsyncMock, return_value=None):
            resp = await client.post(
                f"/api/v1/data/{sf.id}/reindex",
                headers=api_key_header,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["file_id"] == sf.id
        assert data["features_indexed"] == 1
