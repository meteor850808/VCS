"""FastAPI 依赖注入（鉴权等）。"""

import logging
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthContext
from app.core.security import hash_api_key
from app.db.session import get_db
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def authenticated(
    db: AsyncSession = Depends(get_db),
    api_key: str | None = Security(api_key_header),
) -> AuthContext:
    """验证 X-API-Key 并返回 AuthContext（含可访问的 key ID 列表）。

    特殊行为：
    - 如果 X-API-Key 的值 == settings.admin_init_key，则创建超级管理员
      AuthContext（可访问所有活跃 Key 的数据）
    - 否则按正常流程查 api_keys 表

    AuthContext.accessible_key_ids:
      - 超级管理员: 所有活跃 Key 的 ID
      - 无 group: [当前 key id]（仅自己）
      - 有 group: 同组所有活跃 key 的 id 列表
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-API-Key 请求头",
        )

    from app.config import settings

    # ── 超级管理员：ADMIN_INIT_KEY 可当作超级 API Key 使用 ─────────────
    if api_key == settings.admin_init_key:
        return await AuthContext.create_super_admin(db)

    key_hash = hash_api_key(api_key)

    # 尝试从 Redis 缓存读取
    try:
        from app.core.cache import get_redis
        r = await get_redis()
        if r is not None:
            cached_id = await r.get(f"apikey:{key_hash}")
            if cached_id:
                key_id = cached_id.decode() if isinstance(cached_id, bytes) else str(cached_id)
                # 缓存只存了 ID，需要查 DB 获取 group 信息
                result = await db.execute(
                    select(ApiKey).where(ApiKey.id == key_id, ApiKey.is_active == True)
                )
                key = result.scalar_one_or_none()
                if key:
                    return await AuthContext.resolve(db, key)
    except Exception:
        pass

    # 回源 DB
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active == True,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
        )

    # 异步写入缓存
    try:
        r = await get_redis()
        if r is not None:
            await r.setex(f"apikey:{key_hash}", 60, key.id.encode())
    except Exception as e:
        logger.debug("apikey cache write failed for %s: %s", key_hash, e)

    return await AuthContext.resolve(db, key)
