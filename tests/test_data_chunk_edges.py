# -*- coding: utf-8 -*-
"""P5k-1: data.py chunk/merge/cancel + upload 边角分支补齐。

对齐 BASELINE §17 的 P5k 目标（upload 后半段 + 子端点），本轮覆盖：

分块上传端点（POST /data/upload/chunk）
- 总切片数超上限 500 → 400
- 单片超过 3MB（1.5×余量）→ 413
- meta.json 已存在时追加（第二次/重复切片）→ chunks_received 去重累积
- GET /data/upload/chunk-status → ready_to_merge + progress

合并端点（POST /data/upload/merge）
- 切片不完整 → 400 "切片不完整"
- 切片文件物理缺失 → 400 "切片 N 文件缺失"
- 合并后超过压缩包大小上限 → 413
- 合并后超过单文件限制（非压缩包）→ 413
- 带 standard_id → 标准名冗余回填
- aggregate_layer_stats 失败 → SAVEPOINT 隔离不拖垮上传（warning 日志）
- 子任务要素展开异常 → gather(return_exceptions=True) 汇总到 warning
- 切片目录清理 rmtree 异常 → 吞掉（except pass）

取消上传（DELETE /data/upload/chunk）
- 清理已存在的切片目录
- 未知会话 → 200 ok

upload 端点边角
- 读取循环中超过 max_data_store_size_mb → 413
- 非压缩包超过单文件限制 → 413
- auto_create_standard + 已有 standard_id → 在旧标准下新增层组/表
- auto_create_standard 无几何类型 → db_geom_type=None（399 行）
- layer_group 多表时回填第一张表（几何不匹配兜底，483-486）
- 解压告警 ex_result.errors → logger.warning 不中断
- MDB 上传链路（mock extract_mdb_to_stored_files，真实子任务写入）
"""

import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from unittest.mock import patch

from app.api.v1 import data as data_module
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.stored_file import StoredFile
from app.models.standard_table import StandardTable
from tests.conftest import _TestSession


# ── session-scoped DB + key fixture ──────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _key_id(db_session, api_key_header: dict) -> str:
    """从请求头还原 API Key 的 owner id。"""
    from app.core.security import hash_api_key
    q = await db_session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key_header["X-API-Key"]))
    )
    return q.scalar_one().id


@pytest_asyncio.fixture(autouse=True)
def _disable_rate_limit():
    from app.main import app as _app
    limiter = _app.state.limiter
    old = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = old


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


def _make_zip_with_geojson(filename: str = "inner.geojson") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, _make_geojson_bytes().decode("utf-8"))
    return buf.getvalue()


async def _upload_chunk(client, headers, upload_id: str, index: int, total: int, content: bytes):
    """发送单个切片（大小超限类错误单独传大块）。"""
    return await client.post(
        "/api/v1/data/upload/chunk",
        headers=headers,
        files={"chunk": ("part.bin", io.BytesIO(content), "application/octet-stream")},
        data={"upload_id": upload_id, "chunk_index": str(index), "total_chunks": str(total)},
    )


async def _merge(client, headers, upload_id: str, filename: str, total_chunks: int = 1, **extra):
    body = {"upload_id": upload_id, "filename": filename, "total_chunks": total_chunks, **extra}
    return await client.post("/api/v1/data/upload/merge", headers=headers, json=body)


async def test_chunk_over_max_chunks(client, api_key_header):
    """总切片数 501 超过 MAX_CHUNKS(500) → 400。"""
    resp = await _upload_chunk(client, api_key_header, upload_id=uuid.uuid4().hex,
                               index=0, total=501, content=b"x" * 1024)
    assert resp.status_code == 400, resp.text
    assert "超过上限 500" in resp.json()["detail"]


async def test_chunk_oversize_payload(client, api_key_header):
    """单片超过 15MB（settings.file_chunk_size_mb=10 ×1.5 余量）→ 413。"""
    resp = await _upload_chunk(client, api_key_header, upload_id=uuid.uuid4().hex,
                               index=0, total=1, content=b"x" * (16 * 1024 * 1024))
    assert resp.status_code == 413, resp.text
    assert "切片大小" in resp.json()["detail"]


