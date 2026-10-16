"""临时目录安全清理模块。

进程启动时注册 atexit + signal handler，确保异常退出时清理残留临时目录。
各模块创建临时目录后应将其 Path 加入 TEMP_DIRS 集合，完成后 discard。
"""

import atexit
import logging
import shutil
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

TEMP_DIRS: set[Path] = set()


def _cleanup_temp_dirs():
    for d in list(TEMP_DIRS):
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    TEMP_DIRS.clear()


atexit.register(_cleanup_temp_dirs)


def _handle_signal(signum, frame):
    _cleanup_temp_dirs()
    raise SystemExit(128 + signum)


for _sig in (signal.SIGTERM, signal.SIGINT):
    try:
        signal.signal(_sig, _handle_signal)
    except (OSError, ValueError):
        pass  # Windows 或非主线程可能不支持
