# -*- coding: utf-8 -*-
"""app/api/v1/data.py 路由级回归测试（补路由级覆盖：13% → 30%+ 目标）。

覆盖策略：以 tests/conftest.py 的异步 client + api_key_header + db_session 夹具，
先在 vcs_test 库直接造 StoredFile 记录（不依赖 GDAL 上传解析），再驱动
list/stats/detail/features/structure/download/patch/batch-classify/delete 等端点。

说明：
- 上传链路（/data/upload、/check、/chunk、/merge）依赖沙箱特征解析（本地 GDAL 缺失
  历史文件 all-failed），故本轮用"直接造元数据"方式覆盖查询/CRUD 分支；
  上传分支由 tests/test_upload_mdb.py 冒烟存在性。
- 删除类端点经 _delete_file_recursive → drop_partition（DETACH/DROP），
  drop_partition 对不存在分区用 to_regclass 判空，安全。
"""

import pytest
import pytest_asyncio

from app.models.stored_file import StoredFile
from app.models.api_key import ApiKey


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    """与 test_api.py 相同的测试库 autouse 包装。"""
    yield


async def _create_key(db_session, name):
    """快速建测试 Key，返回其 id 与 ORM 对象。"""
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.flush()
    return raw, key


@pytest.fixture
def file_row():
    """构造一个确定性的 StoredFile 参数（供 seed 使用）。"""
    return dict(
        layer_name="道路线",
        content_type="application/geo+json",
        file_size=2048,
        file_data=b"{}",
        description="测试文件",
        data_type="geometry",
        geometry_type="LineString",
        file_format="geojson",
        detected_crs="EPSG:4326",
        detected_crs_srid=4326,
        crs_is_custom=False,
        layer_count=1,
        feature_count=3,
        point_count=0,
        line_count=3,
        polygon_count=0,
        field_schema=[{"name": "name", "field_type": "string"}],
    )