async def test_chunk_meta_append_and_dedupe(client, api_key_header):
    """meta.json 已存在时追加新切片；重复切片不重复计数。"""
    upload_id = uuid.uuid4().hex
    r0 = await _upload_chunk(client, api_key_header, upload_id, 0, 2, b"aaaa")
    assert r0.status_code == 200, r0.text
    assert r0.json()["received"] == 1

    r1 = await _upload_chunk(client, api_key_header, upload_id, 1, 2, b"bbbb")
    assert r1.status_code == 200, r1.text
    assert r1.json()["received"] == 2

    # 重复上传切片 0 → 计数不变
    r_dup = await _upload_chunk(client, api_key_header, upload_id, 0, 2, b"aaaa")
    assert r_dup.status_code == 200, r_dup.text
    assert r_dup.json()["received"] == 2


async def test_chunk_status_ready_to_merge(client, api_key_header):
    """切片收齐后 chunk-status 报告 ready_to_merge。"""
    upload_id = uuid.uuid4().hex
    for i in range(2):
        await _upload_chunk(client, api_key_header, upload_id, i, 2, f"part{i}".encode())
    resp = await client.get(
        "/api/v1/data/upload/chunk-status", headers=api_key_header,
        params={"upload_id": upload_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ready_to_merge"
    assert data["progress"] == "2/2"


async def test_merge_incomplete_chunks(client, api_key_header):
    """已接收切片 < 总切片数 → 400 切片不完整。"""
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 2, b"only-one")
    resp = await _merge(client, api_key_header, upload_id, "part.csv", total_chunks=2)
    assert resp.status_code == 400, resp.text
    assert "切片不完整" in resp.json()["detail"]


async def test_merge_missing_chunk_file(client, api_key_header):
    """meta 声称完整但切片文件被删 → 400 切片 N 文件缺失。"""
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, b"data")
    chunk_file = data_module.CHUNK_DIR / upload_id / "000000.chunk"
    assert chunk_file.exists()
    chunk_file.unlink()
    resp = await _merge(client, api_key_header, upload_id, "broken.csv")
    assert resp.status_code == 400, resp.text
    assert "切片 0 文件缺失" in resp.json()["detail"]


async def test_merge_over_archive_limit(client, api_key_header, monkeypatch):
    """合并后内容超过压缩包大小上限 → 413。"""
    monkeypatch.setattr(data_module.settings, "max_archive_size_mb", 0.001)  # 1KB
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, b"y" * 2048)
    resp = await _merge(client, api_key_header, upload_id, "big.zip")
    assert resp.status_code == 413, resp.text
    assert "合并后文件大小" in resp.json()["detail"]


async def test_merge_over_single_file_limit(client, api_key_header, monkeypatch):
    """非压缩包合并结果超过单文件限制 → 413。"""
    monkeypatch.setattr(data_module.settings, "max_data_store_size_mb", 0.01)    # 10KB
    monkeypatch.setattr(data_module.settings, "max_single_file_size_mb", 0.001)  # 1KB
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, b"y" * 2048)
    resp = await _merge(client, api_key_header, upload_id, "big.csv")
    assert resp.status_code == 413, resp.text
    assert "单文件大小" in resp.json()["detail"]


async def test_merge_with_standard_id(client, api_key_header, db_session):
    """merge 带 standard_id → 标准名冗余回填（1061-1065）。"""
    key_id = await _key_id(db_session, api_key_header)
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k1合并标准", category="auto")
    db_session.add(std)
    await db_session.commit()

    upload_id = uuid.uuid4().hex
    content = _make_csv_bytes()
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, content)
    resp = await _merge(client, api_key_header, upload_id, "merge_std.csv",
                        standard_id=std.id)
    # merge 端点未声明 status_code → 默认 200
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["standard_id"] == std.id
    assert data["data_standard_name"] == "P5k1合并标准"


async def test_merge_agg_failure_non_blocking(client, api_key_header, db_session, monkeypatch):
    """aggregate_layer_stats 抛错 → SAVEPOINT 隔离，上传不受影响（1101-1105）。"""
    async def _boom(*a, **k):
        raise RuntimeError("agg boom")

    with patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
        monkeypatch.setattr(data_module, "aggregate_layer_stats", _boom)
        upload_id = uuid.uuid4().hex
        await _upload_chunk(client, api_key_header, upload_id, 0, 1, _make_geojson_bytes())
        resp = await _merge(client, api_key_header, upload_id, "agg_fail.geojson")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # GeoJSON 无空间语义标签 → data_type=unknown（与 P2c upload 观察一致）
    assert data["geometry_type"] == "Point"
    assert data["feature_count"] == 1  # 要素仍写入了
    sf = await db_session.get(StoredFile, data["id"])
    assert sf is not None


