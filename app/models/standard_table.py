"""标准数据表模型 — 合并原 SourceTable。

数据表属于层组，可指定类型（空间/属性/关系）。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardTable(Base):
    """标准数据表 — 属于层组的数据表定义。"""
    __tablename__ = "standard_tables"
    __table_args__ = (
        UniqueConstraint("layer_id", "table_name", name="uq_st_layer_name"),
        UniqueConstraint("layer_id", "table_code", name="uq_st_layer_code"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    layer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_layer_groups.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    standard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    table_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="spatial",
        comment="spatial (空间) / attribute (属性) / relationship (关系)"
    )
    geometry_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="Point/LineString/Polygon/None"
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
    layer: Mapped["StandardLayerGroup"] = relationship(
        "StandardLayerGroup", back_populates="tables", lazy="selectin"
    )
    standard: Mapped["DataStandard"] = relationship(
        "DataStandard", back_populates="standard_tables", lazy="selectin"
    )
    fields: Mapped[list["StandardField"]] = relationship(
        "StandardField", back_populates="table",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="StandardField.ordering"
    )
    # 【S9】多对多关联（含主/次归属标记；主归属与 layer_id 保持一致）
    table_links: Mapped[list["StandardLayerGroupTableLink"]] = relationship(
        "StandardLayerGroupTableLink", back_populates="table",
        cascade="all, delete-orphan", lazy="selectin",
    )
