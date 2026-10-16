"""数据标准 ORM 模型 — 导入 Excel 定义的字段约束规范。

v2.2 变更：
- 添加 category (national/industry/custom)
- 添加 status (draft/published/archived)
- 添加 version 版本号
- 添加唯一约束 (api_key_id, name)
- 添加与新模型的关系
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class DataStandard(Base):
    __tablename__ = "data_standards"
    __table_args__ = (
        UniqueConstraint("api_key_id", "name", name="uq_ds_api_name"),
        UniqueConstraint("api_key_id", "code", name="uq_ds_api_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
        comment="标准编码（导入回传的匹配键，create 时可填）"
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="custom",
        comment="national (国标) / industry (行标) / custom (自定义)"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft",
        comment="draft (草稿) / published (发布) / archived (归档)"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sheet_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系 — 新模型
    layer_groups: Mapped[list["StandardLayerGroup"]] = relationship(
        "StandardLayerGroup", back_populates="standard",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="StandardLayerGroup.ordering"
    )
    standard_tables: Mapped[list["StandardTable"]] = relationship(
        "StandardTable", back_populates="standard",
        cascade="all, delete-orphan", lazy="selectin"
    )
    standard_fields: Mapped[list["StandardField"]] = relationship(
        "StandardField", back_populates="standard",
        cascade="all, delete-orphan", lazy="selectin"
    )
    standard_dictionaries: Mapped[list["StandardDictionary"]] = relationship(
        "StandardDictionary", back_populates="standard",
        cascade="all, delete-orphan", lazy="selectin"
    )
    versions: Mapped[list["StandardVersion"]] = relationship(
        "StandardVersion", back_populates="standard",
        cascade="all, delete-orphan", lazy="selectin"
    )


class DataStandardSheet(Base):
    __tablename__ = "data_standard_sheets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    standard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_name: Mapped[str] = mapped_column(String(256), nullable=False)
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
    fields: Mapped[list["DataStandardField"]] = relationship(
        "DataStandardField", back_populates="sheet",
        order_by="DataStandardField.ordering",
        lazy="selectin",
    )


class DataStandardField(Base):
    __tablename__ = "data_standard_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sheet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_standard_sheets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet: Mapped["DataStandardSheet"] = relationship("DataStandardSheet", back_populates="fields")
    field_name: Mapped[str] = mapped_column(String(256), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False, default="string")
    length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_values: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
