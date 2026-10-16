"""SQLAlchemy 会话管理 — 异步（API/服务主路径）+ 同步（后台要素入库线程）。"""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# 子任务并发上限：防止 asyncio.gather 同时打开过多 DB 会话撑满连接池
db_task_semaphore = asyncio.Semaphore(settings.max_concurrent_db_tasks)


class Base(DeclarativeBase):
    pass


# 连接池配置（仅对 PostgreSQL 生效，SQLite 忽略 pool 参数）
_engine_kwargs = {
    "echo": settings.debug,
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_max_overflow,
    "pool_recycle": settings.db_pool_recycle,
    "pool_pre_ping": settings.db_pool_pre_ping,
    "pool_timeout": settings.db_pool_timeout,
}
if settings.database_url.startswith("sqlite"):  # pragma: no cover — 测试/生产全程 PG，SQLite 仅本地脚手架
    _engine_kwargs["connect_args"] = {"check_same_thread": False}  # pragma: no cover
    # SQLite 不支持连接池
    _engine_kwargs.pop("pool_size", None)  # pragma: no cover
    _engine_kwargs.pop("max_overflow", None)  # pragma: no cover
    _engine_kwargs.pop("pool_recycle", None)  # pragma: no cover
    _engine_kwargs.pop("pool_pre_ping", None)  # pragma: no cover

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ── 同步会话（asyncio.to_thread 内使用：GDB/SHP 大规模要素入库）────────────
_sync_kwargs = {
    "echo": settings.debug,
    "pool_size": min(settings.db_pool_size, 3),  # GDB 读取有文件锁串行化，不需要大池
    "max_overflow": min(settings.db_max_overflow, 5),
    "pool_recycle": settings.db_pool_recycle,
    "pool_pre_ping": settings.db_pool_pre_ping,
    "pool_timeout": settings.db_pool_timeout,
}
if settings.database_url_sync.startswith("sqlite"):  # pragma: no cover — 同上，SQLite 仅本地脚手架
    _sync_kwargs["connect_args"] = {"check_same_thread": False}  # pragma: no cover
    _sync_kwargs.pop("pool_size", None)  # pragma: no cover
    _sync_kwargs.pop("max_overflow", None)  # pragma: no cover
    _sync_kwargs.pop("pool_recycle", None)  # pragma: no cover
    _sync_kwargs.pop("pool_pre_ping", None)  # pragma: no cover

sync_engine = create_engine(settings.database_url_sync, **_sync_kwargs)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


async def get_db():
    """异步数据库会话依赖注入。

    会话在请求结束时自动提交，异常时回滚。
    注意：对数据一致性要求高的写操作，建议在端点内显式 await session.commit()。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


