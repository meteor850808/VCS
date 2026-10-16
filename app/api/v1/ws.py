"""WebSocket 实时进度推送（API Key 通过首条握手消息传递）。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ── WebSocket 连接数限制 ──────────────────────────────────────────────────────
_active_connections: int = 0
MAX_WS_CONNECTIONS: int = getattr(settings, "ws_max_connections", 50)


def _get_redis_pubsub():
    try:
        import redis.asyncio as aioredis
        # protocol=2: 兼容老版本 Redis（<6 不支持 RESP3/HELLO，如 Windows 移植版 3.x）
        return aioredis.from_url(settings.redis_url, socket_timeout=3, protocol=2)
    except Exception:
        return None


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """
    实时任务进度 WebSocket。

    连接后客户端需发送首条 JSON 消息进行握手鉴权:
      {"api_key": "xxx", "job_id": "xxx"}
      {"api_key": "xxx", "batch_job_id": "xxx"}

    连接: ws://localhost:8000/api/v1/ws/progress
    """
    global _active_connections

    # 检查连接数上限
    if _active_connections >= MAX_WS_CONNECTIONS:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="连接数已达上限")
        return

    _active_connections += 1
    try:
        await _handle_ws(websocket)
    finally:
        _active_connections -= 1


async def _handle_ws(websocket: WebSocket):
    """实际 WebSocket 处理逻辑（与连接计数解耦）。"""
    await websocket.accept()

    # ── 握手鉴权：等待客户端第一条消息 ──────────────────────────────────────
    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "message": "握手超时（10 秒内未发送 api_key）"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except Exception:
        await websocket.send_json({"type": "error", "message": "握手消息必须是 JSON 格式"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    api_key_raw = data.get("api_key")
    if not api_key_raw:
        await websocket.send_json({"type": "error", "message": "握手消息中缺少 api_key"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 鉴权（O(1) 索引查询）
    try:
        from app.db.session import AsyncSessionLocal
        from sqlalchemy import select
        from app.core.security import hash_api_key
        from app.models.api_key import ApiKey
        async with AsyncSessionLocal() as session:
            key_hash = hash_api_key(api_key_raw)
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.key_hash == key_hash,
                    ApiKey.is_active == True,
                )
            )
            valid = result.scalar_one_or_none() is not None
            if not valid:
                await websocket.send_json({"type": "error", "message": "无效的 API Key"})
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"鉴权失败: {e}"})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # 从握手消息中提取过滤参数
    job_id = data.get("job_id")
    batch_job_id = data.get("batch_job_id")

    # 连接 Redis Pub/Sub
    r = _get_redis_pubsub()
    if not r:
        await websocket.send_json({
            "type": "warning",
            "message": "Redis 不可用，实时进度已禁用",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # 保持连接直到客户端断开；周期检测状态，避免无限挂起占用任务资源
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(1)
        except Exception:
            pass
        return

    channels = []
    if job_id:
        channels.append(f"progress:job:{job_id}")
    if batch_job_id:
        channels.append(f"progress:batch:{batch_job_id}")

    if not channels:
        await websocket.send_json({
            "type": "error",
            "message": "请提供 job_id 或 batch_job_id",
        })
        await websocket.close()
        return

    pubsub = r.pubsub()
    heartbeat_task = None  # 确保 finally 中不会 NameError
    try:
        await pubsub.subscribe(*channels)
        await websocket.send_json({
            "type": "connected",
            "channels": channels,
            "message": "已连接进度频道",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        async def heartbeat():
            while True:
                await asyncio.sleep(settings.ws_heartbeat_interval)
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        async for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    payload = json.loads(message['data'])
                    await websocket.send_json(payload)
                except Exception as e:
                    logger.warning("WS 消息转发失败: %s", e)
            if message['type'] == 'subscribe':
                continue

    except WebSocketDisconnect:
        logger.info("WebSocket 客户端断开")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        await pubsub.unsubscribe(*channels)
        await r.aclose()
