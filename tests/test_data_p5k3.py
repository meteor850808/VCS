# -*- coding: utf-8 -*-
"""P5k-3: data.py GDB/SHP/MDB 加载链路 + upload/merge archive 分支补充。

对齐 BASELINE §17 的 P5k-3 目标（data.py 82% 后剩余 181 miss 中最大块），本轮覆盖：

upload 端点 ZIP（archive 分支 1143-1312）
- GDB 组处理（1143-1159）+ 伴生无主 SHP continue（1163-1164）
- tables 块（1226-1248）+ gather/collect（1250-1253）
- GDB 临时目录清理 happy / rmtree 失败 warning（1261-1265）
- 子统计回填 + 父聚合 + details 回填（1273-1296）
- 逐子文件 agg SAVEPOINT 隔离（1299-1305）+ 子文件 file_data 清空（1307-1312）

merge 端点 ZIP（archive 分支 647-768）
- GDB 组（647-663）、纯伴生 SHP 跳过 detail（668-675）、ignored detail（764-768）

函数级直调
- _handle_gdb_group（1910-1969 happy / 1915-1921 无 layers）
- _sync_load_gdb_layer（1994-2018 happy / except / crs_warn）— SyncSessionLocal mock
- _load_gdb_layer_async（2033-2035）to_thread 转发
- _collect_sub_task_results（2038-2053）三形态
- _load_extracted_features_async（2056-2090）happy / crs_warn / 非矢量 / except
- _load_shp_group_features_async（2113-2172）happy / crs_warn / except

upload auto_create_standard 无检测几何（416）

已知跳过（留档，见 ADR P5k-3）：
- 274-277 _parse_table_schema 最外层 except（fiona 表解析失败构造，低价值）
- 331 空文件名 400（FastAPI 422 先拦截，不可达）
- 800-804 / 1704 结构性不可达（gdb 组必有 sub_task；standard_id 空串 FK 违约）
"""

import io
import json
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.v1 import data as data_module
from app.core.security import hash_api_key
from app.models.api_key import ApiKey
from app.models.stored_file import StoredFile
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from tests.conftest import _TestSession


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    from app.main import app as _app
    limiter = getattr(_app.state, "limiter", None)
    if limiter is None:
        yield
        return
    old = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = old


def _make_zip(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            if isinstance(content, bytes):
                zf.writestr(name, content)
            else:
                zf.writestr(name, content.encode("utf-8"))
    return buf.getvalue()


def _make_geojson_bytes() -> bytes:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
             "properties": {"name": "Shanghai"}},
        ],
    }
    return json.dumps(fc).encode("utf-8")


async def _key_id(db_session, tag: str) -> str:
    """按 tag 前缀当场建 key 并返回 (api_key_id, 原始 key)。"""
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=f"p5k3-{tag}")
    db_session.add(key)
    await db_session.commit()
    return key.id, raw


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


async def _upload_chunk(client, headers, upload_id: str, index: int, total: int, content: bytes):
    return await client.post(
        "/api/v1/data/upload/chunk",
        headers=headers,
        files={"chunk": ("part.bin", io.BytesIO(content), "application/octet-stream")},
        data={"upload_id": upload_id, "chunk_index": str(index), "total_chunks": str(total)},
    )


async def _merge(client, headers, upload_id: str, filename: str, total_chunks: int = 1, **extra):
    body = {"upload_id": upload_id, "filename": filename, "total_chunks": total_chunks, **extra}
    return await client.post("/api/v1/data/upload/merge", headers=headers, json=body)


def _fake_gdb_info(tmp_path, layers: list):
    gdb_dir = str(tmp_path / "fake.gdb")
    if layers:
        Path(gdb_dir).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        gdb_path=gdb_dir if layers else "",
        gdb_basename="fake",
        layers=layers,
        tmp_dir=gdb_dir,
    )


