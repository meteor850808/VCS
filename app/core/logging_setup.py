"""结构化日志配置 — 基于 structlog，输出 JSON 行供 ELK/Loki 解析。

用法:
    from app.core.logging import logger
    logger.info("task_started", job_id=job_id, src_format="shp")

在请求处理中，log_ctx 会自动注入 trace_id。

v2.20 起：应用根 logger 装配 RotatingFileHandler（settings.log_file 默认
./data/logs/vcs.log，10MB × 5 份滚动），避免 server_out.log 无限增长。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from app.config import settings


def _ensure_file_handler():
    """幂等地把 RotatingFileHandler 装配到根 logger。

    重复调用（--reload 重载、lifespan 重启）不会叠加处理器。
    写盘失败时优雅降级为仅控制台，不阻断启动。
    """
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler):
            return h
    if not settings.log_file:
        return None
    try:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            str(path),
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        root.addHandler(fh)
        return fh
    except OSError:
        return None


def setup_logging():
    """初始化结构化日志。在应用启动时调用一次（lifespan 开头）。"""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    # 抑制第三方库的 debug 噪音
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    _ensure_file_handler()


def get_logger(name: str | None = None):
    return structlog.get_logger(name or __name__)


logger = structlog.get_logger("vcs")
