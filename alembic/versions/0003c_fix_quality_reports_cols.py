"""fix: 补充 quality_reports 缺失的 10 个列（五维评分/拓扑/更新时间等）。

Revision ID: 0003c_fix_quality_reports_cols
Create Date: 2026-07-22

根因：ORM 模型 QualityReport 在 v2.1 中新增了五维评分、拓扑摘要等字段，
但 alembic baseline 0001 和后续迁移均未包含这些列，导致查询报500。
"""

from alembic import op
import sqlalchemy as sa

revision = "0003c_fix_quality_reports_cols"
down_revision = "0003b_fix_standard_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quality_reports", sa.Column("attribute_summary", sa.JSON(), nullable=True))
    op.add_column("quality_reports", sa.Column("topology_summary", sa.JSON(), nullable=True))
    op.add_column("quality_reports", sa.Column("topology_config", sa.JSON(), nullable=True))
    op.add_column("quality_reports", sa.Column("score_completeness", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("quality_reports", sa.Column("score_accuracy", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("quality_reports", sa.Column("score_consistency", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("quality_reports", sa.Column("score_uniqueness", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("quality_reports", sa.Column("score_topology", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("quality_reports", sa.Column("standard_table_id", sa.String(36), nullable=True))
    # 注：quality_reports.updated_at 已于 0001 建表时创建，此处不再重复 ADD
    #（2026-09-10 空库验证发现的重复列缺陷，已移除）


def downgrade() -> None:
    # 注：quality_reports.updated_at 属 0001，此处不回退（配合 upgrade 修复）
    op.drop_column("quality_reports", "standard_table_id")
    op.drop_column("quality_reports", "score_topology")
    op.drop_column("quality_reports", "score_uniqueness")
    op.drop_column("quality_reports", "score_consistency")
    op.drop_column("quality_reports", "score_accuracy")
    op.drop_column("quality_reports", "score_completeness")
    op.drop_column("quality_reports", "topology_config")
    op.drop_column("quality_reports", "topology_summary")
    op.drop_column("quality_reports", "attribute_summary")