async def test_merge_subtask_error_warning(client, api_key_header, db_session):
    """zip 内子任务要素展开抛错 → gather 汇总，warning 回填（1313-1315）。"""
    async def _explode(*a, **k):
        raise RuntimeError("layer boom")

    with patch("app.api.v1.data.AsyncSessionLocal", _TestSession), \
         patch("app.api.v1.data._load_extracted_features_async", new=_explode):
        upload_id = uuid.uuid4().hex
        await _upload_chunk(client, api_key_header, upload_id, 0, 1, _make_zip_with_geojson())
        resp = await _merge(client, api_key_header, upload_id, "broken.zip")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["warning"]
    assert "部分子文件要素展开失败" in data["warning"]
    assert data["extraction_details"]  # 子文件记录仍入库


async def test_merge_rmtree_exception_swallowed(client, api_key_header, monkeypatch):
    """合并后切片目录清理失败 → except pass 吞掉不中断（1307-1310）。"""
    def _rmtree_boom(*a, **k):
        raise OSError("dir busy")

    monkeypatch.setattr(data_module.shutil, "rmtree", _rmtree_boom)
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, _make_csv_bytes())
    resp = await _merge(client, api_key_header, upload_id, "rmtree_ok.csv")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"]


# ── cancel upload ────────────────────────────────────────────────────────────

async def test_cancel_upload_removes_directory(client, api_key_header):
    """取消上传：切片目录被清理。"""
    upload_id = uuid.uuid4().hex
    await _upload_chunk(client, api_key_header, upload_id, 0, 1, b"some data")
    chunk_subdir = data_module.CHUNK_DIR / upload_id
    assert chunk_subdir.exists()
    resp = await client.delete("/api/v1/data/upload/chunk", headers=api_key_header,
                               params={"upload_id": upload_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert not chunk_subdir.exists()


async def test_cancel_upload_unknown_session(client, api_key_header):
    """取消不存在的会话 → 200 ok。"""
    resp = await client.delete("/api/v1/data/upload/chunk", headers=api_key_header,
                               params={"upload_id": "no-such-session"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


# ── upload 边角 ──────────────────────────────────────────────────────────────

async def test_upload_over_max_store_size(client, api_key_header, monkeypatch):
    """读取循环内超过 max_data_store_size_mb → 413（302）。"""
    monkeypatch.setattr(data_module.settings, "max_data_store_size_mb", 0.001)  # 1KB
    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("too_big.csv", io.BytesIO(b"x" * 2048), "text/csv")},
    )
    assert resp.status_code == 413, resp.text
    assert "文件超过限制" in resp.json()["detail"]


async def test_upload_over_single_file_limit(client, api_key_header, monkeypatch):
    """非压缩包内容超过单文件限制 → 413（319）。"""
    monkeypatch.setattr(data_module.settings, "max_data_store_size_mb", 0.01)    # 10KB
    monkeypatch.setattr(data_module.settings, "max_single_file_size_mb", 0.001)  # 1KB
    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("too_big_single.csv", io.BytesIO(b"x" * 2048), "text/csv")},
    )
    assert resp.status_code == 413, resp.text
    assert "单文件大小" in resp.json()["detail"]


