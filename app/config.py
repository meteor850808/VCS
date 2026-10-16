"""应用配置（从环境变量加载）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

# 已知弱口令引导密钥：任何部署环境都不允许使用（v2.15.1 安全加固）
_WEAK_ADMIN_INIT_KEYS = {"", "admin-init-change-me", "change-me", "changeme", "admin", "test"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── 日志 ──────────────────────────────
    log_level: str = "INFO"
    debug: bool = False
    # v2.20: 日志文件轮转（应用根 logger 装配 RotatingFileHandler；空串=仅控制台不写盘）
    log_file: str = "./data/logs/vcs.log"
    log_max_bytes: int = 10 * 1024 * 1024   # 单文件轮转阈值（10MB）
    log_backup_count: int = 5               # 滚动保留备份份数

    # ── 数据库 ────────────────────────────
    # 默认 PG 连接（通过 .env 覆盖）；SQLite 仅用于测试
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vcs_db"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/vcs_db"
    # ── 连接池（单机部署，PostgreSQL max_connections=100）
    # 异步引擎：主 API 请求 + OBJ/SHP/GeoJSON 子任务（并发写入）
    db_pool_size: int = 5
    db_max_overflow: int = 15
    db_pool_recycle: int = 1800
    db_pool_pre_ping: bool = True
    db_pool_timeout: int = 30
    # 子任务并发上限（asyncio.Semaphore，防止连接池被单次上传撑满）
    max_concurrent_db_tasks: int = 5

    # ── Redis / Celery ────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── 安全 ──────────────────────────────
    # (v2.20 弃用标注) secret_key 自 v2.1 起全仓零引用——认证走 API Key SHA-256 哈希
    # + admin_init_key 引导，无需签名密钥。保留字段仅为兼容旧 .env（pydantic-settings
    # extra_forbidden 严格模式，删除会让带 SECRET_KEY 的存量环境启动失败）。
    # 新部署勿再配置；欠债见 BASELINE §17 C2。
    secret_key: str = "change-me-in-production"
    # 引导密钥强制要求：无默认值（""），缺失或弱口令时启动直接失败。
    # 环境变量名是 ADMIN_INIT_KEY（注意不是 API_KEY_INIT）。
    admin_init_key: str = ""
    cors_origins: list[str] = ["*"]

    @field_validator("admin_init_key")
    @classmethod
    def _admin_init_key_must_be_strong(cls, v: str) -> str:
        if v.strip() in _WEAK_ADMIN_INIT_KEYS:
            raise ValueError(
                "ADMIN_INIT_KEY 未配置或仍为已知弱口令，拒绝启动。"
                "请在 .env 中设置足够强度的 ADMIN_INIT_KEY（随机 24+ 字符）。"
            )
        return v

    # ── 上传 / 存储 ──────────────────────
    max_upload_size_mb: int = 500
    max_data_store_size_mb: int = 200
    max_single_file_size_mb: int = 100       # 单文件 BYTEA 存储上限（MB），超过则拒绝（仅非压缩包）
    max_archive_size_mb: int = 500           # 压缩包上传总大小上限（MB）— 压缩包不存 BYTEA，仅受内存/解压上限约束
    file_chunk_size_mb: int = 10             # 分片上传每片大小（MB）
    enable_file_dedup: bool = True           # 启用文件哈希去重
    max_batch_size: int = 50
    upload_dir: str = "./data/uploads"
    output_dir: str = "./data/outputs"
    quality_report_dir: str = "./data/quality_reports"
    frontend_dir: str = "app/frontend"
    sandbox_limit: int = 1000
    # v2.10：移除 max_features_per_upload（要素流式全量入库，无单文件截断上限）

    # ── WebSocket ─────────────────────────
    ws_heartbeat_interval: int = 30


settings = Settings()
