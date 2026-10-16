"""FastAPI 应用入口。"""

import os
from contextlib import asynccontextmanager

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.v1.router import router
from app.config import settings
from app.core.logging_setup import setup_logging, logger
from app.core.rate_limit import limiter
from app.core.trace_middleware import TraceIDMiddleware
from app.core.security_middleware import SecurityHeadersMiddleware
from app.db.session import engine, AsyncSessionLocal
from app.schemas.common import HealthResponse

# ── 结构化日志（替代 logging.basicConfig）────────────────────────────────────────
setup_logging()

# ── Prometheus 指标 ──────────────────────────────────────────────────────────
# 条件导入：prometheus-fastapi-instrumentator 为可选依赖
_prometheus_available = False
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    _prometheus_available = True
except ImportError:
    pass

# 注解字符串化：redis 可选依赖缺失（aioredis=None）时避免模块级注解求值崩溃
_redis_client: "aioredis.Redis | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_client
    try:
        # protocol=2: 兼容老版本 Redis（<6 不支持 RESP3/HELLO，如 Windows 移植版 3.x）
        _redis_client = aioredis.from_url(
            settings.redis_url, socket_timeout=2,
            max_connections=5, socket_keepalive=True,
            protocol=2,
        )
    except Exception:
        logger.warning("Redis 不可用，健康检查将报告 degraded")
    logger.info("数据库连接就绪")
    yield
    if _redis_client:
        await _redis_client.aclose()
    await engine.dispose()
    logger.info("数据库连接已关闭")


app = FastAPI(
    title="矢量转换服务 (Vector Conversion Service)",
    description="支持 SHP / GeoJSON / KML / DXF 等格式互转，自动 CRS 坐标变换、几何修复与 ogr2ogr 脚本生成。",
    version="2.24.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── 速率限制 ──────────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── 请求追踪 ──────────────────────────────────────────────────────────────────
app.add_middleware(TraceIDMiddleware)

# ── 安全响应头 ────────────────────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

# ── GZip 压缩（减少响应体积）─────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS（通过 settings.cors_origins 配置，默认允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router)

# ── Prometheus /metrics 端点 ─────────────────────────────────────────────────
if _prometheus_available:
    Instrumentator().instrument(app).expose(app, tags=["system"])
    logger.info("prometheus_metrics_enabled", endpoint="/metrics")

# 挂载前端 SPA
frontend_dir = settings.frontend_dir
if os.path.isdir(frontend_dir):
    app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")


# ── 健康检查 ──────────────────────────────────────────────────────────────────

import time
_health_cache: dict = {"data": None, "expires_at": 0}


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    """服务健康检查（无需鉴权），结果缓存 10 秒。"""
    global _health_cache
    now = time.time()
    if _health_cache["data"] and now < _health_cache["expires_at"]:
        return _health_cache["data"]

    pg_status = "ok"
    redis_status = "ok"

    import asyncio
    try:
        # PG 不可达时默认 60s 超时会拖垮健康检查/自检监控（自检卡死根因之一），限 2s
        async with AsyncSessionLocal() as s:
            await asyncio.wait_for(s.execute(text("SELECT 1")), timeout=2)
    except Exception as e:
        pg_status = f"error: {e}"

    if _redis_client:
        try:
            await asyncio.wait_for(_redis_client.ping(), timeout=2)
        except Exception as e:
            redis_status = f"error: {e}"
    else:
        redis_status = "unavailable"

    overall = "ok" if pg_status == "ok" and redis_status == "ok" else "degraded"
    data = HealthResponse(status=overall, postgres=pg_status, redis=redis_status)
    _health_cache["data"] = data
    _health_cache["expires_at"] = now + 10
    return data


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "Vector Conversion Service", "docs": "/docs"}
