"""质检方案 ORM 模型 — 保存/加载质检配置（属性开关+拓扑规则+阈值）。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class QualityPlan(Base):
    """quality_plans — 质检方案（前端质检页保存/加载）"""

    __tablename__ = "quality_plans"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, comment="方案名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="方案描述")

    # ── 配置内容（JSON 存储） ──
    attr_enabled: Mapped[bool] = mapped_column(default=True, comment="属性检查开关")
    topo_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="拓扑规则开关+阈值")
    standard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="关联标准 ID")
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扩展字段（预留）")

    # ── 审计 ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "attr_enabled": self.attr_enabled,
            "topo_config": self.topo_config,
            "standard_id": self.standard_id,
            "extra": self.extra,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