def _gdb_layers():
    return [
        SimpleNamespace(name="roads", geometry_type="LineString", crs_wkt="EPSG:4326"),
        SimpleNamespace(name="parcels", geometry_type="Polygon", crs_wkt="EPSG:4326"),
    ]


def _fake_stats(stored_file_id: str, written: int = 3) -> dict:
    return {"stored_file_id": stored_file_id, "written": written, "skipped_empty": 0,
            "skipped_no_geometry": 0, "total": written, "geometry_type": "Point", "error": None}


# ══════════════════════════════════════════════════════════════════════════════
# upload 端点 ZIP：GDB 组 / 伴生 no-SHP / tables / tag 聚合
# ══════════════════════════════════════════════════════════════════════════════

class TestUploadZipBranches:

    async def test_upload_zip_gdb_shp_companion_table(self, client, db_session, monkeypatch, tmp_path):
        """ZIP 全类型 → gdb 组/伴生无主 SHP/tables/矢量 gather，201 + 子行落库。"""
        key_id, raw = await _key_id(db_session, "zipgdb")
        headers = {"X-API-Key": raw}
        gdb_dir = str(tmp_path / "real.gdb")
        Path(gdb_dir).mkdir(parents=True)
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda files, gdb_dir_name, tmp_dir: _fake_gdb_info(tmp_path, _gdb_layers()))
        with patch("app.api.v1.data._load_gdb_layer_async",
                   new_callable=AsyncMock,
                   return_value=_fake_stats("gdb-layer-1", 5)), \
             patch("app.api.v1.data.aggregate_layer_stats",
                   new_callable=AsyncMock, return_value=None), \
             patch("app.api.v1.data.AsyncSessionLocal", _TestSession):
            resp = await client.post(
                "/api/v1/data/upload", headers=headers,
                files={"file": ("vec_archive.zip",
                                io.BytesIO(_make_zip({
                                    "city.gdb/x.gdbtable": b"gdb-bin1",
                                    "city.gdb/a.gdbindexes": b"gdb-bin2",
                                    "roads.dbf": b"dbf-bytes",
                                    "roads.shx": b"shx-bytes",
                                    "junk.xyz": b"unknown-file",
                                    "info.csv": "id,name\n1,site\n",
                                    "pts.geojson": _make_geojson_bytes(),
                                })), "application/zip")},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data_type"] == "archive"
        detail_types = {d["type"] for d in (body["extraction_details"] or [])}
        assert "GDB" in detail_types
        assert "SHP-companion" in detail_types
        assert "ignored" in detail_types
        assert "CSV" in detail_types
        assert body["extracted_count"] >= 3
        statuses = {(d["type"], d["status"]) for d in (body["extraction_details"] or [])}
        assert ("SHP-companion", "skipped_no_shp") in statuses
        assert ("ignored", "skipped") in statuses

        # GDB 子行落库（file_format=gdb、file_data 已清空）
        gdb_rows = (await db_session.execute(
            select(StoredFile).where(StoredFile.file_format == "gdb")
        )).scalars().all()
        assert len(gdb_rows) >= 2
        assert all(r.feature_count is not None for r in gdb_rows)
        assert all(r.file_data is None for r in gdb_rows)
        assert any(r.layer_name == "roads" for r in gdb_rows)

    async def test_upload_zip_gdb_agg_failure_non_blocking(self, client, db_session, monkeypatch, tmp_path):
        """子文件 agg 逐条 SAVEPOINT 失败 → 上传仍 201（1304-1305）。"""
        key_id, raw = await _key_id(db_session, "zipaggg")
        headers = {"X-API-Key": raw}
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: _fake_gdb_info(tmp_path, _gdb_layers()))

        async def _boom(*_a, **_k):
            raise RuntimeError("agg boom")

        monkeypatch.setattr(data_module, "aggregate_layer_stats", _boom)
        with patch("app.api.v1.data._load_gdb_layer_async",
                   new_callable=AsyncMock, return_value=_fake_stats("g1", 5)):
            resp = await client.post(
                "/api/v1/data/upload", headers=headers,
                files={"file": ("agg_archive.zip",
                                io.BytesIO(_make_zip({
                                    "city.gdb/a": b"x",
                                    "info.csv": "id\n1\n",
                                })), "application/zip")},
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["extracted_count"] >= 3

    async def test_upload_zip_gdb_cleanup_failure_warns(self, client, db_session, monkeypatch, tmp_path):
        """GDB 临时目录清理 rmtree 失败 → warning 不拖垮（1264-1265）。"""
        key_id, raw = await _key_id(db_session, "zipcln")
        headers = {"X-API-Key": raw}
        # 指向不存在目录 → rmtree FileNotFoundError → except warning
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: SimpleNamespace(
                                gdb_path=str(tmp_path / "gone.gdb"), gdb_basename="gone",
                                layers=_gdb_layers(), tmp_dir=str(tmp_path / "gone.gdb")))
        with patch("app.api.v1.data._load_gdb_layer_async",
                   new_callable=AsyncMock, return_value=_fake_stats("g2", 1)), \
             patch("app.api.v1.data.aggregate_layer_stats",
                   new_callable=AsyncMock, return_value=None):
            resp = await client.post(
                "/api/v1/data/upload", headers=headers,
                files={"file": ("cln_archive.zip",
                                io.BytesIO(_make_zip({"city.gdb/a": b"x"})), "application/zip")},
            )
        assert resp.status_code == 201, resp.text

    async def test_upload_zip_gdb_no_layers_skipped(self, client, db_session, monkeypatch, tmp_path):
        """prepare_gdb 空 layers → _handle_gdb_group 直接返回（1915-1921）。"""
        key_id, raw = await _key_id(db_session, "zipnog")
        headers = {"X-API-Key": raw}
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: _fake_gdb_info(tmp_path, []))
        with patch("app.api.v1.data.aggregate_layer_stats",
                   new_callable=AsyncMock, return_value=None):
            resp = await client.post(
                "/api/v1/data/upload", headers=headers,
                files={"file": ("nog_archive.zip",
                                io.BytesIO(_make_zip({"city.gdb/a": b"x",
                                                      "t.txt": "plain"})), "application/zip")},
            )
        assert resp.status_code == 201, resp.text

