"""Migration: quality_plans 质检方案表（v2.10）.

- 新建 `quality_plans` 表：保存/加载质检配置（属性开关+拓扑规则+阈值）
- 字段：name (unique), description, attr_enabled, topo_config (JSON), standard_id, extra (JSON)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0008_quality_plans"
down_revision = "0007_layer_group_table_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True, comment="方案名称"),
        sa.Column("description", sa.Text, nullable=True, comment="方案描述"),
        sa.Column("attr_enabled", sa.Boolean, nullable=False, server_default=sa.text("true"), comment="属性检查开关"),
        sa.Column("topo_config", sa.JSON, nullable=True, comment="拓扑规则开关+阈值"),
        sa.Column("standard_id", sa.String(36), nullable=True, comment="关联标准 ID"),
        sa.Column("extra", sa.JSON, nullable=True, comment="扩展字段"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_quality_plans_name", "quality_plans", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_quality_plans_name", table_name="quality_plans")
    op.drop_table("quality_plans")
