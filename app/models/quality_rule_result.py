"""质检规则执行结果 + 违规明细 ORM 模型。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class QualityRuleResult(Base):
    """规则执行结果 — 一次质检对应多条 rule_result。"""
    __tablename__ = "quality_rule_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quality_rules.id", ondelete="SET NULL"), nullable=True
    )
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    field_name: Mapped[str] = mapped_column(String(256), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    total_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="{sample_values:[...], top_errors:[...]}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class QualityViolation(Base):
    """违规明细 — 每条违规一条记录，可溯源至具体要素。"""
    __tablename__ = "quality_violations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quality_rule_results.id", ondelete="CASCADE"), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
