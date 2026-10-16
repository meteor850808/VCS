# -*- coding: utf-8 -*-
"""CAD 路由集成测试。

架构说明: 由于项目【优化#5】上传后清空 file_data，CAD 路由读缓存
（上传时生成的 cad_metadata + 磁盘预览文件），因此本测试直接验证缓存链路。
上传端点限流 5/min，故 session 级 fixture 只上传一次，全部测试复用。
"""
import io

import ezdxf
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from tests.conftest import _TestSession


def _make_dxf_content() -> bytes:
    """生成带 2 图层 + 3 实体的测试 DXF（Wall: LINE+LWPOLYLINE, Text: TEXT）。"""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Wall", color=1)
    doc.layers.add("Text", color=3)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "Wall"})
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 80), (0, 80)],
                       close=True, dxfattribs={"layer": "Wall"})
    msp.add_text("Room A", dxfattribs={"layer": "Text", "height": 5,
                 "insert": (50, 40)})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


@pytest_asyncio.fixture(scope="session")
async def cad_key_header(test_db):
    """session 级 CAD 专用 API Key（上传限流 5/min，须复用；依赖 test_db 建表）。"""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    raw, hashed = generate_api_key()
    async with _TestSession() as session:
        key = ApiKey(key_hash=hashed, name="cad_test_key", is_active=True)
        session.add(key)
        await session.commit()
    return {"X-API-Key": raw}


@pytest_asyncio.fixture(scope="session")
async def uploaded_cad(cad_key_header):
    """session 级：上传一次 DXF，全部路由测试复用同一 file_id。"""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/data/upload",
            headers=cad_key_header,
            files={"file": ("test.dxf", _make_dxf_content(),
                            "application/dxf")},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]


async def _get_cad(url: str, key_header: dict, principal: str = ""):
    """统一 CAD 请求入口（需 app.dependency_overrides 已指向 vcs_test）。"""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        return await c.get(url, headers=key_header)


