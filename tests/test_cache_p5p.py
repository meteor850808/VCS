"""P5p: core/cache.py 服务级全覆盖（46% → 100%）。

覆盖目标：22-24 ImportError、33-38 get_redis ping 成败、40-41 aioredis None、
43-54 from_url 成败、57-86 cached 装饰器全分支（r None / GET 命中/GET 失败/SET/失败）、
89-92 _make_key。get_redis 全局 _cache_client 由 autouse fixture 每次复位。
"""

import json

import pytest

import app.core.cache as cache


def test_import_error_sets_aioredis_none(monkeypatch):
    """22-24：redis.asyncio 导入失败 → aioredis=None（重载模块验证）。"""
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "redis.asyncio":
            raise ImportError("no redis.asyncio")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    # 必须同时摘除父包属性：仅 delitem sys.modules 会让 import_module 重载出的
    # 新模块残留到 app.core.cache 属性上（sys.modules 虽被 monkeypatch 还原，
    # 属性仍指向新模块 → 后续 from app.core import cache 拿到漂移对象）。
    monkeypatch.delattr(sys.modules["app.core"], "cache", raising=False)
    monkeypatch.delitem(sys.modules, "app.core.cache", raising=False)
    mod = importlib.import_module("app.core.cache")
    assert mod.aioredis is None


class FakeRedis:
    def __init__(self, get=None, set_ok=True, ping_ok=True,
                 get_raises=False, set_raises=False):
        self._get = get
        self._set_ok = set_ok
        self._ping_ok = ping_ok
        self._get_raises = get_raises
        self._set_raises = set_raises
        self.set_calls = []

    async def ping(self):
        if not self._ping_ok:
            raise ConnectionError("down")
        return True

    async def get(self, key):
        if self._get_raises:
            raise OSError("get fail")
        return self._get

    async def set(self, key, val, ex=None):
        if self._set_raises:
            raise OSError("set fail")
        self.set_calls.append((key, val, ex))
        return True


@pytest.fixture(autouse=True)
def _clean_cache_client():
    cache._cache_client = None
    yield
    cache._cache_client = None


class TestGetRedis:

    def test_ping_ok_returns_client(self):
        fake = FakeRedis(ping_ok=True)
        cache._cache_client = fake
        r = cache.get_redis()
        import asyncio
        assert asyncio.run(r) is fake

    def test_ping_fail_resets_then_none(self, monkeypatch):
        """37-38：ping 失败 → _cache_client=None；再因 aioredis None → 40-41 None。"""
        monkeypatch.setattr(cache, "aioredis", None)
        cache._cache_client = FakeRedis(ping_ok=False)
        r = cache.get_redis()
        import asyncio
        assert asyncio.run(r) is None
        assert cache._cache_client is None

    def test_aioredis_none_returns_none(self, monkeypatch):
        """40-41：_cache_client None 且 aioredis None → None。"""
        monkeypatch.setattr(cache, "aioredis", None)
        r = cache.get_redis()
        import asyncio
        assert asyncio.run(r) is None

    def test_from_url_returns_client(self, monkeypatch):
        """43-52：正常 from_url → 缓存客户端并返回。"""
        fake = FakeRedis()
        monkeypatch.setattr(cache.aioredis, "from_url",
                            lambda *a, **k: fake)
        r = cache.get_redis()
        import asyncio
        assert asyncio.run(r) is fake
        assert cache._cache_client is fake

    def test_from_url_exception_returns_none(self, monkeypatch):
        """53-54：from_url 抛错 → None。"""
        def boom(*a, **k):
            raise OSError("connect fail")
        monkeypatch.setattr(cache.aioredis, "from_url", boom)
        r = cache.get_redis()
        import asyncio
        assert asyncio.run(r) is None


class TestCached:

    async def impl(self, a, b=None):
        return {"v": a, "b": b}

    def _wrap(self, **kw):
        return cache.cached(**kw)(self.impl)

    async def test_r_none_calls_func(self, monkeypatch):
        """67-68：get_redis 返回 None → 直接执行原函数。"""
        monkeypatch.setattr(cache, "aioredis", None)
        wrapped = self._wrap(ttl=100)
        res = await wrapped(1, b=2)
        assert res == {"v": 1, "b": 2}

    def test_cache_hit(self):
        """72-74：GET 命中 → 返回反序列化 JSON。"""
        cache._cache_client = FakeRedis(get=json.dumps({"v": 9}))
        wrapped = self._wrap()
        res = wrapped(3)
        import asyncio
        assert asyncio.run(res) == {"v": 9}

    def test_get_fail_then_set(self):
        """75-76 + 78 + 80：GET 失败 → 执行函数 → SET 成功。"""
        client = FakeRedis(get_raises=True, set_ok=True)
        cache._cache_client = client
        wrapped = self._wrap(ttl=222)
        import asyncio
        res = asyncio.run(wrapped(5, b="x"))
        assert res == {"v": 5, "b": "x"}
        assert len(client.set_calls) == 1
        assert client.set_calls[0][2] == 222

    def test_set_fail(self):
        """81-82：SET 失败 → 只记录日志，返回原结果。"""
        cache._cache_client = FakeRedis(set_raises=True)
        wrapped = self._wrap()
        import asyncio
        res = asyncio.run(wrapped(7, b=None))
        assert res == {"v": 7, "b": None}

    def test_happy_no_cache(self):
        """GET 空 → 执行 → SET → 返回。"""
        client = FakeRedis(set_ok=True)
        cache._cache_client = client
        wrapped = self._wrap()
        import asyncio
        res = asyncio.run(wrapped(10, b=20))
        assert res == {"v": 10, "b": 20}
        assert len(client.set_calls) == 1


class TestMakeKey:

    def test_deterministic_same(self):
        k1 = cache._make_key("p", "f", (1, 2), {"x": 1})
        k2 = cache._make_key("p", "f", (1, 2), {"x": 1})
        assert k1 == k2 and k1.startswith("p:")

    def test_diff_args_diff_key(self):
        k1 = cache._make_key("p", "f", (1,), {})
        k2 = cache._make_key("p", "f", (2,), {})
        assert k1 != k2

    def test_kwargs_order_insensitive(self):
        k1 = cache._make_key("p", "f", (), {"a": 1, "b": 2})
        k2 = cache._make_key("p", "f", (), {"b": 2, "a": 1})
        assert k1 == k2