async def test_upload_auto_create_with_existing_standard(client, api_key_header, db_session):
    """auto_create_standard + 已有 standard_id → 旧标准下新增层组（404-411）。"""
    key_id = await _key_id(db_session, api_key_header)
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k1目标标准", category="auto")
    db_session.add(std)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("create_in_std.csv", io.BytesIO(_make_csv_bytes()), "text/csv")},
        data={"auto_create_standard": "true", "standard_id": std.id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["standard_id"] == std.id
    assert data["data_standard_name"] == "P5k1目标标准"

    # 自动创建的层组/标准表挂在同标准下
    lg_rows = (await db_session.execute(
        select(StandardLayerGroup).where(StandardLayerGroup.standard_id == std.id)
    )).scalars().all()
    assert lg_rows
    tbl_rows = (await db_session.execute(
        select(StandardTable).where(StandardTable.standard_id == std.id)
    )).scalars().all()
    assert tbl_rows


async def test_upload_auto_create_no_geometry(client, api_key_header, db_session):
    """auto_create_standard 空几何 → db_geom_type=None，新建标准成功（399）。"""
    key_id = await _key_id(db_session, api_key_header)
    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("create_fresh.csv", io.BytesIO(_make_csv_bytes()), "text/csv")},
        data={"auto_create_standard": "true"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # CSV 检测不出几何 → 新建标准，默认名 = stem + " 数据标准"
    assert data["data_standard_name"] == "create_fresh 数据标准"
    assert data["standard_id"]
    std = await db_session.get(DataStandard, data["standard_id"])
    assert std is not None
    assert std.api_key_id == key_id


async def test_upload_link_group_fallback_first_table(client, api_key_header, db_session):
    """layer_group 多表、压缩包主记录无几何 → 兜底回填第一张表（483-486）。

    CSV 会检测出 geometry_type="table" 从而在 452 的几何一致性校验直接 400；
    要走到 472-486 的 fallback，必须用 is_archive=True 的主记录
    （zip 主记录 geometry_type=None，跳过校验，_picked 兜底第一张）。
    """
    key_id = await _key_id(db_session, api_key_header)
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k1兜底标准", category="auto")
    group = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id, group_name="G1")
    tbl_a = StandardTable(id=str(uuid.uuid4()), layer_id=group.id, standard_id=std.id,
                          table_name="aaa_table", geometry_type="LineString")
    tbl_b = StandardTable(id=str(uuid.uuid4()), layer_id=group.id, standard_id=std.id,
                          table_name="bbb_table", geometry_type="Point")
    db_session.add_all([std, group, tbl_a, tbl_b])
    await db_session.commit()

    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("link_fallback.zip", io.BytesIO(_make_zip_with_geojson()), "application/zip")},
        data={"layer_group_id": group.id, "standard_id": std.id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["standard_table_id"] == tbl_a.id  # order_by(table_name) 第一张


async def test_upload_archive_extract_errors(client, api_key_header, monkeypatch):
    """解压返回 errors → logger.warning 后继续入库（614-615）。"""
    fake_ex = SimpleNamespace(files=[], errors=["boom: broken member"])
    monkeypatch.setattr(data_module, "extract_archive", lambda *a, **k: fake_ex)
    resp = await client.post(
        "/api/v1/data/upload",
        headers=api_key_header,
        files={"file": ("warn_zip.zip", io.BytesIO(_make_zip_with_geojson()), "application/zip")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["extracted_count"] == 0


async def test_upload_mdb_flow(client, api_key_header, db_session):
    """MDB 上传链路：mock 提取器，走真实子任务写入 + 父记录聚合（492-598）。"""
    key_id = await _key_id(db_session, api_key_header)

    sub_id = str(uuid.uuid4())
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [113.2, 23.1]},
        "properties": {"name": "GZ"},
    }]
    stored_key = {
        "id": sub_id,
        "api_key_id": key_id,
        "original_name": "roads.mdb/roads.geojson",
        "content_type": "application/geo+json",
        "file_size": 120,
        "file_data": json.dumps({"type": "FeatureCollection", "features": features}).encode(),
        "file_hash": "mdb-hash-test",
        "source_archive": "roads.mdb",
        "is_archive": False,
        "data_type": "spatial",
        "geometry_type": "Point",
        "file_format": "GeoJSON",
        "field_schema": {"fields": [{"name": "name", "type": "string"}]},
    }
    with patch("app.services.mdb_processor.extract_mdb_to_stored_files",
               return_value=[{"stored_file": stored_key, "features": features}]), \
         patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("roads.mdb", io.BytesIO(b"fake mdb bytes"), "application/x-msaccess")},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["extracted_count"] == 1
    assert data["feature_count"] == 1
    assert data["warning"] is None

    sub_sf = await db_session.get(StoredFile, sub_id)
    assert sub_sf is not None
    assert sub_sf.feature_count == 1
    assert sub_sf.source_archive == "roads.mdb"
    assert sub_sf.file_data is None  # 提取后清空原始字节


# ── MDB 边角分支 ─────────────────────────────────────────────────────────────