class TestMergeZipBranches:

    async def test_merge_zip_gdb_and_companion_continue(self, client, db_session, monkeypatch, tmp_path):
        """merge_chunks ZIP：GDB 组 detail + 无主 SHP continue（1143-1164）。"""
        upload_id = uuid.uuid4().hex
        zip_bytes = _make_zip({
            "city.gdb/x.gdbtable": b"gdb-bin",
            "roads.dbf": b"dbf-bytes",
            "roads.shx": b"shx-bytes",
            "junk.xyz": b"unknown-file",
        })
        key_id, raw = await _key_id(db_session, "mergezip")
        headers = {"X-API-Key": raw}
        parts = [zip_bytes[i:i + 64 * 1024] for i in range(0, len(zip_bytes), 64 * 1024)]
        for i, part in enumerate(parts):
            assert (await _upload_chunk(client, headers, upload_id, i, len(parts), part)).status_code == 200

        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: _fake_gdb_info(tmp_path, _gdb_layers()))
        # rmtree 失败（GDB 临时目录被占用）→ 1264-1265 except 告警不中断
        with patch("app.api.v1.data._load_gdb_layer_async",
                   new_callable=AsyncMock, return_value=_fake_stats("m1", 4)), \
             patch("app.api.v1.data.shutil.rmtree", side_effect=OSError("locked")):
            resp = await _merge(client, headers, upload_id, "merge_mix.zip", total_chunks=len(parts))
        assert resp.status_code == 200, resp.text
        details = resp.json()["extraction_details"] or []
        detail_types = {d["type"] for d in details}
        # merge_chunks 对无主 SHP/ignored 不产出 detail（1163-1164 continue）
        assert detail_types == {"GDB"}


