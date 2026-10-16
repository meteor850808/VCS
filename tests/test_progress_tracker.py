"""P4: 进度追踪测试 — app/services/progress_tracker.py 19% → 100%。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app.services.progress_tracker as pt


class _FakeRedis:
    def __init__(self):
        self.published = []
        self._pubsub = None

    def ping(self):
        return True

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def pubsub(self):
        if self._pubsub is None:
            self._pubsub = _FakePubsub()
        return self._pubsub


class _FakePubsub:
    def __init__(self):
        self.subscribed = None
        self.unsubscribed = False
        self.closed = False
        self.frames = [
            {"type": "subscribe", "channel": "progress:job:j1"},
            {"type": "message", "channel": "progress:job:j1", "data": b'{"progress": 50}'},
        ]

    def subscribe(self, channel):
        self.subscribed = channel

    def unsubscribe(self):
        self.unsubscribed = True

    def close(self):
        self.closed = True

    def listen(self):
        for f in self.frames:
            yield f


def _reset_redis():
    pt._redis = None


class TestGetRedis:
    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_redis()
        yield
        _reset_redis()

    def test_redis_available(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        r = pt._get_redis()
        assert r is fake

    def test_redis_unavailable_logs_and_none(self, monkeypatch, caplog):
        import builtins
        import logging

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "redis":
                raise ImportError("no redis module")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level(logging.WARNING):
            assert pt._get_redis() is None
        assert "Redis 不可用" in caplog.text

        # 再次调用不重复执行逻辑（缓存 False）
        pt._redis = False
        assert pt._get_redis() is None


class TestPublishProgress:
    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_redis()
        yield
        _reset_redis()

    def test_publish_no_redis(self, monkeypatch):
        monkeypatch.setattr(pt, "_get_redis", lambda: None)
        # 不崩溃即可
        pt.publish_progress("j1", "step1", 1)
        pt.publish_batch_update("b1", "running", 3, 1, 0)

    def test_publish_job_only(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        pt.publish_progress("j1", "parse", 2, total_steps=5)
        assert len(fake.published) == 1
        channel, payload = fake.published[0]
        assert channel == "progress:job:j1"
        data = json.loads(payload)
        assert data["type"] == "step_complete"
        assert data["job_id"] == "j1"
        assert data["step"] == "parse"
        assert data["step_index"] == 2
        assert data["progress"] == pytest.approx(0.4)
        assert data["total_steps"] == 5

    def test_publish_job_and_batch(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        pt.publish_progress(
            "j1", "convert", 1, batch_job_id="b1", status="running", message="转换中"
        )
        assert len(fake.published) == 2
        channels = [c for c, _ in fake.published]
        assert channels == ["progress:job:j1", "progress:batch:b1"]
        # 两通道 payload 一致
        assert fake.published[0][1] == fake.published[1][1]

    def test_publish_exception_logged(self, monkeypatch, caplog):
        import logging

        class _BoomRedis:
            def publish(self, channel, payload):
                raise ConnectionError("down")

        monkeypatch.setattr(pt, "_get_redis", lambda: _BoomRedis())
        with caplog.at_level(logging.DEBUG):
            pt.publish_progress("j1", "x", 1)
        assert "发布进度失败" in caplog.text


class TestPublishBatchUpdate:
    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_redis()
        yield
        _reset_redis()

    def test_publish(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        pt.publish_batch_update("b1", "success", total=10, success=8, failed=2)
        assert len(fake.published) == 1
        channel, payload = fake.published[0]
        assert channel == "progress:batch:b1"
        data = json.loads(payload)
        assert data["type"] == "batch_update"
        assert data["total_count"] == 10
        assert data["success_count"] == 8
        assert data["failed_count"] == 2

    def test_exception_logged(self, monkeypatch, caplog):
        import logging

        class _BoomRedis:
            def publish(self, channel, payload):
                raise ConnectionError("down")

        monkeypatch.setattr(pt, "_get_redis", lambda: _BoomRedis())
        with caplog.at_level(logging.DEBUG):
            pt.publish_batch_update("b1", "running", 1, 0, 0)
        assert "发布批量进度失败" in caplog.text


class TestSubscribeJobProgress:
    @pytest.fixture(autouse=True)
    def reset(self):
        _reset_redis()
        yield
        _reset_redis()

    async def test_no_redis_yields_error(self, monkeypatch):
        monkeypatch.setattr(pt, "_get_redis", lambda: None)
        gen = pt.subscribe_job_progress("j1")
        msg = await gen.__anext__()
        data = json.loads(msg)
        assert data["type"] == "error"
        assert "Redis 不可用" in data["message"]
        # 生成器返回后再次迭代 → StopAsyncIteration
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    async def test_forward_and_cleanup(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        gen = pt.subscribe_job_progress("j1")
        msgs = []
        async for m in gen:
            msgs.append(m)
        assert len(msgs) == 1  # 仅 message 帧被转发
        assert b'"progress": 50' in msgs[0]
        pubsub = fake.pubsub()
        assert pubsub.subscribed == "progress:job:j1"
        assert pubsub.unsubscribed is True
        assert pubsub.closed is True

    async def test_consumer_closed_early_cleans_up(self, monkeypatch):
        """生成器被提前关闭（GeneratorExit）时仍执行清理"""
        fake = _FakeRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        gen = pt.subscribe_job_progress("j1")
        await gen.asend(None)
        await gen.aclose()
        pubsub = fake.pubsub()
        assert pubsub.unsubscribed is True
        assert pubsub.closed is True

    async def test_listen_exception_cleans_up(self, monkeypatch):
        """listen() 异常向上传播，但 finally 仍完成清理"""

        class _BoomPubsub(_FakePubsub):
            def listen(self):
                raise ConnectionError("listen down")
                yield  # pragma: no cover

        class _BoomRedis(_FakeRedis):
            def pubsub(self):
                if self._pubsub is None:
                    self._pubsub = _BoomPubsub()
                return self._pubsub

        fake = _BoomRedis()
        monkeypatch.setattr(pt, "_get_redis", lambda: fake)
        gen = pt.subscribe_job_progress("j1")
        with pytest.raises(ConnectionError):
            async for m in gen:
                pass  # pragma: no cover
        pubsub = fake.pubsub()
        assert pubsub.unsubscribed is True
        assert pubsub.closed is True