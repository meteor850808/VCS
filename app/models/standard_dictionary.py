"""标准字典模型 — 支持标准级和全局共享。

字典用于定义字段的值域，支持层级结构。
scope='standard' 表示标准级字典，scope='global' 表示全局共享字典。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardDictionary(Base):
    """标准字典 — 定义字段的值域。"""
    __tablename__ = "standard_dictionaries"
    __table_args__ = (
        UniqueConstraint("standard_id", "dict_name", name="uq_sd_standard_name"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    standard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="所属标准 ID，NULL 表示全局字典"
    )
    api_key_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    dict_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dict_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard",
        comment="standard (标准级) / global (全局共享)"
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="系统预置字典不可删除"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    standard: Mapped["DataStandard | None"] = relationship(
        "DataStandard", back_populates="standard_dictionaries", lazy="selectin"
    )
    items: Mapped[list["DictionaryItem"]] = relationship(
        "DictionaryItem", back_populates="dictionary",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="DictionaryItem.ordering"
    )
    field_links: Mapped[list["StandardFieldDictLink"]] = relationship(
        "StandardFieldDictLink", back_populates="dictionary",
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_global(self) -> bool:
        """是否为全局共享字典。"""
        return self.scope == "global"

    @property
    def item_count(self) -> int:
        """字典条目数量。"""
        return len(self.items)
