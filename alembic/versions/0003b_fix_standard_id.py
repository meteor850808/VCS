"""fix: 补充 stored_files 缺失的标准关联列 standard_id / standard_table_id。

Revision ID: 0003b_fix_standard_id
Create Date: 2026-07-21

根因：ORM 模型定义了 standard_id / standard_table_id 外键列，
但 baseline 0001 未创建，0003 也未包含。此迁移补充缺失列。
"""

from alembic import op
import sqlalchemy as sa

revision = "0003b_fix_standard_id"
down_revision = "0003_stored_files_v2_4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stored_files", sa.Column(
        "standard_id", sa.String(36),
        sa.ForeignKey("data_standards.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="关联数据标准",
    ))
    op.add_column("stored_files", sa.Column(
        "standard_table_id", sa.String(36),
        sa.ForeignKey("standard_tables.id", ondelete="SET NULL"),
        nullable=True, comment="关联标准表",
    ))


def downgrade() -> None:
    op.drop_column("stored_files", "standard_table_id")
    op.drop_column("stored_files", "standard_id")
