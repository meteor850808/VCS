"""标准字段模型 — 合并原 DataStandardField + SourceField。

字段属于数据表，可关联字典形成值域控制。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardField(Base):
    """标准字段 — 数据表的字段定义。"""
    __tablename__ = "standard_fields"
    __table_args__ = (
        UniqueConstraint("table_id", "field_name", name="uq_sf_table_field"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    table_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_tables.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    standard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(256), nullable=False)
    field_alias: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="中文名，如 '控制点名称'"
    )
    data_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="string",
        comment="string/integer/float/date/datetime/geometry/boolean"
    )
    length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    precision: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="小数精度，如 float(12,3)"
    )
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    table: Mapped["StandardTable"] = relationship(
        "StandardTable", back_populates="fields", lazy="selectin"
    )
    standard: Mapped["DataStandard"] = relationship(
        "DataStandard", back_populates="standard_fields", lazy="selectin"
    )
    dict_links: Mapped[list["StandardFieldDictLink"]] = relationship(
        "StandardFieldDictLink", back_populates="field",
        cascade="all, delete-orphan", lazy="selectin"
    )
    quality_rules: Mapped[list["QualityRule"]] = relationship(
        "QualityRule", back_populates="standard_field", lazy="selectin"
    )
