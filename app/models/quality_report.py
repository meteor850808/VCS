"""质量报告 ORM 模型。

v2.1 变更：
- 添加外键约束 (job_id, batch_job_id, api_key_id, data_standard_id)
- 添加 updated_at 审计字段
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class QualityReport(Base):
    __tablename__ = "quality_reports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversion_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    detected_format: Mapped[str] = mapped_column(String(64), nullable=False)
    total_features: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    missing_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    null_value_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    critical_fields_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    issues: Mapped[list | None] = mapped_column(JSON, nullable=True)
    constraint_violations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    topology_violations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    data_standard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True
    )
    standard_table_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("standard_tables.id", ondelete="SET NULL"), nullable=True
    )
    stored_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True,
        comment="关联入库文件，用于导出违规要素几何"
    )
    # ── 新增：分项结果 ──
    attribute_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="{total,passed,failed,by_rule:{...}}")
    topology_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="{total,passed,failed,by_rule:{...}}")
    topology_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="拓扑检查配置快照")
    checked_layers: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
        comment="质检圈定的图层名列表（v2.10.5）；NULL=全部图层",
    )
    # ── 新增：五维评分 ──
    score_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_consistency: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_uniqueness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_topology: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # v2.20: 五维加权综合分（唯一来源 quality_checker.WEIGHTS；列在库中早已存在，
    # 此前从未写入/映射。无默认值——存量行保持 NULL → 读取时按 WEIGHTS 即时重算）
    score_overall: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="v2.20 五维加权综合分（quality_checker.WEIGHTS）",
    )
    html_report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    json_report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )
