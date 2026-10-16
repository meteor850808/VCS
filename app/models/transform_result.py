"""坐标转换结果 ORM 模型（v2.11）。

独立于原文件，保存转换后的 GeoJSON 输出与审计信息。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TransformResult(Base):
    __tablename__ = "transform_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    source_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("stored_files.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    source_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_crs: Mapped[str] = mapped_column(String(256), nullable=False)
    target_crs: Mapped[str] = mapped_column(String(256), nullable=False)
    source_srid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_srid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
