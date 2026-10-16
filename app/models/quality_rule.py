"""质检规则 ORM 模型 — 从数据标准字段自动/手动生成的质检规则。

v2.2 变更：
- standard_field_id 指向新的 standard_fields 表
- 添加与 StandardField 的关系
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class QualityRule(Base):
    __tablename__ = "quality_rules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id"), nullable=False, index=True
    )
    standard_field_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("standard_fields.id", ondelete="CASCADE"), nullable=True
    )
    rule_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="NOT_NULL / UNIQUE / VALUE_IN_SET / TYPE_MATCH / LENGTH_LIMIT / PATTERN_MATCH / RANGE_CHECK"
    )
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False)
    field_pattern: Mapped[str] = mapped_column(
        String(256), nullable=False, default="",
        comment="匹配字段名（支持通配符 * 和 ?）"
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="warning"
    )
    params: Mapped[dict | None] = mapped_column(
        JSON, nullable=True,
        comment="{allowed_values:[...], max_length:100, min:0, max:999, pattern:...}"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="True=从数据标准自动生成, False=用户手动创建"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    standard_field: Mapped["StandardField | None"] = relationship(
        "StandardField", back_populates="quality_rules", lazy="selectin"
    )
