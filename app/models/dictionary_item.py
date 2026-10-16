"""字典条目模型 — 支持层级结构。

字典条目通过 parent_code 支持层级结构。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class DictionaryItem(Base):
    """字典条目 — 每个字典的码值。"""
    __tablename__ = "dictionary_items"
    __table_args__ = (
        UniqueConstraint("dict_id", "item_code", name="uq_di_dict_code"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    dict_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_dictionaries.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="父级码值，支持层级结构"
    )
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
    dictionary: Mapped["StandardDictionary"] = relationship(
        "StandardDictionary", back_populates="items", lazy="selectin"
    )
