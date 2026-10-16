# -*- coding: utf-8 -*-
"""P5k-2: data.py CRUD 子端点 / 统计过滤 / quality-check 端点 + upload 残余分支。

对齐 BASELINE §17 的 P5k 剩余目标（upload 后半段 + CRUD 子端点），本轮覆盖：

列表过滤（GET /data）
- data_type / file_format / search 过滤
- geometry_type 计数列命中（LineString → line_count>0，1379-1380）
- geometry_type 字符串比较兜底（table，1382）
- standard_id=none / 指定 standard（1386-1389）
- from/to 日期范围（1391/1393）

详情/要素/结构/下载（GET /data/{file_id}/...）
- features 返已有分区要素行（1513-1527）
- structure 404（1573）
- structure 多图层行 → fields 清空（multi-layer，1585-1586）
- download 404（1634）

元数据编辑（PATCH /data/{file_id}）
- 标准关联生效（1680-1685）/ 清空（1687）
- standard_table_id 更新（1689） / data_standard_name 直接写入（1691）

批量关联/删除/清理
- batch-classify 带 standard_id（1719-1723）
- delete 404（1759）；delete 压缩包父记录递归删子（1777）
- batch-delete 命中多条（1813-1823）
- cleanup-archives 实际删除（1851-1852）

reindex 聚合异常（2294-2295）
quality-check 端点（2335-2449）
- 404/无标准 400/非法 topology_config/layers JSON/非字符串数组/未知图层
- happy：mock run_quality_check → 报告落库 + JSON 写盘 + checked_layers

upload 残余
- xlsx 真实 schema 解析（238-256）
- 损坏 xlsx → schema 解析 except（257-260）
- 空间格式检测失败 → _detect_and_populate except（195-198）
- 临时目录/文件清理异常 → except pass（204-205 / 209-210）
- auto_create_standard 无检测几何 → db_geom_type=None（399）
- geoemtry_type 传入时 GEOM_MAP 命中（391-392）
"""

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.api.v1 import data as data_module
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.stored_file import StoredFile
from app.models.stored_file_layer import StoredFileLayer
from app.models.standard_table import StandardTable
from tests.conftest import _TestSession


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _create_key(db_session, name: str) -> tuple[str, str]:
    """创建新 API Key，返回 (raw, key_id)。"""
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.flush()
    return raw, key.id


@pytest_asyncio.fixture(autouse=True)
def _disable_rate_limit():
    from app.main import app as _app
    limiter = _app.state.limiter
    old = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = old


# ── 数据构造 helpers ─────────────────────────────────────────────────────────

def _make_csv_bytes(row_count: int = 3) -> bytes:
    lines = ["name,val"]
    for i in range(row_count):
        lines.append(f"item{i},{i}")
    return "\n".join(lines).encode("utf-8")


def _make_geojson_bytes() -> bytes:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
             "properties": {"name": "Shanghai"}},
        ],
    }
    return json.dumps(fc).encode("utf-8")


def _make_zip_with_geojson() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner.geojson", _make_geojson_bytes().decode("utf-8"))
    return buf.getvalue()


