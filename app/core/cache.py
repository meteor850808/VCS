"""Redis 缓存装饰器 — 热点查询结果自动缓存。

用法:
    from app.core.cache import cached

    @cached(ttl=300, prefix="capabilities")
    async def get_capabilities() -> dict: ...

自动序列化/反序列化 JSON，Cache Miss 时执行原函数并写回。
"""

# 注解延迟求值：redis.asyncio 缺失时 aioredis=None，
# 若不延迟求值，模块加载期 `aioredis.Redis` 注解会抛 AttributeError，
# 使下方 ImportError 优雅降级（aioredis=None）永远走不到。
from __future__ import annotations

import asyncio
import functools
import json
import hashlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from app.config import settings

_cache_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis | None:
    global _cache_client
    if _cache_client is not None:
        try:
            await _cache_client.ping()
            return _cache_client
        except Exception:
            _cache_client = None

    if aioredis is None:
        return None

    try:
        # protocol=2: 兼容老版本 Redis（<6 不支持 RESP3/HELLO，如 Windows 移植版 3.x）
        _cache_client = aioredis.from_url(
            settings.redis_url,
            socket_timeout=2,
            max_connections=10,
            socket_keepalive=True,
            protocol=2,
        )
        return _cache_client
    except Exception:
        return None


def redis_available() -> bool:
    """同步探测 Redis 是否可用（2s 超时）。

    供提交转换/批量任务的端点使用：Celery apply_async 前先探活，
    避免 Redis 不可用时 kombu 对 broker/result backend 的重试连接
    挂起请求数分钟。不可用则降级为当前进程内同步执行。
    """
    try:
        import redis
        # protocol=2: 兼容老版本 Redis（<6 不支持 RESP3/HELLO，如 Windows 移植版 3.x）
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2, protocol=2)
        return bool(r.ping())
    except Exception:
        return False


def cached(ttl: int = 300, prefix: str = "cache"):
    """异步函数 Redis 缓存装饰器。

    ttl: 缓存过期秒数（默认 5 分钟）
    prefix: 缓存键前缀（如 "capabilities"）
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            r = await get_redis()
            if r is None:
                return await func(*args, **kwargs)

            cache_key = _make_key(prefix, func.__name__, args, kwargs)
            try:
                cached_data = await r.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as e:
                logger.debug("cache GET failed for %s: %s", cache_key, e)

            result = await func(*args, **kwargs)
            try:
                await r.set(cache_key, json.dumps(result, default=str), ex=ttl)
            except Exception as e:
                logger.debug("cache SET failed for %s: %s", cache_key, e)

            return result
        return wrapper
    return decorator


def _make_key(prefix: str, func_name: str, args: tuple, kwargs: dict) -> str:
    """生成确定性缓存键。"""
    raw = f"{prefix}:{func_name}:{args}:{sorted(kwargs.items())}"
    return f"{prefix}:{hashlib.md5(raw.encode()).hexdigest()}"
