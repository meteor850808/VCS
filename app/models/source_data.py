"""源数据管理模型 — 源数据表、字段、字典、版本。

v2.1 变更：
- 添加外键约束 (api_key_id → api_keys.id)
- 添加 updated_at 审计字段
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class SourceTable(Base):
    """源数据表 — 对应业务图层/实体。"""
    __tablename__ = "source_tables"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    standard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True
    )
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="Point/Polygon/None=属性表"
    )
    source: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="数据来源：国标/行标/自定义"
    )
    version: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="版本号：GB/T 13923-2022"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
    fields: Mapped[list["SourceField"]] = relationship(
        "SourceField", back_populates="table",
        order_by="SourceField.ordering",
        cascade="all, delete-orphan",
    )


class SourceField(Base):
    """源数据字段 — 每个字段的定义。"""
    __tablename__ = "source_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table: Mapped["SourceTable"] = relationship("SourceTable", back_populates="fields")
    field_name: Mapped[str] = mapped_column(String(256), nullable=False)
    field_alias: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="字段别名（中文名）"
    )
    field_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="字段分类：属性/几何/时间"
    )
    data_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="string",
        comment="数据类型：string/integer/float/date/geometry"
    )
    length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    precision: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="小数精度")
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_values: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="兼容旧版：内嵌允许值列表（推荐用字典）"
    )
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
    dict_links: Mapped[list["FieldDictLink"]] = relationship(
        "FieldDictLink", back_populates="field",
        cascade="all, delete-orphan",
    )


class SourceDictionary(Base):
    """源数据字典 — 码值对照表。（迁移后将被 StandardDictionary 替代）"""
    __tablename__ = "source_dictionaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dict_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dict_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )


# DictionaryItem 已迁移到 app.models.dictionary_item（新模型）


class FieldDictLink(Base):
    """字段-字典关联 — 多对多关系。"""
    __tablename__ = "field_dict_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    field_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_fields.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    field: Mapped["SourceField"] = relationship("SourceField", back_populates="dict_links")
    dict_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("standard_dictionaries.id", ondelete="CASCADE"),
        nullable=False
    )
    filter_condition: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="码值筛选条件"
    )
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StandardVersion(Base):
    """标准版本 — 每次上传/更新的变更记录。"""
    __tablename__ = "standard_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    standard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    diff_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    change_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系 — 与 DataStandard 双向
    standard: Mapped["DataStandard | None"] = relationship(
        "DataStandard", back_populates="versions", lazy="selectin"
    )
