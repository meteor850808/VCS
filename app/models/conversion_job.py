"""转换任务 ORM 模型（兼容 SQLite / PostgreSQL）。

v2.1 变更：
- 添加外键约束 (api_key_id, batch_job_id, quality_report_id)
- 添加 updated_at 审计字段
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobStatus(str, enum.Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    SUCCESS    = "success"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class RepairStrategyEnum(str, enum.Enum):
    AUTO_FIX = "auto_fix"
    ISOLATE  = "isolate"
    DROP     = "drop"


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"

    # ── 主键 & 标识 ───────────────────────────────────────────────────────────
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── 输入参数 ──────────────────────────────────────────────────────────────
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    input_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    src_crs: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dst_crs: Mapped[str] = mapped_column(String(256), nullable=False)
    output_format: Mapped[str] = mapped_column(String(64), nullable=False, default="GeoJSON")
    select_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    where_clause: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RepairStrategyEnum.AUTO_FIX
    )
    script_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ogr2ogr")
    target_encoding: Mapped[str] = mapped_column(String(32), nullable=False, default="UTF-8")

    # ── 识别结果 ──────────────────────────────────────────────────────────────
    detected_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected_encoding: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detected_crs: Mapped[str | None] = mapped_column(String(256), nullable=True)
    detection_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── 生成脚本 ──────────────────────────────────────────────────────────────
    generated_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_script_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── 执行统计 ──────────────────────────────────────────────────────────────
    feature_count_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_count_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_fixed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_dropped: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── 状态 & 日志 ───────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobStatus.PENDING, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sandbox_stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_stderr: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 时间 ──────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── 批量 & 质量关联 ─────────────────────────────────────────────────────────
    quality_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
