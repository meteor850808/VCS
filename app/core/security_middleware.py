"""安全中间件 — 注入响应头 + 请求体大小限制。"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi import HTTPException, status

from app.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为每个响应注入安全相关 HTTP 头。"""

    async def dispatch(self, request: Request, call_next):
        # ── 请求体大小限制 ────────────────────────────────────────────────────
        content_length = request.headers.get("content-length")
        if content_length:
            max_bytes = settings.max_upload_size_mb * 1024 * 1024
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"请求体超过限制 {settings.max_upload_size_mb} MB",
                )

        response = await call_next(request)

        # ── 安全响应头 ────────────────────────────────────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
