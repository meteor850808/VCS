"""创建质检规则、执行结果、违规明细、每日快照表

Revision ID: 0003_quality_rules
Create Date: 2026-06-18
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_quality_rules"
down_revision: Union[str, None] = "0002_performance_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── quality_rules ──────────────────────────────────────────────────────────
    op.create_table(
        "quality_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id"), nullable=False, index=True),
        sa.Column("standard_field_id", sa.String(36), sa.ForeignKey("data_standard_fields.id", ondelete="CASCADE"), nullable=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("rule_name", sa.String(256), nullable=False),
        sa.Column("field_pattern", sa.String(256), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── quality_rule_results ───────────────────────────────────────────────────
    op.create_table(
        "quality_rule_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("quality_rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("total_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── quality_violations ─────────────────────────────────────────────────────
    op.create_table(
        "quality_violations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("rule_result_id", sa.String(36), sa.ForeignKey("quality_rule_results.id", ondelete="CASCADE"), nullable=True),
        sa.Column("field_name", sa.String(256), nullable=False, index=True),
        sa.Column("issue_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("actual", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── quality_daily_snapshots ────────────────────────────────────────────────
    op.create_table(
        "quality_daily_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), nullable=False, index=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checked_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completeness", sa.Numeric(5, 4), nullable=True),
        sa.Column("uniqueness", sa.Numeric(5, 4), nullable=True),
        sa.Column("consistency", sa.Numeric(5, 4), nullable=True),
        sa.Column("violations_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_overall", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("api_key_id", "snapshot_date", name="uq_qds_api_date"),
    )


def downgrade() -> None:
    op.drop_table("quality_daily_snapshots")
    op.drop_table("quality_violations")
    op.drop_table("quality_rule_results")
    op.drop_table("quality_rules")
