"""pytest 共享测试基础设施（conftest）。

组合修复（2026-09-01）：
- 方案 A：`app.dependency_overrides[get_db]` 从 test_api.py 模块顶层副作用
  移入 `test_db` fixture（yield 后自动清除），消除对全局 app 的 import 污染。
- 方案 B：`_test_engine` / `_TestSession` / `test_db` / `client` / `api_key_header`
  共享至此，integration 测试可显式引用同一 vcs_test 库。

配套：pytest.ini 已设 `asyncio_default_test_loop_scope=session`，
保证模块级 async engine 连接池与测试运行在同一个事件循环。
"""

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.db.session import Base, get_db
from app import models  # noqa: F401  导入全部 ORM 模型注册到 Base.metadata

# ── 测试数据库（独立测试库 vcs_test，需 PostGIS） ──────────────────────────────
TEST_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vcs_test"
_test_engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
_TestSession = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    """FastAPI 依赖覆盖：测试请求全部走 vcs_test。"""
    async with _TestSession() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(scope="session")
async def test_db():
    """session 级测试库：建表 → 测试 → 删表；yield 后清除 override，不污染其他测试文件。

    注意：非 autouse。只有显式引用（或 test_api.py 顶部 autouse 包装）才会生效，
    integration 的同步 TestClient 测试因此保持连接真实 app 配置，互不干扰。
    """
    app.dependency_overrides[get_db] = override_get_db
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def api_key_header(test_db):
    """创建测试用 API Key，返回 Header dict（依赖 test_db 保证表存在）。"""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    raw, hashed = generate_api_key()
    async with _TestSession() as session:
        key = ApiKey(key_hash=hashed, name="test_key", is_active=True)
        session.add(key)
        await session.commit()
    return {"X-API-Key": raw}


@pytest_asyncio.fixture
async def db_session(test_db):
    """直接访问测试库的会话（用于插入/断言测试数据）。"""
    async with _TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def admin_init_header():
    from app.config import settings
    return {"X-Admin-Init-Key": settings.admin_init_key}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c