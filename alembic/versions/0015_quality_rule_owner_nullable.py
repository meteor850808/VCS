"""quality_rules.api_key_id 放宽为可空（超管规则归属 NULL）

Revision ID: 0015_quality_rule_owner_nullable
Revises: 0014_scoring_profile
Create Date: 2026-11-11

背景：超级管理员经 `X-API-Key: <ADMIN_INIT_KEY>` 鉴权时，`api_key.id` 是
虚拟 ID `00000000-0000-0000-0000-000000000001`，`api_keys` 表没有这一行。
`quality_rules.api_key_id` 原为 NOT NULL，超管创建/生成质检规则只能写虚拟
ID → `ForeignKeyViolationError`（HTTP 500）。其余归属列早已可空并统一写
`api_key.owner_id`（超管 → NULL），本迁移把最后一列补齐。

降级：NULL 归属的规则在旧 schema 无法表达（NOT NULL + FK），
先删除这些行再恢复约束。
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_quality_rule_owner_nullable"
down_revision = "0014_scoring_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "quality_rules", "api_key_id",
        nullable=True, existing_type=sa.String(36),
        comment="归属 Key；超级管理员创建/生成的规则为 NULL",
    )


def downgrade() -> None:
    op.execute("DELETE FROM quality_rules WHERE api_key_id IS NULL")
    op.alter_column(
        "quality_rules", "api_key_id",
        nullable=False, existing_type=sa.String(36),
        comment=None,
    )
