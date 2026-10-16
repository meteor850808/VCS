"""API Key ORM 模型。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Group 支持 ──────────────────────────────────────────────────────────
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("key_groups.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="所属分组 ID，同组 Key 共享数据访问权限"
    )
    group: Mapped["KeyGroup | None"] = relationship(
        "KeyGroup", back_populates="api_keys", lazy="selectin"
    )