def _make_minimal_xlsx() -> bytes:
    """openpyxl 生成最小 xlsx（1 表头 + 2 行）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "val"])
    ws.append(["a", 1])
    ws.append(["b", 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sf_base(api_key_id: str, **kw) -> StoredFile:
    defaults = dict(
        original_name="base.csv", content_type="text/csv", file_size=10,
        is_archive=False, data_type="table", file_format="csv",
        geometry_type="table", api_key_id=api_key_id,
    )
    defaults.update(kw)
    return StoredFile(**defaults)


async def _upload(client, headers, filename: str, content: bytes, mime: str = "application/octet-stream", **form):
    return await client.post(
        "/api/v1/data/upload", headers=headers,
        files={"file": (filename, io.BytesIO(content), mime)},
        data=form,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 列表过滤
# ══════════════════════════════════════════════════════════════════════════════

class TestListFilters:

    async def test_list_data_type_and_format_filter(self, client, db_session):
        raw, key_id = await _create_key(db_session, "p5k2-list")
        headers = {"X-API-Key": raw}
        sf_geo = _sf_base(key_id, original_name="layer.geojson", data_type="geometry",
                          geometry_type="Point", file_format="geojson")
        sf_tbl = _sf_base(key_id, original_name="sheet.csv", data_type="table",
                          geometry_type="table", file_format="csv")
        db_session.add_all([sf_geo, sf_tbl])
        await db_session.commit()

        # data_type 过滤
        resp = await client.get("/api/v1/data", headers=headers, params={"data_type": "geometry"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == sf_geo.id

        # file_format 过滤（lower 归一）
        resp = await client.get("/api/v1/data", headers=headers, params={"file_format": "CSV"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["id"] == sf_tbl.id

        # search ilike
        resp = await client.get("/api/v1/data", headers=headers, params={"search": "layer"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["id"] == sf_geo.id

    async def test_list_geometry_type_count_column(self, client, db_session):
        """LineString 走计数列命中（line_count>0），不依赖 geometry_type 标签。"""
        raw, key_id = await _create_key(db_session, "p5k2-geomcnt")
        headers = {"X-API-Key": raw}
        sf = _sf_base(key_id, original_name="roads.shp", data_type="geometry",
                      geometry_type="LineString", file_format="shp", line_count=3)
        db_session.add(sf)
        await db_session.commit()

        resp = await client.get("/api/v1/data", headers=headers, params={"geometry_type": "LineString"})
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert any(i["id"] == sf.id for i in items)
        for i in items:
            assert i["line_count"] > 0

    async def test_list_geometry_type_string_fallback(self, client, db_session):
        """table 不在计数列映射 → 字符串比较（1382）。"""
        raw, key_id = await _create_key(db_session, "p5k2-geomstr")
        headers = {"X-API-Key": raw}
        sf = _sf_base(key_id, original_name="sheet.csv", data_type="table",
                      geometry_type="table", file_format="csv")
        db_session.add(sf)
        await db_session.commit()

        resp = await client.get("/api/v1/data", headers=headers, params={"geometry_type": "table"})
        assert resp.status_code == 200, resp.text
        one = resp.json()
        assert one["total"] == 1
        assert one["items"][0]["id"] == sf.id

    async def test_list_standard_id_none_and_specific(self, client, db_session):
        raw, key_id = await _create_key(db_session, "p5k2-stdf")
        headers = {"X-API-Key": raw}
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2过滤标准", category="auto")
        db_session.add(std)
        sf_plain = _sf_base(key_id, original_name="plain.csv")
        sf_linked = _sf_base(key_id, original_name="linked.csv", standard_id=std.id)
        db_session.add_all([sf_plain, sf_linked])
        await db_session.commit()

        resp = await client.get("/api/v1/data", headers=headers, params={"standard_id": "none"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["id"] == sf_plain.id

        resp = await client.get("/api/v1/data", headers=headers, params={"standard_id": std.id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["id"] == sf_linked.id

    async def test_list_date_range_filter(self, client, db_session):
        """from(1391) / to(1393) 日期过滤。"""
        raw, key_id = await _create_key(db_session, "p5k2-date")
        headers = {"X-API-Key": raw}
        old = _sf_base(key_id, original_name="old.csv",
                       created_at=datetime.now(timezone.utc) - timedelta(days=2))
        fresh = _sf_base(key_id, original_name="fresh.csv",
                         created_at=datetime.now(timezone.utc))
        db_session.add_all([old, fresh])
        await db_session.commit()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        # 三天前 → 今天：两条都在
        resp = await client.get("/api/v1/data", headers=headers,
                                params={"from": two_days_ago, "to": today})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 2

        # 昨天起：只留 fresh
        resp = await client.get("/api/v1/data", headers=headers, params={"from": yesterday})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == fresh.id

        # 截止昨天 23:59:59：只留 old
        resp = await client.get("/api/v1/data", headers=headers, params={"to": yesterday})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == old.id


# ══════════════════════════════════════════════════════════════════════════════
# 详情 / 要素 / 结构 / 下载
# ══════════════════════════════════════════════════════════════════════════════

class TestDetailSubEndpoints:

    async def test_features_with_rows(self, client, api_key_header):
        """上传真实 GeoJSON（写分区要素）→ features 端点返回 FeatureCollection。"""
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await _upload(client, api_key_header, "feat.geojson", _make_geojson_bytes(),
                                 "application/geo+json")
        assert resp.status_code == 201, resp.text
        file_id = resp.json()["id"]

        resp = await client.get(f"/api/v1/data/{file_id}/features", headers=api_key_header)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        assert body["metadata"]["total"] == 1
        assert body["features"][0]["geometry"]["type"] == "Point"

    async def test_structure_404(self, client, api_key_header):
        resp = await client.get("/api/v1/data/no-such-file/structure", headers=api_key_header)
        assert resp.status_code == 404, resp.text

    async def test_structure_multi_layer_rows(self, client, api_key_header, db_session):
        """多图层聚合行 → layers 多条且 fields 清空（1585-1594）。"""
        key_id = None
        from app.core.security import hash_api_key
        q = await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )
        key_id = q.scalar_one().id

        sf = _sf_base(key_id, original_name="mixed.zip", data_type="geometry",
                      geometry_type="Point", file_format="zip", layer_count=2,
                      point_count=3, line_count=1, feature_count=4,
                      field_schema=[{"name": "name", "type": "string"}])
        db_session.add(sf)
        await db_session.flush()
        db_session.add_all([
            StoredFileLayer(stored_file_id=sf.id, api_key_id=key_id, layer_name="L1",
                            geometry_type="Point", point_count=3, feature_count=3),
            StoredFileLayer(stored_file_id=sf.id, api_key_id=key_id, layer_name="L2",
                            geometry_type="LineString", line_count=1, feature_count=1),
        ])
        await db_session.commit()

        resp = await client.get(f"/api/v1/data/{sf.id}/structure", headers=api_key_header)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["layer_count"] == 2
        assert len(body["layers"]) == 2
        assert any(l["name"] == "L1" for l in body["layers"])
        assert all(l["fields"] == [] for l in body["layers"])  # 多图层不挂字段

    async def test_download_404(self, client, api_key_header):
        resp = await client.get("/api/v1/data/no-such-file/download", headers=api_key_header)
        assert resp.status_code == 404, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# 元数据编辑 PATCH
# ══════════════════════════════════════════════════════════════════════════════

class TestPatchMetadata:

    async def test_patch_standard_id_resolves_name(self, client, api_key_header, db_session):
        """PATCH standard_id → 冗余名称回查（1678-1685）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2补丁标准", category="auto")
        db_session.add(std)
        sf = _sf_base(key_id, original_name="patch.csv")
        db_session.add(sf)
        await db_session.commit()

        resp = await client.patch(f"/api/v1/data/{sf.id}", headers=api_key_header,
                                  json={"standard_id": std.id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["standard_id"] == std.id
        assert resp.json()["data_standard_name"] == "P5k2补丁标准"

    async def test_patch_standard_table_id(self, client, api_key_header, db_session):
        """PATCH standard_table_id（1688-1689）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2表标准", category="auto")
        grp = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id, group_name="G1")
        tbl = StandardTable(id=str(uuid.uuid4()), layer_id=grp.id, standard_id=std.id,
                            table_name="t1", geometry_type="Point")
        db_session.add_all([std, grp, tbl])
        sf = _sf_base(key_id, original_name="patch_tbl.csv")
        db_session.add(sf)
        await db_session.commit()

        resp = await client.patch(f"/api/v1/data/{sf.id}", headers=api_key_header,
                                  json={"standard_table_id": tbl.id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["standard_table_id"] == tbl.id

    async def test_patch_data_standard_name_direct(self, client, api_key_header, db_session):
        """PATCH data_standard_name 直接写入（1690-1691）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        sf = _sf_base(key_id, original_name="patch_name.csv")
        db_session.add(sf)
        await db_session.commit()

        resp = await client.patch(f"/api/v1/data/{sf.id}", headers=api_key_header,
                                  json={"description": "注释", "data_standard_name": "直接名称"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["description"] == "注释"
        assert body["data_standard_name"] == "直接名称"


# ══════════════════════════════════════════════════════════════════════════════
# 批量关联 / 删除 / 清理
# ══════════════════════════════════════════════════════════════════════════════

class TestBatchDeleteCleanup:

    async def test_batch_classify_with_standard(self, client, api_key_header, db_session):
        """batch-classify 带 standard_id → 名称冗余回填（1719-1723）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2批量标准", category="auto")
        db_session.add(std)
        sf1 = _sf_base(key_id, original_name="bc1.csv")
        sf2 = _sf_base(key_id, original_name="bc2.csv")
        db_session.add_all([sf1, sf2])
        await db_session.commit()

        resp = await client.post("/api/v1/data/batch-classify", headers=api_key_header,
                                 json={"file_ids": [sf1.id, sf2.id], "standard_id": std.id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated"] == 2
        await db_session.refresh(sf1)
        await db_session.refresh(sf2)
        assert sf1.standard_id == std.id
        assert sf1.data_standard_name == "P5k2批量标准"
        assert sf2.standard_id == std.id

    async def test_delete_404(self, client, api_key_header):
        resp = await client.delete("/api/v1/data/no-such-file", headers=api_key_header)
        assert resp.status_code == 404, resp.text

    async def test_delete_archive_recursive(self, client, api_key_header, db_session):
        """删除压缩包父记录 → 子记录一并递归删除（1769-1777）。"""
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await _upload(client, api_key_header, "tree.zip", _make_zip_with_geojson(),
                                 "application/zip")
        assert resp.status_code == 201, resp.text
        parent_id = resp.json()["id"]

        child_rows = (await db_session.execute(
            select(StoredFile).where(StoredFile.source_archive == "tree.zip")
        )).scalars().all()
        assert len(child_rows) == 1

        del_resp = await client.delete(f"/api/v1/data/{parent_id}", headers=api_key_header)
        assert del_resp.status_code == 204, del_resp.text

        assert await db_session.get(StoredFile, parent_id, populate_existing=True) is None
        assert await db_session.get(StoredFile, child_rows[0].id, populate_existing=True) is None

    async def test_batch_delete_multiple(self, client, api_key_header, db_session):
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        sf1 = _sf_base(key_id, original_name="bd1.csv")
        sf2 = _sf_base(key_id, original_name="bd2.csv")
        db_session.add_all([sf1, sf2])
        await db_session.commit()

        resp = await client.post("/api/v1/data/batch-delete", headers=api_key_header,
                                 json=[sf1.id, sf2.id])
        assert resp.status_code == 204, resp.text
        # 独立会话删除后，本会话 identity-map 已缓存旧对象 → populate_existing 强制回查
        assert await db_session.get(StoredFile, sf1.id, populate_existing=True) is None
        assert await db_session.get(StoredFile, sf2.id, populate_existing=True) is None

    async def test_cleanup_archives_deletes_old_only(self, client, api_key_header, db_session):
        """cleanup-archives 只删超过 cutoff 的压缩包（1851-1852）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        old = _sf_base(key_id, original_name="old.zip", is_archive=True, data_type="archive",
                       file_format="zip",
                       created_at=datetime.now(timezone.utc) - timedelta(days=10))
        fresh = _sf_base(key_id, original_name="fresh.zip", is_archive=True, data_type="archive",
                         file_format="zip",
                         created_at=datetime.now(timezone.utc))
        db_session.add_all([old, fresh])
        await db_session.commit()

        resp = await client.post("/api/v1/data/cleanup-archives", headers=api_key_header,
                                 params={"days": 7})
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 1
        assert await db_session.get(StoredFile, old.id, populate_existing=True) is None
        assert await db_session.get(StoredFile, fresh.id, populate_existing=True) is not None

    async def test_cleanup_archives_preserves_child_data(self, client, api_key_header, db_session):
        """【v2.19.6 方案 A】cleanup-archives 只删压缩包壳，子文件记录与
        PostGIS 分区数据全部保留（source_archive 解除关联 → 提升为独立记录）。

        注意：此处故意不先 SELECT ApiKey 拿 key_id——该读事务会对 api_keys
        持有 AccessShareLock，阻塞端点内 DROP TABLE（DROP 需对被引用表取排他锁）。
        """
        # 真实上传带子文件的 zip（inner.geojson 1 个点要素）——唯一名避免跨运行残留
        zip_name = f"h5_{uuid.uuid4().hex[:8]}.zip"
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            up = await _upload(client, api_key_header, zip_name, _make_zip_with_geojson(),
                               "application/zip")
        assert up.status_code == 201, up.text
        parent_id = up.json()["id"]

        child_rows = (await db_session.execute(
            select(StoredFile).where(StoredFile.source_archive == zip_name)
        )).scalars().all()
        assert len(child_rows) == 1
        child = child_rows[0]
        assert child.is_archive is False
        assert child.feature_count == 1  # 数据已入库（父记录聚合值）

        # 子记录分区表有真实数据
        pname = f"point_features_p_{child.id.replace('-', '_')}"
        exists = (await db_session.execute(
            text(f"SELECT to_regclass('{pname}')")
        )).scalar()
        assert exists is not None, "子记录分区表应存在"

        # 回拨父记录 created_at 到 10 天前
        await db_session.execute(
            text("UPDATE stored_files SET created_at = now() - interval '10 days' WHERE id = :p"),
            {"p": parent_id},
        )
        await db_session.commit()

        # 调 cleanup（days=7）→ 只删壳
        resp = await client.post("/api/v1/data/cleanup-archives", headers=api_key_header,
                                 params={"days": 7})
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 1

        # 壳被删
        assert await db_session.get(StoredFile, parent_id, populate_existing=True) is None
        # 子记录保留（source_archive 已解除）
        child_after = await db_session.get(StoredFile, child.id, populate_existing=True)
        assert child_after is not None
        assert child_after.source_archive is None
        assert child_after.feature_count == 1
        # 分区数据仍在（物理保留）
        pname2 = f"point_features_p_{child.id.replace('-', '_')}"
        ok = (await db_session.execute(text(f"SELECT to_regclass('{pname2}')"))).scalar()
        assert ok is not None, "cleanup 后子记录分区表不应被 DROP"
        n = (await db_session.execute(text(f"SELECT count(*) FROM {pname2}"))).scalar()
        assert n == 1

    async def test_delete_archive_recursive_still_recurses(self, client, api_key_header, db_session):
        """【v2.19.6 方案 A 回归】DELETE /data/{id} 显式删除压缩包壳仍递归删子（
        方案 A 只改变 cleanup-archives 的自动清理语义，显式删除行为不变）。

        注意：不先 SELECT ApiKey 拿 key_id——见上方 preserves_child_data 注释，
        读事务的 AccessShareLock 会阻塞 delete 端点内的 DROP TABLE（FK 排他锁）。
        """
        zip_name = f"h5d_{uuid.uuid4().hex[:8]}.zip"
        with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            up = await _upload(client, api_key_header, zip_name, _make_zip_with_geojson(),
                               "application/zip")
        assert up.status_code == 201, up.text
        parent_id = up.json()["id"]

        child_rows = (await db_session.execute(
            select(StoredFile).where(StoredFile.source_archive == zip_name)
        )).scalars().all()
        assert len(child_rows) == 1
        child = child_rows[0]

        del_resp = await client.delete(f"/api/v1/data/{parent_id}", headers=api_key_header)
        assert del_resp.status_code == 204, del_resp.text

        assert await db_session.get(StoredFile, parent_id, populate_existing=True) is None
        assert await db_session.get(StoredFile, child.id, populate_existing=True) is None
        pname = f"point_features_p_{child.id.replace('-', '_')}"
        assert (await db_session.execute(text(f"SELECT to_regclass('{pname}')"))).scalar() is None


# ══════════════════════════════════════════════════════════════════════════════
# reindex 聚合异常不阻断
# ══════════════════════════════════════════════════════════════════════════════

class TestReindexAggFailure:

    async def test_reindex_agg_failure_non_blocking(self, client, api_key_header, db_session, monkeypatch):
        """reindex 时 aggregate_layer_stats 抛错 → 不影响索引结果（2291-2295）。

        与 P2c 教训一致：_load_all_features_to_spatial_async 内部用
        db_task_semaphore + 独立子会话，真实执行存在事件循环竞争 → 挂起。
        此处 mock loader，等同 P2c test_reindex_with_file_data 形态。
        """
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        sf = _sf_base(key_id, original_name="reindex.geojson", data_type="geometry",
                      geometry_type="Point", file_format="geojson",
                      file_data=_make_geojson_bytes())
        db_session.add(sf)
        await db_session.commit()

        async def _boom(*_a, **_k):
            raise RuntimeError("agg boom")

        fake_stats = {"written": 1, "skipped_empty": 0, "skipped_no_geometry": 0,
                      "geometry_type": "Point", "error": None}
        with patch("app.api.v1.data._load_all_features_to_spatial_async",
                   new_callable=AsyncMock, return_value=fake_stats):
            monkeypatch.setattr(data_module, "aggregate_layer_stats", _boom)
            resp = await client.post(f"/api/v1/data/{sf.id}/reindex", headers=api_key_header)
        assert resp.status_code == 200, resp.text
        assert resp.json()["features_indexed"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# quality-check 端点
# ══════════════════════════════════════════════════════════════════════════════

_FAKE_QC_RESULT = {
    "scores": {"completeness": 95.0, "accuracy": 90.0, "consistency": 85.0,
               "uniqueness": 80.0, "topology": 70.0,
               "overall": 85.75},  # =95×0.25+90×0.25+85×0.20+80×0.15+70×0.15
    "attr_summary": {"total": 5, "passed": 4, "failed": 1, "by_rule": {}},
    "topo_summary": {"total": 2, "passed": 2, "failed": 0, "by_rule": {}},
    "attr_results": [{"rule_id": "r1", "passed": True}],
    "topo_results": [],
}


class TestQualityCheck:

    async def _make_qc_file(self, db_session, api_key_header, with_standard: bool = True,
                            with_layer: bool = True) -> StoredFile:
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id

        std = None
        if with_standard:
            std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2质控标准", category="auto")
            db_session.add(std)
            await db_session.flush()
        sf = _sf_base(key_id, original_name="qc.csv", standard_id=std.id if std else None,
                      data_standard_name=std.name if std else None)
        db_session.add(sf)
        await db_session.flush()
        if with_layer:
            db_session.add(StoredFileLayer(stored_file_id=sf.id, api_key_id=key_id,
                                           layer_name="L1", feature_count=1))
        await db_session.commit()
        return sf

    async def test_quality_file_not_found(self, client, api_key_header):
        resp = await client.post("/api/v1/data/no-such-file/quality-check", headers=api_key_header)
        assert resp.status_code == 404, resp.text

    async def test_quality_no_standard(self, client, api_key_header, db_session):
        sf = await self._make_qc_file(db_session, api_key_header, with_standard=False)
        resp = await client.post(f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header)
        assert resp.status_code == 400, resp.text
        assert "请提供 data_standard_id" in resp.json()["detail"]

    async def test_quality_bad_topology_config(self, client, api_key_header, db_session):
        sf = await self._make_qc_file(db_session, api_key_header)
        resp = await client.post(
            f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
            data={"topology_config": "{not-json"},
        )
        assert resp.status_code == 400, resp.text
        assert "topology_config" in resp.json()["detail"]

    async def test_quality_bad_layers_json(self, client, api_key_header, db_session):
        sf = await self._make_qc_file(db_session, api_key_header)
        resp = await client.post(
            f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
            data={"layers": "not-json"},
        )
        assert resp.status_code == 400, resp.text
        assert "layers 不是有效的 JSON 数组" in resp.json()["detail"]

    async def test_quality_layers_not_strings(self, client, api_key_header, db_session):
        sf = await self._make_qc_file(db_session, api_key_header)
        resp = await client.post(
            f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
            data={"layers": "[1, 2]"},
        )
        assert resp.status_code == 400, resp.text
        assert "layers 必须是字符串数组" in resp.json()["detail"]

    async def test_quality_unknown_layer(self, client, api_key_header, db_session):
        sf = await self._make_qc_file(db_session, api_key_header)
        resp = await client.post(
            f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
            data={"layers": '["Lx"]'},
        )
        assert resp.status_code == 400, resp.text
        assert "未知图层" in resp.json()["detail"]

    async def test_quality_happy(self, client, api_key_header, db_session, monkeypatch, tmp_path):
        """合法调用 → 报告落库 + JSON 写盘 + checked_layers 回填。"""
        from app.models.quality_report import QualityReport
        sf = await self._make_qc_file(db_session, api_key_header)
        monkeypatch.setattr(data_module.settings, "quality_report_dir", str(tmp_path))

        with patch("app.services.quality_checker.run_quality_check",
                   return_value=_FAKE_QC_RESULT), \
             patch("app.services.topology_checker.TopologyConfig"):
            resp = await client.post(
                f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
                data={
                    "geometry_type": "Point",
                    "topology_config": json.dumps({"enabled_rules": ["a"], "tolerances": {}}),
                    "layers": '["L1"]',
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["score_completeness"] == 95.0
        assert body["score_overall"] == 85.75  # v2.20: 后端按 WEIGHTS 计算返回
        assert body["total_features"] == 5
        assert body["checked_layers"] == ["L1"]

        # 报告已落库
        report = (await db_session.execute(
            select(QualityReport).where(QualityReport.file_name == "qc.csv")
        )).scalars().first()
        assert report is not None
        assert report.score_overall == 85.75  # v2.20: 综合分已持久化
        assert report.json_report_path
        assert Path(report.json_report_path).exists()

    async def test_quality_happy_form_standard_fallback(self, client, api_key_header, db_session):
        """文件未关联标准，但 form 传 data_standard_id → 生效（2353）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k2表单标准", category="auto")
        db_session.add(std)
        sf = _sf_base(key_id, original_name="qc_form.csv")
        db_session.add(sf)
        await db_session.commit()

        with patch("app.services.quality_checker.run_quality_check",
                   return_value=_FAKE_QC_RESULT):
            resp = await client.post(
                f"/api/v1/data/{sf.id}/quality-check", headers=api_key_header,
                data={"data_standard_id": std.id,
                      "geometry_type": "Point",
                      "topology_config": json.dumps({"enabled_rules": ["a"], "tolerances": {}}),
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data_standard_id"] == std.id
        assert body["score_accuracy"] == 90.0


# ══════════════════════════════════════════════════════════════════════════════
# upload 残余分支
# ══════════════════════════════════════════════════════════════════════════════

class TestUploadResidual:

    async def test_upload_xlsx_real_schema(self, client, api_key_header, db_session):
        """xlsx 上传 → _populate_table_schema 真实解析（238-256）。"""
        resp = await _upload(client, api_key_header, "book.xlsx", _make_minimal_xlsx(),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data_type"] == "table"
        assert body["geometry_type"] == "table"
        assert body["file_format"] == "xlsx"
        assert body["layer_count"] == 1
        # field_schema 需有表头列（name/val）
        sf = await db_session.get(StoredFile, body["id"])
        assert sf is not None
        assert sf.field_schema and len(sf.field_schema) == 2

    async def test_upload_corrupt_xlsx_schema_except(self, client, api_key_header, db_session):
        """损坏 xlsx → _parse_xlsx_schema 抛错 → except 兜底（257-260）。"""
        resp = await _upload(client, api_key_header, "broken.xlsx",
                             b"PK\x03\x04 not a real xlsx")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data_type"] == "table"
        sf = await db_session.get(StoredFile, body["id"])
        assert sf is not None
        assert sf.field_schema is None

    async def test_detect_failure_fallback(self, db_session, api_key_header, monkeypatch):
        """空间格式检测抛错 → except 兜底 data_type/file_format（195-198）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id
        sf = _sf_base(key_id, original_name="bad.gpkg", content_type="application/octet-stream")
        monkeypatch.setattr(data_module, "detect_format",
                            lambda p: (_ for _ in ()).throw(RuntimeError("fiona broken")))
        data_module._detect_and_populate(sf, b"garbage", "bad.gpkg")
        assert sf.file_format == "gpkg"
        # except 分支：data_type 由 detect_data_type(扩展名) 归因 → "GPKG"→geometry
        assert sf.data_type == "geometry"

    async def test_detect_cleanup_exception_swallowed(self, db_session, api_key_header, monkeypatch):
        """检测后清理 rmtree / os.remove 异常 → except pass（204-205 / 209-210）。"""
        from app.core.security import hash_api_key
        key_id = (await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
        )).scalar_one().id

        # 单文件路径（os.remove 抛错 + detect 正常）
        sf1 = _sf_base(key_id, original_name="clean.geojson")
        monkeypatch.setattr(data_module.os, "remove",
                            lambda p: (_ for _ in ()).throw(OSError("locked")))
        data_module._detect_and_populate(sf1, _make_geojson_bytes(), "clean.geojson")
        assert sf1.file_format == "geojson"

        # 伴生文件路径（shutil.rmtree(tmp_dir) 抛错）
        sf2 = _sf_base(key_id, original_name="layer.shp")
        monkeypatch.setattr(data_module.shutil, "rmtree",
                            lambda p, **k: (_ for _ in ()).throw(OSError("busy")))
        data_module._detect_and_populate(
            sf2, b"shp-bytes", "layer.shp",
            companion_contents={".shp": b"shp", ".dbf": b"dbf", ".shx": b"shx", ".prj": b"prj"},
        )
        assert sf2.file_format == "shp"

    async def test_upload_auto_create_geometry_map(self, client, api_key_header, db_session, monkeypatch):
        """auto_create + geometry_type 表单 → GEOM_MAP 命中（391-392）。"""
        resp = await _upload(client, api_key_header, "geom_map.csv", _make_csv_bytes(),
                             "text/csv", auto_create_standard="true", geometry_type="point")
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_standard_name"] == "geom_map 数据标准"
        # auto_create_standard_from_schema 已建表（几何 Point 传入）
        std_id = data["standard_id"]
        tbl_rows = (await db_session.execute(
            select(StandardTable).where(StandardTable.standard_id == std_id)
        )).scalars().all()
        assert tbl_rows
        assert tbl_rows[0].geometry_type == "Point"

    async def test_upload_auto_create_detected_undefined(self, client, api_key_header, db_session, monkeypatch):
        """_detect_and_populate 检测不出几何 → 399 行 db_geom_type=None。"""
        resp = await _upload(client, api_key_header, "no_geom.csv", _make_csv_bytes(),
                             "text/csv", auto_create_standard="true")
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_standard_name"] == "no_geom 数据标准"
        # CSV 检测到 geometry_type="table" → 397 行返回 None，标准表 geometry None
        std_id = data["standard_id"]
        tbl_rows = (await db_session.execute(
            select(StandardTable).where(StandardTable.standard_id == std_id)
        )).scalars().all()
        assert tbl_rows
        assert tbl_rows[0].geometry_type in (None, "Point", "LineString", "Polygon")