# ══════════════════════════════════════════════════════════════════════════════
# 函数级：_load_gdb_layer_async / _sync_load_gdb_layer / _collect_sub_task_results
# ══════════════════════════════════════════════════════════════════════════════

class TestGdbLoaderUnit:

    async def test_load_gdb_layer_async_forwards(self, monkeypatch):
        """_load_gdb_layer_async → to_thread(_sync_load_gdb_layer)（2033-2035）。"""
        def _fake_sync(*a, **k):
            return {"stored_file_id": a[0], "written": 7}
        monkeypatch.setattr(data_module, "_sync_load_gdb_layer", _fake_sync)
        stats = await data_module._load_gdb_layer_async(
            "sf-1", "/tmp/x.gdb", "roads", "key-1", srid=4326)
        assert stats["written"] == 7

    async def test_sync_load_gdb_layer_happy(self, monkeypatch):
        """GDB 同步 loader happy：写入 2 要素、无坐标域告警（1994-2014）。"""
        monkeypatch.setattr(data_module, "SyncSessionLocal",
                            lambda: MagicMock(__enter__=lambda s: MagicMock(), __exit__=lambda *a: False))
        monkeypatch.setattr(data_module, "gdb_read_lock", lambda path: _Ctx())
        monkeypatch.setattr(data_module, "read_layer_features",
                            lambda p, l: iter([{"geometry": {"type": "Point", "coordinates": [1, 2]}}]) )
        monkeypatch.setattr(data_module, "write_features_batch_sync",
                            lambda *a, **k: {"written": 2, "skipped_empty": 0, "skipped_no_geometry": 0, "total": 2})
        monkeypatch.setattr(data_module, "validate_loaded_extent_sync", lambda *a, **k: None)
        stats = data_module._sync_load_gdb_layer("sf-1", "/tmp/x.gdb", "roads", "key-1", srid=4326)
        assert stats["written"] == 2
        assert "crs_warning" not in stats

    async def test_sync_load_gdb_layer_crs_warn(self, monkeypatch):
        """坐标域校验返回非空 → crs_warning 记录（2011-2013）。"""
        monkeypatch.setattr(data_module, "SyncSessionLocal",
                            lambda: MagicMock(__enter__=lambda s: MagicMock(), __exit__=lambda *a: False))
        monkeypatch.setattr(data_module, "gdb_read_lock", lambda path: _Ctx())
        monkeypatch.setattr(data_module, "read_layer_features", lambda p, l: iter([]))
        monkeypatch.setattr(data_module, "write_features_batch_sync",
                            lambda *a, **k: {"written": 0, "skipped_empty": 1, "skipped_no_geometry": 0, "total": 0})
        monkeypatch.setattr(data_module, "validate_loaded_extent_sync",
                            lambda *a, **k: "坐标超限告警")
        stats = data_module._sync_load_gdb_layer("sf-1", "/tmp/x.gdb", "roads", "key-1", srid=4326)
        assert stats["crs_warning"] == "坐标超限告警"

    async def test_sync_load_gdb_layer_except(self, monkeypatch):
        """batch 写入抛错 → stats.error 兜底（2015-2017）。"""
        monkeypatch.setattr(data_module, "SyncSessionLocal",
                            lambda: MagicMock(__enter__=lambda s: MagicMock(), __exit__=lambda *a: False))
        monkeypatch.setattr(data_module, "gdb_read_lock", lambda path: _Ctx())
        monkeypatch.setattr(data_module, "read_layer_features",
                            lambda p, l: (_ for _ in ()).throw(RuntimeError("fiona broken")))
        stats = data_module._sync_load_gdb_layer("sf-1", "/tmp/x.gdb", "roads", "key-1", srid=4326)
        assert stats["error"] is not None

    async def test_collect_sub_task_results_states(self):
        """gather 结果归并：dict ok / dict error / Exception（2044-2052）。"""
        results = [
            {"stored_file_id": "a", "written": 2, "error": None},
            {"stored_file_id": "b", "written": 0, "error": "boom"},
            RuntimeError("explode"),
        ]
        stats_by_file, errors = data_module._collect_sub_task_results(results)
        assert stats_by_file["a"]["written"] == 2
        assert "boom" in errors
        assert "explode" in errors


