"""P7a-2: db/session get_db + main 入口 + 模型杂项 → 100%。

- get_db: commit / rollback / close 全分支
- main: ImportError 降级（redis/prometheus 缺失）、lifespan 全分支、
  health 缓存命中 / redis ok / error / unavailable、root
- models: key_group __repr__、conversion_job/batch_job duration_seconds、
  spatial_feature resolve_table
"""

import builtins
import importlib.util
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.api_key import ApiKey


def _main_spec_path():
    from app import main as main_mod
    return Path(main_mod.__file__)


def _exec_main_without(name: str, blocked: set[str]):
    """在隔离模块里重新执行 main.py，模拟可选依赖缺失。"""
    spec = importlib.util.spec_from_file_location(name, str(_main_spec_path()))
    mod = importlib.util.module_from_spec(spec)
    real_import = builtins.__import__

    def fake_import(import_name, *a, **k):
        if import_name in blocked:
            raise ImportError(f"No module named '{import_name}'")
        return real_import(import_name, *a, **k)

    builtins.__import__ = fake_import
    try:
        spec.loader.exec_module(mod)
    finally:
        builtins.__import__ = real_import
    return mod


# ── db/session.get_db ─────────────────────────────────────────────────────────

class TestGetDb:

    async def test_commits_and_closes(self, monkeypatch):
        from app.db import session as mod

        fake = MagicMock()
        fake.commit = AsyncMock()
        fake.rollback = AsyncMock()
        fake.close = AsyncMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: fake)

        seen = None
        async for s in mod.get_db():
            seen = s
        assert seen is fake
        fake.commit.assert_awaited_once()
        fake.close.assert_awaited_once()

    async def test_rollback_on_error(self, monkeypatch):
        from app.db import session as mod

        fake = MagicMock()
        fake.commit = AsyncMock()
        fake.rollback = AsyncMock()
        fake.close = AsyncMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: fake)

        # 模拟 FastAPI Depends 注入：端点抛异常 → athrow 进生成器
        agen = mod.get_db()
        assert await agen.__anext__() is fake
        with pytest.raises(RuntimeError, match="boom"):
            await agen.athrow(RuntimeError("boom"))
        fake.commit.assert_not_awaited()
        fake.rollback.assert_awaited_once()
        fake.close.assert_awaited_once()


# ── main ──────────────────────────────────────────────────────────────────────

class TestMainRoot:

    async def test_root(self):
        from app import main as m
        out = await m.root()
        assert out["service"] == "Vector Conversion Service"
        assert out["docs"] == "/docs"


class TestMainOptionalDeps:

    def test_redis_missing_graceful(self, monkeypatch):
        import app.main as real  # noqa: F401 — 确保依赖已加载
        mod = _exec_main_without("_p7a_main_no_redis", {"redis.asyncio"})
        assert mod.aioredis is None
        assert mod._prometheus_available is True  # prometheus 仍正常

    def test_prometheus_missing_graceful(self, monkeypatch):
        mod = _exec_main_without(
            "_p7a_main_no_prom",
            {"prometheus_fastapi_instrumentator"})
        assert mod._prometheus_available is False
        assert mod.aioredis is not None


class TestMainLifespan:

    async def test_normal_lifecycle(self, monkeypatch):
        from app import main as m

        fake_redis = AsyncMock()
        # from_url 是同步类方法（不 await），用 MagicMock 而非 AsyncMock
        monkeypatch.setattr(m.aioredis, "from_url",
                            MagicMock(return_value=fake_redis))
        async with m.lifespan(m.app):
            assert m._redis_client is fake_redis
        fake_redis.aclose.assert_awaited_once()

    async def test_redis_connect_failure_warns(self, monkeypatch, caplog):
        from app import main as m

        # 清掉前置用例（test_normal_lifecycle）对模块级 _redis_client 的残留赋值
        monkeypatch.setattr(m, "_redis_client", None)
        monkeypatch.setattr(m.aioredis, "from_url",
                            MagicMock(side_effect=RuntimeError("refused")))
        async with m.lifespan(m.app):
            assert m._redis_client is None


