"""进度追踪 — Redis Pub/Sub 实时推送任务进度。"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试连接 Redis；不可用时静默降级
_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis
            from app.config import settings
            # protocol=2: 兼容老版本 Redis（<6 不支持 RESP3/HELLO，如 Windows 移植版 3.x）
            _redis = redis.from_url(settings.redis_url, socket_timeout=2, protocol=2)
            _redis.ping()
        except Exception:
            logger.warning("Redis 不可用，进度推送已禁用")
            _redis = False
    return _redis if _redis is not False else None


def publish_progress(
    job_id: str,
    step: str,
    step_index: int,
    total_steps: int = 5,
    status: str = "running",
    message: str = "",
    batch_job_id: Optional[str] = None,
):
    """发布单任务进度到 Redis Pub/Sub。"""
    r = _get_redis()
    if not r:
        return

    payload = json.dumps({
        "type": "step_complete",
        "job_id": job_id,
        "batch_job_id": batch_job_id,
        "step": step,
        "step_index": step_index,
        "total_steps": total_steps,
        "progress": step_index / total_steps,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)

    try:
        r.publish(f"progress:job:{job_id}", payload)
        if batch_job_id:
            r.publish(f"progress:batch:{batch_job_id}", payload)
    except Exception as e:
        logger.debug(f"发布进度失败: {e}")


def publish_batch_update(
    batch_job_id: str,
    status: str,
    total: int,
    success: int,
    failed: int,
):
    """发布批量任务汇总更新。"""
    r = _get_redis()
    if not r:
        return

    payload = json.dumps({
        "type": "batch_update",
        "batch_job_id": batch_job_id,
        "status": status,
        "total_count": total,
        "success_count": success,
        "failed_count": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)

    try:
        r.publish(f"progress:batch:{batch_job_id}", payload)
    except Exception as e:
        logger.debug(f"发布批量进度失败: {e}")


async def subscribe_job_progress(job_id: str):
    """生成器：订阅单个任务的进度消息（用于 SSE）。"""
    r = _get_redis()
    if not r:
        yield json.dumps({"type": "error", "message": "Redis 不可用"})
        return

    pubsub = r.pubsub()
    try:
        pubsub.subscribe(f"progress:job:{job_id}")
        for message in pubsub.listen():
            if message['type'] == 'message':
                yield message['data']
    except GeneratorExit:
        pass
    finally:
        pubsub.unsubscribe()
        pubsub.close()