class TestCADInfo:
    async def test_info_ok(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(f"/api/v1/cad/{uploaded_cad}/info", cad_key_header)
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["format"] == "DXF"
        assert d["layer_count"] >= 2
        assert d["entity_total"] >= 3
        assert d["preview_url"] == f"/api/v1/cad/{uploaded_cad}/preview"

    async def test_info_404(self, cad_key_header):
        resp = await _get_cad("/api/v1/cad/no-such-id/info", cad_key_header)
        assert resp.status_code == 404

    async def test_info_non_cad_format(self, cad_key_header):
        """非 CAD 文件（CSV）访问 CAD 端点返回 400。"""
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            up = await c.post(
                "/api/v1/data/upload",
                headers=cad_key_header,
                files={"file": ("t.csv", "a,b\n1,2\n".encode(),
                                "text/csv")},
            )
        assert up.status_code == 201, up.text
        csv_id = up.json()["id"]
        resp = await _get_cad(f"/api/v1/cad/{csv_id}/info", cad_key_header)
        assert resp.status_code == 400


class TestCADLayers:
    async def test_layers_ok(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(f"/api/v1/cad/{uploaded_cad}/layers", cad_key_header)
        assert resp.status_code == 200
        names = {l["name"] for l in resp.json()["layers"]}
        assert "Wall" in names
        assert "Text" in names

    async def test_layers_404(self, cad_key_header):
        resp = await _get_cad("/api/v1/cad/no-such-id/layers", cad_key_header)
        assert resp.status_code == 404


class TestCADEntities:
    async def test_entities_ok(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(f"/api/v1/cad/{uploaded_cad}/entities",
                              cad_key_header)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 3

    async def test_entities_filter(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/entities?layer=Wall", cad_key_header)
        assert resp.status_code == 200
        for e in resp.json()["entities"]:
            assert e["layer"] == "Wall"

    async def test_entities_pagination(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/entities?limit=1", cad_key_header)
        assert resp.status_code == 200
        assert len(resp.json()["entities"]) == 1
        assert resp.json()["total"] >= 3


class TestCADPreview:
    async def test_preview_svg(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/preview?format=svg", cad_key_header)
        assert resp.status_code == 200, resp.text
        assert "image/svg" in resp.headers["content-type"]

    async def test_preview_png(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/preview?format=png", cad_key_header)
        assert resp.status_code == 200, resp.text
        assert "image/png" in resp.headers["content-type"]

    async def test_preview_404(self, cad_key_header):
        resp = await _get_cad("/api/v1/cad/no-such-id/preview", cad_key_header)
        assert resp.status_code == 404

    async def test_preview_bad_format(self, uploaded_cad, cad_key_header):
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/preview?format=bmp", cad_key_header)
        assert resp.status_code == 400


class TestCADMetadataCache:
    """验证上传时缓存真实写入 cad_metadata / cad_preview_path。"""

    async def test_cache_populated(self, uploaded_cad):
        async with _TestSession() as session:
            from app.models.stored_file import StoredFile
            from sqlalchemy import select
            r = await session.execute(
                select(StoredFile).where(StoredFile.id == uploaded_cad)
            )
            sf = r.scalar_one()
            assert sf.cad_metadata is not None
            assert sf.cad_metadata["info"]["layer_count"] >= 2
            assert sf.cad_metadata["entities_total"] >= 3
            assert sf.cad_preview_path

    async def test_preview_files_on_disk(self, uploaded_cad):
        from app.api.v1.cad import _common as C
        sf = await _get_sf(uploaded_cad)
        assert C._preview_file(sf, "svg") is not None
        assert C._preview_file(sf, "png") is not None


async def _get_sf(file_id):
    async with _TestSession() as session:
        from app.models.stored_file import StoredFile
        from sqlalchemy import select
        r = await session.execute(
            select(StoredFile).where(StoredFile.id == file_id)
        )
        return r.scalar_one()


class TestCADBranchCoverage:
    """补测 cad 路由剩余分支（P7 覆盖回跌收口）。"""

    async def _cad_key_id(self, cad_key_header):
        """从 X-API-Key 头反查对应 ApiKey.id（scope 匹配需同 key）。"""
        from app.core.security import hash_api_key
        from app.models.api_key import ApiKey
        from sqlalchemy import select
        h = hash_api_key(cad_key_header["X-API-Key"])
        async with _TestSession() as session:
            r = await session.execute(
                select(ApiKey.id).where(ApiKey.key_hash == h))
            row = r.first()
            assert row, "找不到 cad_key 对应 API Key"
            return row[0]

    async def test_entities_type_filter(self, uploaded_cad, cad_key_header):
        """entities?entity_type=LINE → 只返回 LINE 实体（L34 分支）。"""
        resp = await _get_cad(
            f"/api/v1/cad/{uploaded_cad}/entities?entity_type=LINE",
            cad_key_header)
        assert resp.status_code == 200, resp.text
        for e in resp.json()["entities"]:
            assert e["entity_type"] == "LINE"

    async def test_metadata_missing_returns_421(self, test_db, cad_key_header):
        """cad_metadata 为空 → 421（上传时缓存未生成）。"""
        import uuid
        from app.models.stored_file import StoredFile
        sf = StoredFile(
id=str(uuid.uuid4()),
            original_name="nometa.dxf",
            file_format="dxf",
            content_type="application/dxf",
            file_size=0,
            is_archive=False,
            api_key_id=await self._cad_key_id(cad_key_header),
            cad_metadata=None,  # 关键：无缓存
        )
        async with _TestSession() as session:
            session.add(sf)
            await session.commit()
        resp = await _get_cad(f"/api/v1/cad/{sf.id}/info", cad_key_header)
        assert resp.status_code == 421, resp.text

    async def test_metadata_json_string(self, test_db, cad_key_header):
        """cad_metadata 为 JSON 字符串 → json.loads 解析（L50 分支）。"""
        import json as _json
        import uuid
        from app.models.stored_file import StoredFile
        meta = {
            "info": {"format": "DXF", "version": "R2018",
                     "layer_count": 1, "entity_total": 0,
                     "preview_url": "/api/v1/cad/x/preview"},
            "layers": [],
            "entities": [],
            "entities_total": 0,
            "entities_cached": 0,
        }
        sf = StoredFile(
id=str(uuid.uuid4()),
            original_name="strmeta.dxf",
            file_format="dxf",
            content_type="application/dxf",
            file_size=0,
            is_archive=False,
            api_key_id=await self._cad_key_id(cad_key_header),
            cad_metadata=_json.dumps(meta),  # 字符串形式
        )
        async with _TestSession() as session:
            session.add(sf)
            await session.commit()
        resp = await _get_cad(f"/api/v1/cad/{sf.id}/info", cad_key_header)
        assert resp.status_code == 200, resp.text
        assert resp.json()["format"] == "DXF"

    async def test_preview_file_missing_404(self, test_db, cad_key_header):
        """预览文件不在磁盘 → 404（preview.py L25 分支，区别于 no-such-id）。"""
        import uuid
        from app.models.stored_file import StoredFile
        sf = StoredFile(
id=str(uuid.uuid4()),
            original_name="nopv.dxf",
            file_format="dxf",
            content_type="application/dxf",
            file_size=0,
            is_archive=False,
            api_key_id=await self._cad_key_id(cad_key_header),
            cad_metadata={"info": {"format": "DXF"}, "layers": [],
                          "entities": [], "entities_total": 0},
            cad_preview_path=None,  # 预览路径为空 → 文件不存在
        )
        async with _TestSession() as session:
            session.add(sf)
            await session.commit()
        resp = await _get_cad(f"/api/v1/cad/{sf.id}/preview?format=svg",
                              cad_key_header)
        assert resp.status_code == 404, resp.text


