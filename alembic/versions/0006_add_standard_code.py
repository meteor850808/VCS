"""Migration: add code column to data_standards (S7 标准回传更新).

v2.8 S7 变更：数据标准新增「标准编码」字段（data_standards.code），
作为导入回传（mode=update/upsert）的稳定匹配键：
- 原唯一约束仅 (api_key_id, name)，改名导入即撞约束；
- 新增 (api_key_id, code) 唯一约束，code 可空（存量标准不受影响，
  PostgreSQL 唯一约束允许多个 NULL）。

已有数据 code 均为 NULL，语义等价于「未分配编码，仅按名称匹配」。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0006_add_standard_code"
down_revision = "0005_remove_srid_typmod"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_standards",
        sa.Column("code", sa.String(64), nullable=True, index=True,
                  comment="标准编码（导入回传的匹配键）"),
    )
    op.create_unique_constraint("uq_ds_api_code", "data_standards", ["api_key_id", "code"])


def downgrade() -> None:
    op.drop_constraint("uq_ds_api_code", "data_standards", type_="unique")
    op.drop_index(op.f("ix_data_standards_code"), table_name="data_standards")
    op.drop_column("data_standards", "code")
