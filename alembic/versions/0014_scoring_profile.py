"""scoring profile v3: quality_reports 新增口径元数据并放宽评分列为可空

Revision ID: 0014_scoring_profile
Revises: 0013_cad_enhancement
Create Date: 2026-11-10

评分口径 v3:
- 新增 `scoring_profile`（v2 / v3，NOT NULL，服务端默认 v2）——
  同一组数值列承载两种口径，跨口径统计必须按此列过滤
- 新增 `score_evaluated`（{维度: 是否已评价}）与 `evaluation_errors`（规则异常清单）
- 五维 `score_*` 列改可空：v3 下某维度可能整轮未被评价，NULL 表示「未评价」
  而不是 0 分（0 分会把没评过的维度按零分计入综合分）
- 旧单维 `completeness_score` 同样改可空：它与 `score_completeness` 同源，
  保持 `NOT NULL DEFAULT 1.0` 会把 v3 的「未评价」静默写成 100% 完整

降级前先把 NULL 回填 0.0，否则 NOT NULL 约束无法恢复。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_scoring_profile"
down_revision = "0013_cad_enhancement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quality_reports", sa.Column("scoring_profile", sa.String(8), nullable=False, server_default="v2", comment="口径版本：v2=条目通过率，v3=要素级派生"))
    op.add_column("quality_reports", sa.Column("score_evaluated", JSONB(), nullable=True, comment="{维度: 是否已评价}"))
    op.add_column("quality_reports", sa.Column("evaluation_errors", JSONB(), nullable=True, comment="规则执行异常清单"))
    op.alter_column("quality_reports", "completeness_score", nullable=True, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_completeness", nullable=True, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_accuracy", nullable=True, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_consistency", nullable=True, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_uniqueness", nullable=True, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_topology", nullable=True, existing_type=sa.Float())


def downgrade() -> None:
    # 回填：v3 报告里未评价的维度为 NULL，恢复 NOT NULL 前必须给它们一个值
    op.execute("UPDATE quality_reports SET completeness_score = COALESCE(completeness_score, 0.0)")
    op.execute("UPDATE quality_reports SET score_completeness = COALESCE(score_completeness, 0.0)")
    op.execute("UPDATE quality_reports SET score_accuracy = COALESCE(score_accuracy, 0.0)")
    op.execute("UPDATE quality_reports SET score_consistency = COALESCE(score_consistency, 0.0)")
    op.execute("UPDATE quality_reports SET score_uniqueness = COALESCE(score_uniqueness, 0.0)")
    op.execute("UPDATE quality_reports SET score_topology = COALESCE(score_topology, 0.0)")
    op.alter_column("quality_reports", "completeness_score", nullable=False, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_completeness", nullable=False, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_accuracy", nullable=False, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_consistency", nullable=False, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_uniqueness", nullable=False, existing_type=sa.Float())
    op.alter_column("quality_reports", "score_topology", nullable=False, existing_type=sa.Float())
    op.drop_column("quality_reports", "evaluation_errors")
    op.drop_column("quality_reports", "score_evaluated")
    op.drop_column("quality_reports", "scoring_profile")