# ══════════════════════════════════════════════════════════════════════════════
# 函数级：_load_extracted_features_async / _load_shp_group_features_async
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractedLoaders:

    _FEAT = [{"geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}]

    async def test_load_extracted_happy(self, monkeypatch):
        """矢量子文件展开 happy：geometry_type 归一化 + 写入（2065-2086）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_bytes", lambda c, f: self._FEAT)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 3, "skipped_empty": 0, "skipped_no_geometry": 0}))
        monkeypatch.setattr(data_module, "validate_loaded_extent", AsyncMock(return_value=None))
        stats = await data_module._load_extracted_features_async("sf-1", "x.geojson", b"{}", "owner-1", srid=4326)
        assert stats["written"] == 3
        assert stats["geometry_type"] == "Point"

    async def test_load_extracted_crs_warn(self, monkeypatch):
        """坐标域告警 → stats.crs_warning（2084-2086）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_bytes", lambda c, f: self._FEAT)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 1, "skipped_empty": 0, "skipped_no_geometry": 0}))
        monkeypatch.setattr(data_module, "validate_loaded_extent", AsyncMock(return_value="extent 警告"))
        stats = await data_module._load_extracted_features_async("sf-1", "x.geojson", b"{}", "owner-1", srid=4326)
        assert stats["crs_warning"] == "extent 警告"

    async def test_load_extracted_not_vector(self, monkeypatch):
        """非矢量扩展名 → 直接空统计（2063-2064）。"""
        stats = await data_module._load_extracted_features_async("sf-1", "x.txt", b"hi", "owner-1", srid=4326)
        assert stats["written"] == 0

    async def test_load_extracted_except(self, monkeypatch):
        """提取抛错 → stats.error 兜底（2087-2089）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_bytes",
                            lambda c, f: (_ for _ in ()).throw(RuntimeError("bad geojson")))
        stats = await data_module._load_extracted_features_async("sf-1", "x.geojson", b"{}", "owner-1", srid=4326)
        assert stats["error"] is not None

    async def test_load_shp_group_happy(self, monkeypatch, tmp_path):
        """SHP 组：真实临时目录写入 + Fiona 提取 mock（2118-2161）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_file",
                            lambda path, drv: self._FEAT)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 2, "skipped_empty": 0, "skipped_no_geometry": 0}))
        monkeypatch.setattr(data_module, "validate_loaded_extent", AsyncMock(return_value=None))
        stats = await data_module._load_shp_group_features_async(
            "sf-1", "roads.shp", b"shp-bytes",
            {".dbf": b"dbf", ".shx": b"shx"}, "owner-1", srid=4326)
        assert stats["written"] == 2
        assert stats["geometry_type"] == "Point"

    async def test_load_shp_group_crs_warn(self, monkeypatch):
        """SHP 组坐标域告警（2158-2161）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_file", lambda path, drv: self._FEAT)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 1, "skipped_empty": 0, "skipped_no_geometry": 0}))
        monkeypatch.setattr(data_module, "validate_loaded_extent", AsyncMock(return_value="warn"))
        stats = await data_module._load_shp_group_features_async(
            "sf-1", "roads.shp", b"shp", {}, "owner-1", srid=4326)
        assert stats["crs_warning"] == "warn"

    async def test_load_shp_group_except(self, monkeypatch):
        """SHP 组提取抛错 → except 兜底 + 临时目录清理（2162-2171）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_file",
                            lambda path, drv: (_ for _ in ()).throw(RuntimeError("no fiona")))
        stats = await data_module._load_shp_group_features_async(
            "sf-1", "roads.shp", b"shp", {}, "owner-1", srid=4326)
        assert stats["error"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# 函数级：_handle_gdb_group 直调
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleGdbGroupUnit:

    async def test_handle_gdb_group_no_layers(self, db_session, monkeypatch, tmp_path):
        """无 layers → 立即返回空结果（1915-1921）。"""
        key_id, _ = await _key_id(db_session, "gdbskip")
        sf = StoredFile(id=str(uuid.uuid4()), api_key_id=key_id, original_name="a.zip",
                        file_size=1, is_archive=True, data_type="archive", file_format="zip")
        db_session.add(sf)
        await db_session.commit()
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: _fake_gdb_info(tmp_path, []))
        count, details, sub_tasks, tmp = await data_module._handle_gdb_group(
            db=db_session, gdb_files=[SimpleNamespace(file_size=10)], gdb_dir="city.gdb",
            archive_filename="a.zip", api_key_id=key_id, standard_id=None,
            standard_table_id=None, resolved_standard_name=None,
            archive_record=sf, api_key_owner_id=key_id)
        assert count == 0
        assert not details and not sub_tasks
        assert tmp is None

    async def test_handle_gdb_group_happy(self, db_session, monkeypatch, tmp_path):
        """2 层 GDB → 子行落库 + 均摊大小 + sub_tasks（1933-1969）。"""
        key_id, _ = await _key_id(db_session, "gdbunit")
        sf = StoredFile(id=str(uuid.uuid4()), api_key_id=key_id, original_name="u.zip",
                        file_size=1, is_archive=True, data_type="archive", file_format="zip")
        db_session.add(sf)
        await db_session.commit()
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: _fake_gdb_info(tmp_path, _gdb_layers()))
        # 子行计数基准（仅自有档案新增，session 级 DB 复用需差分）
        before = (await db_session.execute(
            select(StoredFile).where(StoredFile.api_key_id == key_id, StoredFile.file_format == "gdb")
        )).scalars().all()
        count, details, sub_tasks, tmp = await data_module._handle_gdb_group(
            db=db_session,
            gdb_files=[SimpleNamespace(file_size=100), SimpleNamespace(file_size=101)],
            gdb_dir="city.gdb", archive_filename="u.zip", api_key_id=key_id,
            standard_id=None, standard_table_id=None, resolved_standard_name=None,
            archive_record=sf, api_key_owner_id=key_id)
        assert count == 2
        assert len(details) == 2
        assert len(sub_tasks) == 2
        assert tmp is not None
        assert sf.layer_count == 2
        rows = (await db_session.execute(
            select(StoredFile).where(StoredFile.api_key_id == key_id, StoredFile.file_format == "gdb")
        )).scalars().all()
        assert len(rows) == len(before) + 2
        assert sum(r.file_size for r in rows) == sum(r.file_size for r in before) + 201
        sizes = sorted(r.file_size for r in rows)
        assert sizes == [100, 101]


