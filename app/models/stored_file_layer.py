"""StoredFileLayer — 文件-图层汇总模型（v2.10.5 图层 × 几何类型归类）.

每个入库文件的每个图层一行；点/线/面三维计数由
`feature_store.aggregate_layer_stats` 从三张分区要素表实时聚合维护
（源即真相，无漂移）。质检图层圈定（quality-check 的 layers 参数）
也以本表为准校验图层名合法性。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class StoredFileLayer(Base):
    __tablename__ = "stored_file_layers"
    __table_args__ = (
        UniqueConstraint("stored_file_id", "layer_name", name="uq_sfl_file_layer"),
        Index("ix_sfl_api_key", "api_key_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    stored_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stored_files.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    api_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    layer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    geometry_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="主导几何类型 Point/LineString/Polygon/mixed",
    )
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    polygon_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="要素总数（冗余 = 三类之和，便于排序）",
    )
    # ── CAD 图层样式（v2.18）──
    color_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="DXF ACI 颜色索引 (1-255)"
    )
    color_rgb: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="真彩色 'R,G,B'（如有）"
    )
    linetype: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="线型名"
    )
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="可见性"
    )
    is_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="冻结状态"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
