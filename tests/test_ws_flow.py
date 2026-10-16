"""P0/P2a: WebSocket 完整 _handle_ws 流程测试 — 用 fake websocket + monkeypatch 隔离 DB/Redis。

覆盖 app/api/v1/ws.py _handle_ws 的握手与订阅各分支（不触碰真实 DB/Redis）：
- 握手超时（asyncio.wait_for TimeoutError）→ 1008 + error 消息
- 握手消息非 JSON → 1008 + error
- 缺 api_key → 1008 + error
- 无效 API Key（DB 查无）→ 1008 + error
- 鉴权通过 + Redis 不可用 → warning 消息 + 保持连接直到断开
- 完整订阅流程：握手成功 + Redis 可用 → connected + 订阅 + 消息转发 + 断开清理
- [P2a] 鉴权异常（DB 报错）→ 1011 + error
- [P2a] batch_job_id 通道路径
- [P2a] 无 job_id/batch_job_id → 错误
- [P2a] heartbeat 任务体执行
- [P2a] 消息转发异常（非 JSON data）
- [P2a] WebSocketDisconnect 异常
- [P2a] 通用 Exception 异常
"""

import asyncio
import json

import pytest
from fastapi import status

from starlette.websockets import WebSocketState

from app.config import settings
import app.api.v1.ws as ws_module
from app.api.v1.ws import _handle_ws


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeRow:
    def __init__(self, id="k-1", is_active=True):
        self.id = id
        self.is_active = is_active


class _FakeSession:
    """可编程的 async session：返回预设的 scalar 结果。"""
    def __init__(self, result_obj=None):
        self._result_obj = result_obj

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        return _FakeResult(self._result_obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


class _FakeAsyncSessionLocal:
    """可编程的 AsyncSessionLocal：__call__ 返回退出 ctx 的 fake session。"""
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


class _FakeWebSocket:
    """记录行为的 WS 桩：可编程 receive_json 序列 + 心跳循环可控。"""
    def __init__(self, received=None, disconnect_after_heartbeat=False):
        self.received = list(received or [])   # 每次 receive_json 弹出
        self.sent = []
        self.closed_with = None
        self.client_state = WebSocketState.CONNECTED
        self._disconnect_after_heartbeat = disconnect_after_heartbeat
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self.received:
            raise ValueError("no more messages")
        return self.received.pop(0)

    async def send_json(self, data):
        self.sent.append(data)
        if self._disconnect_after_heartbeat and data.get("type") == "heartbeat":
            self.client_state = WebSocketState.DISCONNECTED

    async def close(self, code=None, reason=None):
        self.closed_with = (code, reason)


def _pass_through_wait_for(monkeypatch):
    """让 asyncio.wait_for 直接透传（避免真实 10s 超时/挂起）。"""
    async def fake_wait_for(coro, timeout):
        return await coro
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)


def _patch_auth(monkeypatch, result_obj):
    """注入假 AsyncSessionLocal：execute 返回指定 scalar 结果。"""
    _pass_through_wait_for(monkeypatch)
    fake_aml = _FakeAsyncSessionLocal(_FakeSession(result_obj=result_obj))
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", fake_aml)


# ── 握手阶段各分支 ─────────────────────────────────────────

class TestHandshake:
    async def test_timeout_closes_1008(self, monkeypatch):
        # 让 asyncio.wait_for 立即超时 → 走握手超时分支
        async def fake_wait_for(coro, timeout):
            raise asyncio.TimeoutError()
        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        ws = _FakeWebSocket(received=[{"api_key": "late"}])
        await _handle_ws(ws)
        assert ws.accepted is True
        assert ws.sent[-1]["type"] == "error"
        assert "超时" in ws.sent[-1]["message"]
        assert ws.closed_with is not None and ws.closed_with[0] == 1008

    async def test_non_json_closes_1008(self, monkeypatch):
        _pass_through_wait_for(monkeypatch)

        class _BadWS(_FakeWebSocket):
            async def receive_json(self):
                raise ValueError("bad json")

        ws = _BadWS()
        await _handle_ws(ws)
        assert ws.sent[-1] == {"type": "error", "message": "握手消息必须是 JSON 格式"}
        assert ws.closed_with is not None and ws.closed_with[0] == 1008

    async def test_missing_api_key_closes_1008(self, monkeypatch):
        _pass_through_wait_for(monkeypatch)
        ws = _FakeWebSocket(received=[{"job_id": "j1"}])  # 无 api_key
        await _handle_ws(ws)
        assert ws.sent[-1]["type"] == "error"
        assert "缺少 api_key" in ws.sent[-1]["message"]
        assert ws.closed_with is not None and ws.closed_with[0] == 1008


# ── 鉴权路径 ───────────────────────────────────────────────

