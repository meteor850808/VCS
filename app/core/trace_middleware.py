"""请求追踪中间件 — 为每个请求生成 trace_id 并注入日志上下文。"""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import structlog


class TraceIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求生成唯一的 trace_id，注入 structlog 上下文和响应头。

    下游调用时可通过 X-Trace-ID 请求头接力追踪。
    """

    async def dispatch(self, request: Request, call_next):
        # 尝试从上游获取 trace_id（如 Nginx / API Gateway）
        trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())

        # 注入 structlog 上下文
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        response = await call_next(request)

        # 暴露给前端 / 下游
        response.headers["X-Trace-ID"] = trace_id
        structlog.contextvars.clear_contextvars()

        return response
