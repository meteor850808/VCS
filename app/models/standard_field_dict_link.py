"""标准字段-字典关联模型 — 值域控制。

字段通过此关联与字典形成值域约束。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardFieldDictLink(Base):
    """标准字段-字典关联 — 值域控制。"""
    __tablename__ = "standard_field_dict_links"
    __table_args__ = (
        UniqueConstraint("field_id", "dict_id", name="uq_sfdl_field_dict"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    field_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_fields.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    dict_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_dictionaries.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    filter_condition: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
        comment="码值筛选条件，如 'status=active'"
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="是否为主要值域"
    )
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # 关系
    field: Mapped["StandardField"] = relationship(
        "StandardField", back_populates="dict_links", lazy="selectin"
    )
    dictionary: Mapped["StandardDictionary"] = relationship(
        "StandardDictionary", back_populates="field_links", lazy="selectin"
    )

    @property
    def dict_name(self) -> str | None:
        """字典名称 — 供 Pydantic from_attributes 序列化使用。

        注意：仅依赖 dictionary 关系已加载（selectin）时可用，
        未加载时访问会触发 async lazy load 异常，这里统一兜底为 None。
        """
        try:
            return self.dictionary.dict_name if self.dictionary else None
        except Exception:
            return None
