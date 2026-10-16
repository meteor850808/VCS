"""KeyGroup ORM 模型 — API Key 分组，同组 Key 共享数据访问权限。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class KeyGroup(Base):
    __tablename__ = "key_groups"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True,
        comment="组名称（唯一）"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship: 组内的 API Key
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="group", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<KeyGroup {self.name} ({len(self.api_keys) if self.api_keys else 0} keys)>"
