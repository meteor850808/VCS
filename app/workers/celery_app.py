"""Celery 实例配置。"""

from celery import Celery
from app.config import settings

# ── Redis 协议兼容（v2.10.1）──────────────────────────────────────────────────
# 老版本 Redis（<6.0，如 Windows 移植版 3.x）不支持 RESP3/HELLO 握手。
# redis-py 8.x 默认 protocol=3 会导致 "unknown command 'HELLO'"。
# ① kombu 5.6 的 redis transport 不透传 protocol 参数到 ConnectionPool，
#    故 monkey patch _connparams 强制 protocol=2（覆盖 broker 连接）。
# ② Celery 的 RedisBackend 独立创建连接池（subscribe 消费结果），其
#    connparams 同样不透传 protocol，需在 __init__ 后强制注入。
def _patch_redis_protocol_compat() -> None:
    try:
        from kombu.transport import redis as kombu_redis

        _orig_connparams = kombu_redis.Channel._connparams

        def _patched_connparams(self, asynchronous=False):
            params = _orig_connparams(self, asynchronous=asynchronous)
            params["protocol"] = 2
            return params

        kombu_redis.Channel._connparams = _patched_connparams
    except Exception:  # 环境差异下不阻塞启动
        pass

    try:
        from celery.backends.redis import RedisBackend

        _orig_init = RedisBackend.__init__

        def _patched_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            self.connparams.setdefault("protocol", 2)

        RedisBackend.__init__ = _patched_init
    except Exception:  # 环境差异下不阻塞启动
        pass


_patch_redis_protocol_compat()

celery_app = Celery(
    "vcs",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue="vector_conversion",
    # 显式声明任务模块：worker 启动时加载转换/批量任务
    # （避免 tasks.py 反向 import celery_app 造成循环依赖，故用 include 而非 import）
    include=["app.workers.tasks", "app.workers.batch_tasks"],
    # 结果过期时间（1 小时后自动清理，防止 Redis 内存泄漏）
    result_expires=3600,
    # Worker 崩溃后自动重试任务
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    # broker/result backend 连接失败不重试：Redis 未启动时 apply_async 立即抛异常
    # （否则 kombu 会按 broker_connection_max_retries 默认 100 次无限重试，
    #  导致 API 请求挂起数分钟，占住服务线程），由调用方降级为进程内执行。
    broker_connection_retry=False,
    broker_connection_max_retries=0,
    broker_transport_options={
        "max_retries": 0,
        "interval_start": 0,
        "interval_step": 0,
        "interval_max": 0,
    },
)

# 导入定时任务配置（清理 + 指标快照）
from app.workers.beat_schedule import CELERY_BEAT_SCHEDULE  # noqa: E402
celery_app.conf.beat_schedule = CELERY_BEAT_SCHEDULE