# ══════════════════════════════════════════════════════════════════════════════
# P5k-3 收尾：upload SHP 主文件 / 非压缩 agg 失败 / merge SHP+矢量 / 纯表格 /
# reindex 403 / handle_gdb_group 无路径清理 / MDB loader / 全量 loader crs_warn
# ══════════════════════════════════════════════════════════════════════════════

class TestP5k3Tail:

    async def test_upload_zip_shp_main(self, client, db_session):
        """upload_file ZIP 带 .shp 主文件 → SHP StoredFile + detail（677-704）。"""
        key_id, raw = await _key_id(db_session, "shpmain")
        headers = {"X-API-Key": raw}
        with patch("app.api.v1.data._load_shp_group_features_async",
                   new_callable=AsyncMock,
                   side_effect=lambda *a, **k: _fake_stats(a[0], 5)):
            resp = await client.post(
                "/api/v1/data/upload", headers=headers,
                files={"file": ("shp_archive.zip",
                                io.BytesIO(_make_zip({
                                    "roads.shp": b"fake-shp",
                                    "roads.dbf": b"fake-dbf",
                                    "roads.shx": b"fake-shx",
                                })), "application/zip")},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        statuses = {(d["type"], d["status"], d.get("features")) for d in (body["extraction_details"] or [])}
        assert ("SHP", "ok", 5) in statuses
        assert body["extracted_count"] >= 1

    async def test_upload_single_agg_failure_non_blocking(self, client, db_session, monkeypatch):
        """非压缩上传：aggregate_layer_stats 失败 → 仅告警不报错（785-786）。"""
        key_id, raw = await _key_id(db_session, "aggfail")
        headers = {"X-API-Key": raw}
        monkeypatch.setattr(data_module, "aggregate_layer_stats",
                            AsyncMock(side_effect=RuntimeError("agg boom")))
        resp = await client.post(
            "/api/v1/data/upload", headers=headers,
            files={"file": ("pts.geojson", io.BytesIO(_make_geojson_bytes()), "application/geo+json")},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["feature_count"] >= 1

    async def test_merge_zip_shp_and_vector(self, client, db_session):
        """merge_chunks：SHP 主文件 + 矢量文件 gather + 回填（1165-1223/1293-1296）。"""
        upload_id = uuid.uuid4().hex
        zip_bytes = _make_zip({
            "roads.shp": b"fake-shp",
            "roads.dbf": b"fake-dbf",
            "roads.shx": b"fake-shx",
            "pts.geojson": _make_geojson_bytes(),
        })
        key_id, raw = await _key_id(db_session, "mergevec")
        headers = {"X-API-Key": raw}
        parts = [zip_bytes[i:i + 64 * 1024] for i in range(0, len(zip_bytes), 64 * 1024)]
        for i, part in enumerate(parts):
            assert (await _upload_chunk(client, headers, upload_id, i, len(parts), part)).status_code == 200
        with patch("app.api.v1.data._load_shp_group_features_async",
                   new_callable=AsyncMock,
                   side_effect=lambda *a, **k: _fake_stats(a[0], 5)), \
             patch("app.api.v1.data._load_extracted_features_async",
                   new_callable=AsyncMock,
                   side_effect=lambda *a, **k: _fake_stats(a[0], 2)):
            resp = await _merge(client, headers, upload_id, "merge_sv.zip", total_chunks=len(parts))
        assert resp.status_code == 200, resp.text
        details = resp.json()["extraction_details"] or []
        statuses = {(d["type"], d["status"], d.get("features")) for d in details}
        assert ("SHP", "ok", 5) in statuses
        assert ("GEOJSON", "ok", 2) in statuses

    async def test_merge_zip_tables_only_no_sub_tasks(self, client, db_session):
        """merge_chunks：仅表格 → 无 sub_tasks/无 load_errors 分支（1255-1256）。"""
        upload_id = uuid.uuid4().hex
        zip_bytes = _make_zip({"t.csv": "a,b\n1,2\n"})
        key_id, raw = await _key_id(db_session, "mergetbl")
        headers = {"X-API-Key": raw}
        parts = [zip_bytes[i:i + 64 * 1024] for i in range(0, len(zip_bytes), 64 * 1024)]
        for i, part in enumerate(parts):
            assert (await _upload_chunk(client, headers, upload_id, i, len(parts), part)).status_code == 200
        resp = await _merge(client, headers, upload_id, "merge_t.csv.zip", total_chunks=len(parts))
        assert resp.status_code == 200, resp.text
        statuses = {(d["type"], d["status"]) for d in (resp.json()["extraction_details"] or [])}
        assert ("CSV", "table_only") in statuses

    async def test_handle_gdb_group_no_path_cleanup(self, db_session, monkeypatch, tmp_path):
        """_handle_gdb_group：无可用 gdb_path 但有 tmp_dir → 清理兜底（1916-1920）。"""
        key_id, _ = await _key_id(db_session, "gdbnopath")
        sf = StoredFile(id=str(uuid.uuid4()), api_key_id=key_id, original_name="n.zip",
                        file_size=1, is_archive=True, data_type="archive", file_format="zip")
        db_session.add(sf)
        await db_session.commit()
        monkeypatch.setattr(data_module, "prepare_gdb",
                            lambda *a, **k: SimpleNamespace(
                                gdb_path="", gdb_basename="fake", layers=[],
                                tmp_dir=str(tmp_path / "leftover")))
        monkeypatch.setattr(data_module, "shutil",
                            SimpleNamespace(rmtree=MagicMock(side_effect=OSError("busy"))))
        count, details, sub_tasks, tmp = await data_module._handle_gdb_group(
            db=db_session, gdb_files=[SimpleNamespace(file_size=10)], gdb_dir="city.gdb",
            archive_filename="a.zip", api_key_id=key_id, standard_id=None,
            standard_table_id=None, resolved_standard_name=None,
            archive_record=sf, api_key_owner_id=key_id)
        assert count == 0
        assert not details and not sub_tasks
        assert tmp is None

    async def test_reindex_forbidden_other_owner(self, client, db_session):
        """reindex：非属主 key → 403（2284）。"""
        key_id, raw = await _key_id(db_session, "reidxowner")
        headers = {"X-API-Key": raw}
        resp = await client.post(
            "/api/v1/data/upload", headers=headers,
            files={"file": ("pts.geojson", io.BytesIO(_make_geojson_bytes()), "application/geo+json")},
        )
        assert resp.status_code == 201, resp.text
        sf_id = resp.json()["id"]
        other_id, other_raw = await _key_id(db_session, "reidxtrespass")
        resp2 = await client.post(
            f"/api/v1/data/{sf_id}/reindex", headers={"X-API-Key": other_raw})
        assert resp2.status_code == 403

    async def test_load_mdb_empty_and_crs_warn(self, monkeypatch):
        """_load_mdb_features_async：空 features 早退 + crs_warning 记录（2242-2266）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 3, "skipped_empty": 0,
                                                     "skipped_no_geometry": 0, "total": 3}))
        monkeypatch.setattr(data_module, "validate_loaded_extent",
                            AsyncMock(return_value="MDB 坐标域告警"))
        stats = await data_module._load_mdb_features_async("sf-mdb", "tbl", [], "key-1")
        assert stats["written"] == 0
        stats2 = await data_module._load_mdb_features_async(
            "sf-mdb", "tbl", [{"geometry": {"type": "Point", "coordinates": [1, 2]}}], "key-1")
        assert stats2["written"] == 3
        assert stats2["crs_warning"] == "MDB 坐标域告警"

    async def test_load_mdb_except(self, monkeypatch):
        """_load_mdb_features_async：batch 写入抛错 → stats.error 兜底（2267-2269）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(side_effect=RuntimeError("disk full")))
        stats = await data_module._load_mdb_features_async(
            "sf-mdb", "tbl", [{"geometry": {"type": "Point", "coordinates": [1, 2]}}], "key-1")
        assert stats["error"] is not None

    async def test_load_all_features_crs_warn(self, monkeypatch):
        """_load_all_features_to_spatial_async：几何入库 + 坐标域告警 → crs_warning（2213-2216）。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_bytes",
                            lambda content, filename: [{"geometry": {"type": "Point", "coordinates": [1, 2]}}])
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 1, "skipped_empty": 0,
                                                     "skipped_no_geometry": 0, "total": 1}))
        monkeypatch.setattr(data_module, "validate_loaded_extent",
                            AsyncMock(return_value="坐标超限告警"))
        stats = await data_module._load_all_features_to_spatial_async(
            "sf-gj", "pts.geojson", _make_geojson_bytes(), "key-1", srid=4326)
        assert stats["written"] == 1
        assert stats["crs_warning"] == "坐标超限告警"
        assert stats["geometry_type"] == "Point"