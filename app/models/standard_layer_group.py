"""标准层组模型 — 支持嵌套层级结构（最大深度 5 层）。

层组是数据标准的业务逻辑分组，用于组织数据表。
支持任意深度嵌套，但限制最大深度为 5 层。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StandardLayerGroup(Base):
    """标准层组 — 支持嵌套的业务逻辑分组。"""
    __tablename__ = "standard_layer_groups"
    __table_args__ = (
        UniqueConstraint("standard_id", "parent_id", "group_name", name="uq_slg_std_parent_name"),
        UniqueConstraint("standard_id", "parent_id", "group_code", name="uq_slg_std_parent_code"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id != id",
            name="ck_slg_no_self_ref"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    standard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("standard_layer_groups.id", ondelete="CASCADE"),
        nullable=True, index=True,
        comment="父层组 ID，NULL 表示顶层"
    )
    group_name: Mapped[str] = mapped_column(String(256), nullable=False)
    group_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    standard: Mapped["DataStandard"] = relationship(
        "DataStandard", back_populates="layer_groups", lazy="selectin"
    )
    parent: Mapped["StandardLayerGroup | None"] = relationship(
        "StandardLayerGroup", remote_side="StandardLayerGroup.id",
        back_populates="children", lazy="selectin"
    )
    children: Mapped[list["StandardLayerGroup"]] = relationship(
        "StandardLayerGroup", back_populates="parent",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="StandardLayerGroup.ordering"
    )
    tables: Mapped[list["StandardTable"]] = relationship(
        "StandardTable", back_populates="layer",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="StandardTable.ordering"
    )
    # 【S9】多对多关联（含主/次归属标记）
    table_links: Mapped[list["StandardLayerGroupTableLink"]] = relationship(
        "StandardLayerGroupTableLink", back_populates="layer_group",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="StandardLayerGroupTableLink.ordering",
    )

    @property
    def table_count(self) -> int:
        """本层组挂载的数据表总数（主归属 + 次归属）。"""
        prim = len(self.tables)
        sec = len([lk for lk in self.table_links if not lk.is_primary])
        return prim + sec

    @property
    def depth(self) -> int:
        """计算当前层组的嵌套深度。"""
        d = 0
        current = self.parent
        while current is not None:
            d += 1
            current = current.parent
        return d

    @property
    def is_root(self) -> bool:
        """是否为顶层组。"""
        return self.parent_id is None

    def get_path(self) -> list[str]:
        """获取从根到当前节点的路径名称列表。"""
        path = [self.group_name]
        current = self.parent
        while current is not None:
            path.insert(0, current.group_name)
            current = current.parent
        return path
