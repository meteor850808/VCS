"""每日质量快照 ORM 模型 — 趋势分析用。

v2.1 变更：
- 添加外键约束 (api_key_id → api_keys.id)
- 添加 updated_at 审计字段
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class QualityDailySnapshot(Base):
    """每日质量快照，记录某天的全库质量汇总。"""
    __tablename__ = "quality_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("api_key_id", "snapshot_date", name="uq_qds_api_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    uniqueness: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    consistency: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    violations_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_overall: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