def _mdb_result(sub_id: str, key_id: str, filename: str, features: list | None = None,
                detected_crs: str | None = None) -> list[dict]:
    """构造 extract_mdb_to_stored_files 的返回值。"""
    if features is None:
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [113.2, 23.1]},
            "properties": {"name": "GZ"},
        }]
    stored = {
        "id": sub_id,
        "api_key_id": key_id,
        "original_name": f"{filename}/{filename}.geojson",
        "content_type": "application/geo+json",
        "file_size": 120,
        "file_data": json.dumps({"type": "FeatureCollection", "features": features}).encode(),
        "file_hash": "mdb-hash",
        "source_archive": filename,
        "is_archive": False,
        "data_type": "spatial",
        "geometry_type": "Point" if features else "Unknown",
        "file_format": "GeoJSON",
        "field_schema": {"fields": [{"name": "name", "type": "string"}]},
    }
    if detected_crs:
        stored["detected_crs"] = detected_crs
    return [{"stored_file": stored, "features": features}]


async def test_upload_mdb_extract_failure(client, api_key_header):
    """extract_mdb_to_stored_files 抛异常 → 400（499-501）。"""
    def _boom(*a, **k):
        raise RuntimeError("access driver missing")

    with patch("app.services.mdb_processor.extract_mdb_to_stored_files", new=_boom):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("bad.mdb", io.BytesIO(b"junk"), "application/x-msaccess")},
        )
    assert resp.status_code == 400, resp.text
    assert "Failed to process MDB file" in resp.json()["detail"]


async def test_upload_mdb_with_standard_id(client, api_key_header, db_session):
    """MDB 子文件回填 standard_id / standard_table_id（518-520）。"""
    key_id = await _key_id(db_session, api_key_header)
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key_id, name="P5k1MDB标准", category="auto")
    group = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id, group_name="G1")
    tbl = StandardTable(id=str(uuid.uuid4()), layer_id=group.id, standard_id=std.id,
                        table_name="mdb_tbl", geometry_type="Point")
    db_session.add_all([std, group, tbl])
    await db_session.commit()

    sub_id = str(uuid.uuid4())
    with patch("app.services.mdb_processor.extract_mdb_to_stored_files",
               return_value=_mdb_result(sub_id, key_id, "std.mdb")), \
         patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("std.mdb", io.BytesIO(b"mdb"), "application/x-msaccess")},
            data={"standard_id": std.id, "standard_table_id": tbl.id},
        )
    assert resp.status_code == 201, resp.text
    sub_sf = await db_session.get(StoredFile, sub_id)
    assert sub_sf is not None
    assert sub_sf.standard_id == std.id
    assert sub_sf.standard_table_id == tbl.id


async def test_upload_mdb_no_sub_tasks(client, api_key_header, db_session):
    """MDB 结果无要素 → sub_tasks 空，load_errors=[]（544-548）。"""
    key_id = await _key_id(db_session, api_key_header)
    sub_id = str(uuid.uuid4())
    with patch("app.services.mdb_processor.extract_mdb_to_stored_files",
               return_value=_mdb_result(sub_id, key_id, "empty.mdb", features=[])):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("empty.mdb", io.BytesIO(b"mdb"), "application/x-msaccess")},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["extracted_count"] == 1
    assert data["feature_count"] == 0
    assert data["warning"] is None


async def test_upload_mdb_parent_crs_fallback(client, api_key_header, db_session):
    """子记录 detected_crs 非空 → 回填父 archive 记录（567-568）。"""
    key_id = await _key_id(db_session, api_key_header)
    sub_id = str(uuid.uuid4())
    with patch("app.services.mdb_processor.extract_mdb_to_stored_files",
               return_value=_mdb_result(sub_id, key_id, "crs.mdb",
                                        detected_crs="EPSG:4490")), \
         patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("crs.mdb", io.BytesIO(b"mdb"), "application/x-msaccess")},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["detected_crs"] == "EPSG:4490"


async def test_upload_mdb_subtask_failure_warning(client, api_key_header, db_session):
    """MDB 子任务失败 → load_errors 汇总 warning（577-579）。"""
    key_id = await _key_id(db_session, api_key_header)
    sub_id = str(uuid.uuid4())

    async def _explode(*a, **k):
        raise RuntimeError("mdb write boom")

    with patch("app.services.mdb_processor.extract_mdb_to_stored_files",
               return_value=_mdb_result(sub_id, key_id, "fail.mdb")), \
         patch("app.api.v1.data.AsyncSessionLocal", _TestSession), \
         patch("app.api.v1.data._load_mdb_features_async", new=_explode):
        resp = await client.post(
            "/api/v1/data/upload",
            headers=api_key_header,
            files={"file": ("fail.mdb", io.BytesIO(b"mdb"), "application/x-msaccess")},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["warning"]
    assert "部分子文件要素展开失败" in data["warning"]