class TestAuth:
    async def test_invalid_key_streams_error_and_1008(self, monkeypatch):
        # 假 session 返回 None → 无有效 key
        _patch_auth(monkeypatch, result_obj=None)
        ws = _FakeWebSocket(received=[{"api_key": "bad", "job_id": "j1"}])
        await _handle_ws(ws)
        assert ws.sent[-1]["type"] == "error"
        assert "无效的 API Key" in ws.sent[-1]["message"]
        assert ws.closed_with is not None and ws.closed_with[0] == 1008

    async def test_valid_key_redis_unavailable_warning(self, monkeypatch):
        # 有效 key（返回行），但 _get_redis_pubsub 返回 None → Redis 不可用分支
        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: None)
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])

        async def fake_sleep(_):
            ws.client_state = WebSocketState.DISCONNECTED

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        await _handle_ws(ws)
        last = ws.sent[-1]
        assert last["type"] == "warning"
        assert "Redis 不可用" in last["message"]


# ── 完整订阅流程（Redis 可用） ─────────────────────────────

class TestFullFlow:
    async def test_connected_subscribe_forward(self, monkeypatch):
        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
                self.frames = [
                    {"type": "subscribe", "channel": "progress:job:j1"},
                    {"type": "message", "channel": "progress:job:j1", "data": json.dumps({"progress": 50})},
                ]

            async def subscribe(self, *channels):
                self.subscribed = list(channels)

            async def unsubscribe(self, *channels):
                self.unsubscribed = list(channels)

            def listen(self):
                async def gen():
                    for f in self.frames:
                        yield f
                return gen()

        class _FakeRedis:
            def __init__(self, pubsub):
                self._pubsub = pubsub
                self.closed = False

            def pubsub(self):
                return self._pubsub

            async def aclose(self):
                self.closed = True

        pubsub = _FakePubsub()
        redis = _FakeRedis(pubsub)
        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: redis)
        # 心跳任务会在 finally 中被 cancel（主 async-for 仅两帧，很快结束）
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])
        await _handle_ws(ws)
        connected = [m for m in ws.sent if m.get("type") == "connected"]
        assert connected, ws.sent
        assert connected[0]["channels"] == ["progress:job:j1"]
        forwarded = [m for m in ws.sent if m.get("progress") == 50]
        assert forwarded, ws.sent
        assert pubsub.subscribed == ["progress:job:j1"]
        assert pubsub.unsubscribed == ["progress:job:j1"]
        assert redis.closed is True


# ── P2a: 补充覆盖 ws.py 剩余分支 ─────────────────────────────

