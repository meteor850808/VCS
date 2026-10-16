"""标准层组-数据表 多对多关联模型 — S9 表多分类（方案 A）。

一张数据表可挂到多个层组分类下（跨专业复用），层组保持单父树不变。

语义约定：
- 每张表在**主层组**（`standard_tables.layer_id` 外键）拥有一行 `is_primary=True` 的关联；
  主层组是唯一真源，质检/快照/导出默认只认主归属。
- 其余为**次归属**（`is_primary=False`），仅表示「该表也归入此层组」。
- 删除次层组只摘挂不删表；删除主层组时若表仍被其他层组引用（有次归属关联），
  须先摘挂（B5 删除守卫在 API 层拦截并提示）。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardLayerGroupTableLink(Base):
    """层组-数据表多对多关联（含主归属标记）。"""
    __tablename__ = "standard_layer_group_tables"
    __table_args__ = (
        UniqueConstraint("layer_group_id", "table_id", name="uq_slgt_layer_table"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    layer_group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_layer_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
        comment="层组 ID"
    )
    table_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_tables.id", ondelete="CASCADE"),
        nullable=False, index=True,
        comment="数据表 ID"
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="是否主归属（每表唯一，与 standard_tables.layer_id 保持一致）"
    )
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # 关系
    layer_group: Mapped["StandardLayerGroup"] = relationship(
        "StandardLayerGroup", back_populates="table_links", lazy="selectin"
    )
    table: Mapped["StandardTable"] = relationship(
        "StandardTable", back_populates="table_links", lazy="selectin"
    )
