"""Celery Beat 定时任务 — 文件清理与维护。

通过 Celery Beat 调度执行:
    celery -A app.workers.celery_app beat -s celerybeat-schedule
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import shared_task

from app.config import settings

logger = logging.getLogger(__name__)


@shared_task(name="vcs.cleanup_temp_files")
def cleanup_temp_files():
    """清理 7 天前的临时上传和输出文件。"""
    cutoff = time.time() - 7 * 24 * 3600
    cleaned = 0

    for dir_name in ("uploads", "outputs"):
        base = Path(settings.upload_dir).parent / dir_name
        if not base.exists():
            continue
        for entry in base.iterdir():
            try:
                entry_is_dir = entry.is_dir()
            except OSError:
                # Path.is_dir() 只吞 ENOENT/ENOTDIR/EBADF，EACCES 等会抛出 →
                # 无法 stat 的条目直接跳过，避免整个清理任务崩溃
                continue
            if entry_is_dir:
                try:
                    mtime = entry.stat().st_mtime
                    if mtime < cutoff:
                        import shutil
                        shutil.rmtree(entry, ignore_errors=True)
                        cleaned += 1
                except OSError:
                    pass

    logger.info("cleanup_completed", dirs_removed=cleaned)
    return {"cleaned_dirs": cleaned}


@shared_task(name="vcs.metrics_snapshot")
def metrics_snapshot():
    """每小时记录一次关键指标快照（供无 Prometheus 环境参考）。"""
    from pathlib import Path
    from app.config import settings

    data_dir = Path(settings.upload_dir).parent
    upload_count = len(list((data_dir / "uploads").glob("*"))) if (data_dir / "uploads").exists() else 0
    output_count = len(list((data_dir / "outputs").glob("*"))) if (data_dir / "outputs").exists() else 0

    logger.info(
        "metrics_snapshot",
        upload_dirs=upload_count,
        output_dirs=output_count,
    )
    return {"upload_dirs": upload_count, "output_dirs": output_count}


# Celery Beat 调度配置（可被 celery_app.conf.beat_schedule 覆盖）
CELERY_BEAT_SCHEDULE = {
    "cleanup-temp-files": {
        "task": "vcs.cleanup_temp_files",
        "schedule": timedelta(hours=6),
    },
    "metrics-snapshot": {
        "task": "vcs.metrics_snapshot",
        "schedule": timedelta(hours=1),
    },
}
