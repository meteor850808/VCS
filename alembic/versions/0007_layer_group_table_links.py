"""Migration: standard_layer_group_tables M2M (S9 表多分类).

v2.8 S9 方案 A：数据表可挂到多个层组分类下，层组保持单父树。
- 新建 `standard_layer_group_tables` 多对多关联表（layer_group_id, table_id,
  is_primary, ordering）；
- 回填存量数据：已有表的主归属写一行 `is_primary=True`（与 layer_id 外键一致），
  使关联表成为唯一真源（主 + 次归属都在其中）；
- 唯一约束 (layer_group_id, table_id) 防止重复挂载；
- 两端外键 ondelete=CASCADE：删层组只摘挂、删表清关联，均不产生孤儿行。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0007_layer_group_table_links"
down_revision = "0006_add_standard_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standard_layer_group_tables",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("layer_group_id", sa.String(36), nullable=False),
        sa.Column("table_id", sa.String(36), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ordering", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["layer_group_id"], ["standard_layer_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["standard_tables.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("layer_group_id", "table_id", name="uq_slgt_layer_table"),
    )
    op.create_index("ix_slgt_layer_group_id", "standard_layer_group_tables", ["layer_group_id"])
    op.create_index("ix_slgt_table_id", "standard_layer_group_tables", ["table_id"])

    # 回填存量：每张表在 layer_id 指向的主层组下写一行 is_primary=True
    op.execute(
        """
        INSERT INTO standard_layer_group_tables
            (id, layer_group_id, table_id, is_primary, ordering, created_at)
        SELECT
            gen_random_uuid()::text,
            layer_id, id, true, ordering, now()
        FROM standard_tables
        """
    )


def downgrade() -> None:
    op.drop_index("ix_slgt_table_id", table_name="standard_layer_group_tables")
    op.drop_index("ix_slgt_layer_group_id", table_name="standard_layer_group_tables")
    op.drop_table("standard_layer_group_tables")