class TestMainHealth:

    async def test_cache_hit_skips_db(self, monkeypatch):
        from app import main as m

        data = {"status": "ok", "postgres": "fake", "redis": "fake"}
        monkeypatch.setattr(m, "_health_cache",
                            {"data": data, "expires_at": time.time() + 30})

        def boom(*a, **k):
            raise AssertionError("缓存命中不应访问 DB/Redis")

        monkeypatch.setattr(m, "AsyncSessionLocal", boom)
        monkeypatch.setattr(m, "_redis_client", boom)
        out = await m.health_check()
        assert out == data

    async def test_full_ok_and_caches(self, monkeypatch):
        from app import main as m

        monkeypatch.setattr(m, "_health_cache", {"data": None, "expires_at": 0})
        fake_redis = AsyncMock()
        fake_redis.ping.return_value = True
        monkeypatch.setattr(m, "_redis_client", fake_redis)
        out = await m.health_check()
        assert out.status == "ok"
        assert out.redis == "ok"
        assert m._health_cache["data"] is out
        assert m._health_cache["expires_at"] > time.time() + 5

    async def test_redis_ping_error(self, monkeypatch):
        from app import main as m

        monkeypatch.setattr(m, "_health_cache", {"data": None, "expires_at": 0})
        fake_redis = AsyncMock()
        fake_redis.ping.side_effect = RuntimeError("timeout")
        monkeypatch.setattr(m, "_redis_client", fake_redis)
        out = await m.health_check()
        assert out.status == "degraded"
        assert out.redis == "error: timeout"

    async def test_redis_unavailable(self, monkeypatch):
        from app import main as m

        monkeypatch.setattr(m, "_health_cache", {"data": None, "expires_at": 0})
        monkeypatch.setattr(m, "_redis_client", None)
        out = await m.health_check()
        assert out.redis == "unavailable"
        assert out.status == "degraded"


# ── models 杂项 ───────────────────────────────────────────────────────────────

class TestKeyGroupRepr:

    def test_empty(self):
        from app.models.key_group import KeyGroup
        g = KeyGroup(name="组A")
        assert repr(g) == "<KeyGroup 组A (0 keys)>"

    def test_with_keys(self):
        from app.models.key_group import KeyGroup
        g = KeyGroup(name="组B")
        k = ApiKey(key_hash="h-1", name="k", is_active=True)
        k.group = g  # back_populates 双向填充，无需落库
        assert repr(g) == "<KeyGroup 组B (1 keys)>"


class TestJobDuration:

    @staticmethod
    def _t():
        return datetime.now(timezone.utc)

    def test_conversion_job_complete(self):
        from app.models.conversion_job import ConversionJob
        t0 = self._t()
        j = ConversionJob(started_at=t0, finished_at=t0 + timedelta(seconds=5))
        assert j.duration_seconds == 5.0

    def test_conversion_job_incomplete(self):
        from app.models.conversion_job import ConversionJob
        assert ConversionJob(started_at=None, finished_at=None).duration_seconds is None
        assert ConversionJob(started_at=self._t(), finished_at=None).duration_seconds is None

    def test_batch_job_complete(self):
        from app.models.batch_job import BatchJob
        t0 = self._t()
        j = BatchJob(started_at=t0, finished_at=t0 + timedelta(seconds=3))
        assert j.duration_seconds == 3.0

    def test_batch_job_incomplete(self):
        from app.models.batch_job import BatchJob
        assert BatchJob(started_at=None, finished_at=None).duration_seconds is None


class TestResolveTable:

    def test_known_types(self):
        from app.models.spatial_feature import resolve_table
        assert resolve_table("Point") == ("point_features", "POINT")
        assert resolve_table("Polygon") == ("polygon_features", "POLYGON")
        assert resolve_table("MultiLineString") == ("line_features", "MULTILINESTRING")

    def test_unknown_returns_none(self):
        from app.models.spatial_feature import resolve_table
        assert resolve_table("Bogus") is None
        assert resolve_table("") is None