class TestP2aCoverage:
    """覆盖 ws.py lines 97-100, 118-119, 126, 129-134, 148-153, 162-163, 167-170。"""

    async def test_auth_exception_1011(self, monkeypatch):
        """lines 97-100: session.execute 抛异常 → 1011 + '鉴权失败'"""
        _pass_through_wait_for(monkeypatch)

        class _BrokenSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def execute(self, *a, **kw):
                raise RuntimeError("DB connection lost")

        monkeypatch.setattr("app.db.session.AsyncSessionLocal", _FakeAsyncSessionLocal(_BrokenSession()))
        ws = _FakeWebSocket(received=[{"api_key": "key", "job_id": "j1"}])
        await _handle_ws(ws)
        assert ws.sent[-1]["type"] == "error"
        assert "鉴权失败" in ws.sent[-1]["message"]
        assert ws.closed_with[0] == status.WS_1011_INTERNAL_ERROR

    async def test_redis_unavailable_loop_exception(self, monkeypatch):
        """lines 118-119: Redis 不可用 → while 循环中异常被 except Exception 捕获后退出"""
        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: None)
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])

        call_count = 0
        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise OSError("simulated socket error")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        await _handle_ws(ws)
        assert call_count >= 2  # 循环体至少执行了2次后异常退出

    async def test_batch_job_id_channel(self, monkeypatch):
        """line 126: batch_job_id 路径 → 订阅 progress:batch:{batch_job_id}"""
        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
                self.frames = [
                    {"type": "subscribe", "channel": "progress:batch:b1"},
                    {"type": "message", "channel": "progress:batch:b1", "data": json.dumps({"done": True})},
                ]
            async def subscribe(self, *ch):
                self.subscribed = list(ch)
            async def unsubscribe(self, *ch):
                self.unsubscribed = list(ch)
            def listen(self):
                async def gen():
                    for f in self.frames:
                        yield f
                return gen()

        class _FakeRedis:
            def __init__(self, ps):
                self._ps = ps
            def pubsub(self):
                return self._ps
            async def aclose(self):
                pass

        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: _FakeRedis(_FakePubsub()))
        ws = _FakeWebSocket(received=[{"api_key": "valid", "batch_job_id": "b1"}])
        await _handle_ws(ws)
        connected = [m for m in ws.sent if m.get("type") == "connected"]
        assert connected[0]["channels"] == ["progress:batch:b1"]

    async def test_no_channels_error(self, monkeypatch):
        """lines 129-134: 无 job_id 也无 batch_job_id → 错误 + close"""
        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: object())
        ws = _FakeWebSocket(received=[{"api_key": "valid"}])
        await _handle_ws(ws)
        assert ws.sent[-1]["type"] == "error"
        assert "job_id" in ws.sent[-1]["message"]
        assert ws.closed_with is not None

    async def test_heartbeat_exception_breaks(self, monkeypatch):
        """lines 152-153: heartbeat send_json 抛异常 → except Exception: break"""
        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
            async def subscribe(self, *ch):
                self.subscribed = list(ch)
            async def unsubscribe(self, *ch):
                self.unsubscribed = list(ch)
            def listen(self):
                async def gen():
                    yield {"type": "subscribe", "channel": "progress:job:j1"}
                    yield {"type": "message", "channel": "progress:job:j1", "data": json.dumps({"pct": 10})}
                    await asyncio.sleep(0.05)
                return gen()

        class _FakeRedis:
            def __init__(self, ps):
                self._ps = ps
            def pubsub(self):
                return self._ps
            async def aclose(self):
                pass

        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: _FakeRedis(_FakePubsub()))
        monkeypatch.setattr(settings, "ws_heartbeat_interval", 0.01)

        send_count = 0
        original_ws_class = _FakeWebSocket
        class _FailingSendWS(_FakeWebSocket):
            async def send_json(self, data):
                nonlocal send_count
                if data.get("type") == "heartbeat":
                    send_count += 1
                    if send_count >= 2:
                        raise OSError("send failed")
                await super().send_json(data)

        ws = _FailingSendWS(received=[{"api_key": "valid", "job_id": "j1"}])
        await _handle_ws(ws)
        # heartbeat 应发送过至少1次，然后异常 break
        assert send_count >= 1

    async def test_message_forwarding_exception(self, monkeypatch):
        """lines 162-163: message.data 不是合法 JSON → 异常被静默吞掉"""
        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
            async def subscribe(self, *ch):
                self.subscribed = list(ch)
            async def unsubscribe(self, *ch):
                self.unsubscribed = list(ch)
            def listen(self):
                async def gen():
                    yield {"type": "subscribe", "channel": "progress:job:j1"}
                    yield {"type": "message", "channel": "progress:job:j1", "data": b"not-json-data"}
                return gen()

        class _FakeRedis:
            def __init__(self, ps):
                self._ps = ps
            def pubsub(self):
                return self._ps
            async def aclose(self):
                pass

        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: _FakeRedis(_FakePubsub()))
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])
        await _handle_ws(ws)
        # 不应崩溃，异常被静默处理
        connected = [m for m in ws.sent if m.get("type") == "connected"]
        assert connected

    async def test_websocket_disconnect_handler(self, monkeypatch):
        """lines 167-168: pubsub.listen 中抛 WebSocketDisconnect → 日志记录"""
        from fastapi.websockets import WebSocketDisconnect

        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
            async def subscribe(self, *ch):
                self.subscribed = list(ch)
            async def unsubscribe(self, *ch):
                self.unsubscribed = list(ch)
            def listen(self):
                async def gen():
                    yield {"type": "subscribe", "channel": "progress:job:j1"}
                    raise WebSocketDisconnect(code=1000)
                return gen()

        class _FakeRedis:
            def __init__(self, ps):
                self._ps = ps
            def pubsub(self):
                return self._ps
            async def aclose(self):
                pass

        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: _FakeRedis(_FakePubsub()))
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])
        await _handle_ws(ws)
        # 不应崩溃，WebSocketDisconnect 被捕获
        connected = [m for m in ws.sent if m.get("type") == "connected"]
        assert connected

    async def test_generic_exception_handler(self, monkeypatch):
        """lines 169-170: pubsub.listen 中抛 RuntimeError → 日志记录"""
        class _FakePubsub:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
            async def subscribe(self, *ch):
                self.subscribed = list(ch)
            async def unsubscribe(self, *ch):
                self.unsubscribed = list(ch)
            def listen(self):
                async def gen():
                    yield {"type": "subscribe", "channel": "progress:job:j1"}
                    raise RuntimeError("Redis connection lost")
                return gen()

        class _FakeRedis:
            def __init__(self, ps):
                self._ps = ps
            def pubsub(self):
                return self._ps
            async def aclose(self):
                pass

        _patch_auth(monkeypatch, result_obj=_FakeRow())
        monkeypatch.setattr(ws_module, "_get_redis_pubsub", lambda: _FakeRedis(_FakePubsub()))
        ws = _FakeWebSocket(received=[{"api_key": "valid", "job_id": "j1"}])
        await _handle_ws(ws)
        # 不应崩溃，异常被日志记录
        connected = [m for m in ws.sent if m.get("type") == "connected"]
        assert connected