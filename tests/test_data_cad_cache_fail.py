# -*- coding: utf-8 -*-
"""CAD 快照缓存失败分支测试（覆盖回跌收口 P7b）。

目标: data.py L344-345 — 上传 DXF 时 cache_cad_snapshot 抛异常被吞，
不影响上传成功（返回 201 + warning 日志）。
"""
import io

import ezdxf
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app


def _make_dxf_content() -> bytes:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Wall", color=1)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "Wall"})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


@pytest_asyncio.fixture(scope="module")
async def fail_key(test_db):
    """专用 API Key（上传限流 5/min，本模块只用 1 次上传）。"""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from tests.conftest import _TestSession
    raw, hashed = generate_api_key()
    async with _TestSession() as session:
        key = ApiKey(key_hash=hashed, name="cad_fail_test_key", is_active=True)
        session.add(key)
        await session.commit()
    return {"X-API-Key": raw}


class TestCADCacheFail:
    async def test_cache_failure_swallowed(self, fail_key, monkeypatch):
        """cache_cad_snapshot 抛异常 → 上传仍 201（L344-345）。"""
        import app.services.cad_cache as cad_cache

        async def _boom(*args, **kwargs):
            raise RuntimeError("simulated cache failure")

        monkeypatch.setattr(cad_cache, "cache_cad_snapshot", _boom)

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/data/upload",
                headers=fail_key,
                files={"file": ("boom.dxf", _make_dxf_content(),
                                "application/dxf")},
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["id"]