class TestDataListFiles:
    """GET /api/v1/data 分页查询 + 过滤。"""

    async def test_list_files_empty(self, client, api_key_header):
        resp = await client.get("/api/v1/data", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_files_with_rows_and_filter(
        self, client, db_session, file_row
    ):
        raw, key = await _create_key(db_session, "data_list_a")
        for i in range(3):
            db_session.add(StoredFile(
                original_name=f"路网_{i}.geojson",
                api_key_id=key.id,
                is_archive=False,
                **file_row,
            ))
        await db_session.commit()
        headers = {"X-API-Key": raw}

        # 全量
        resp = await client.get("/api/v1/data", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3

        # 按格式过滤
        resp = await client.get(
            "/api/v1/data?file_format=geojson", headers=headers
        )
        assert resp.json()["total"] == 3

        # 按几何子类型过滤（计数驱动，line_count>0）
        resp = await client.get(
            "/api/v1/data?geometry_type=LineString", headers=headers
        )
        assert resp.json()["total"] == 3

        # 搜索文件名
        resp = await client.get("/api/v1/data?search=路网_1", headers=headers)
        assert resp.json()["total"] == 1

        # 分页
        resp = await client.get(
            "/api/v1/data?page=1&page_size=2", headers=headers
        )
        assert len(resp.json()["items"]) == 2


class TestDataStats:
    """GET /api/v1/data/stats 文件统计。"""

    async def test_stats_counts(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_stats_a")
        db_session.add(StoredFile(
            original_name="s.geojson", api_key_id=key.id, **file_row,
        ))
        # 压缩包记录应被排除（is_archive=True）
        db_session.add(StoredFile(
            original_name="s.zip", api_key_id=key.id, is_archive=True,
            data_type="archive", file_format="zip",
        ))
        await db_session.commit()
        resp = await client.get(
            "/api/v1/data/stats", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 1          # archive 被排除
        assert data["by_format"]["geojson"] == 1
        assert data["by_data_type"]["geometry"] == 1
        assert data["by_geometry_type"]["LineString"] == 1
        assert data["feature_totals"]["line"] == 3


class TestDataDetail:
    """GET /api/v1/data/{file_id} + features + structure + download。"""

    async def test_detail_found_and_404(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_detail_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000001",
            original_name="房屋.shp", api_key_id=key.id, **file_row,
        )
        db_session.add(sf)
        await db_session.commit()
        headers = {"X-API-Key": raw}

        # 命中
        resp = await client.get(f"/api/v1/data/{sf.id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["original_name"] == "房屋.shp"
        assert body["data_type"] == "geometry"

        # 未命中 → 404
        resp = await client.get(
            "/api/v1/data/d9000000-0000-0000-0000-00000000dead",
            headers=headers,
        )
        assert resp.status_code == 404

    async def test_features_empty(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_feat_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000002",
            original_name="点.geojson", api_key_id=key.id,
            data_type="geometry", geometry_type="Point", file_format="geojson",
            feature_count=0, file_data=None,
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sf.id}/features", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert body["features"] == []

    async def test_structure_history_fallback(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_struct_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000003",
            original_name="面.gpkg", api_key_id=key.id,
            data_type="geometry", geometry_type="Polygon", file_format="gpkg",
            feature_count=2,
            field_schema=[{"name": "area", "field_type": "float"}],
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{sf.id}/structure", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_id"] == sf.id
        assert len(body["layers"]) == 1
        assert body["layers"][0]["fields"] == [
            {"name": "area", "field_type": "float", "nullable": True, "sample": None}
        ]

    async def test_download_with_data_and_410(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_dl_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000004",
            original_name="数据.geojson", api_key_id=key.id,
            file_data=b'{"type":"FeatureCollection"}', file_size=34,
            content_type="application/geo+json",
            **{k: v for k, v in file_row.items() if k not in ("file_data", "file_size", "content_type")},
        )
        db_session.add(sf)
        await db_session.commit()
        headers = {"X-API-Key": raw}

        resp = await client.get(f"/api/v1/data/{sf.id}/download", headers=headers)
        assert resp.status_code == 200
        assert resp.content == b'{"type":"FeatureCollection"}'
        assert "filename*=UTF-8" in resp.headers.get("content-disposition", "")

        # 无 file_data → 410
        empty = StoredFile(
            id="d9000000-0000-0000-0000-000000000005",
            original_name="无源.geojson", api_key_id=key.id,
            file_data=None,
            **{k: v for k, v in file_row.items() if k not in ("file_data", "file_size", "content_type")},
        )
        db_session.add(empty)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data/{empty.id}/download", headers=headers
        )
        assert resp.status_code == 410


class TestDataUpdatePatch:
    """PATCH /api/v1/data/{file_id} 元数据更新。"""

    async def test_patch_description(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_patch_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000006",
            original_name="a.geojson", api_key_id=key.id, **file_row,
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.patch(
            f"/api/v1/data/{sf.id}",
            headers={"X-API-Key": raw},
            json={"description": "新描述"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "新描述"

    async def test_patch_404(self, client, db_session, api_key_header):
        resp = await client.patch(
            "/api/v1/data/nonexistent-id",
            headers=api_key_header,
            json={"description": "x"},
        )
        assert resp.status_code == 404


class TestDataClassifyAndDelete:
    """batch-classify + delete + batch-delete + cleanup。"""

    async def test_batch_classify_empty(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/batch-classify",
            headers=api_key_header,
            json={"file_ids": [], "standard_id": None},
        )
        assert resp.status_code == 400

    async def test_batch_classify(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_cls_a")
        ids = []
        for i in range(2):
            sf = StoredFile(
                id=f"d9000000-0000-0000-0000-0000000000{i}7",
                original_name=f"c{i}.geojson", api_key_id=key.id, **file_row,
            )
            db_session.add(sf)
            ids.append(sf.id)
        await db_session.commit()
        resp = await client.post(
            "/api/v1/data/batch-classify",
            headers={"X-API-Key": raw},
            json={"file_ids": ids, "standard_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

    async def test_delete_filefound(self, client, db_session, file_row):
        raw, key = await _create_key(db_session, "data_del_a")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000009",
            original_name="del.geojson", api_key_id=key.id, **file_row,
        )
        db_session.add(sf)
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/data/{sf.id}", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 204

    async def test_batch_delete_empty(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/data/batch-delete",
            headers=api_key_header,
            json=[],
        )
        assert resp.status_code == 400

    async def test_cleanup_archives(self, client, db_session):
        raw, key = await _create_key(db_session, "data_clean_a")
        await db_session.commit()
        # 无过期记录也应为 200
        resp = await client.post(
            "/api/v1/data/cleanup-archives?days=30",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200


class TestDataScopeIsolation:
    """不同 Key 之间文件互不可见。"""

    async def test_other_key_cannot_see(self, client, db_session, file_row):
        raw_a, key_a = await _create_key(db_session, "data_scope_aw")
        raw_b, key_b = await _create_key(db_session, "data_scope_bw")
        sf = StoredFile(
            id="d9000000-0000-0000-0000-000000000010",
            original_name="secret.geojson", api_key_id=key_a.id, **file_row,
        )
        db_session.add(sf)
        await db_session.commit()
        # B 看不到 A 的文件
        resp = await client.get(
            f"/api/v1/data/{sf.id}", headers={"X-API-Key": raw_b}
        )
        assert resp.status_code == 404
        # A 自己能看到
        resp = await client.get(
            f"/api/v1/data/{sf.id}", headers={"X-API-Key": raw_a}
        )
        assert resp.status_code == 200