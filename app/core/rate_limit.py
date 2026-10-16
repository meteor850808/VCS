"""速率限制配置 — 基于 slowapi 令牌桶算法。

端点分级：
- strict (5/min)  — 数据上传（写入密集型）
- standard (10/min) — 格式转换
- moderate (20/min) — 格式检测
- relaxed (60/min) — 查询/列表
"""

from fastapi import Request
from slowapi import Limiter


def _get_client_ip(request: Request) -> str:
    """从请求中获取真实客户端 IP（支持 X-Forwarded-For 代理场景）。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For: client, proxy1, proxy2 → 取第一个
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


# 全局限流器实例
limiter = Limiter(
    key_func=_get_client_ip,
    default_limits=["100/minute"],
    headers_enabled=True,   # 响应头中暴露 X-RateLimit-*
)
