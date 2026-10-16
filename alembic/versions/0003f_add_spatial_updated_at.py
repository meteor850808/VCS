"""Migration: add updated_at column to spatial feature tables."""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0003f_add_spatial_updated_at"
down_revision = "0003e_add_stored_file_id"
branch_labels = None
depends_on = None


def upgrade():
    # 注：point/line/polygon_features 的 updated_at 已于 0001 建表时创建，
    # 原 ADD COLUMN 在全新库上必然 DuplicateColumnError
    # （2026-09-10 空库验证发现的重复列缺陷）→ no-op，保留函数维持迁移链语义
    pass


def downgrade():
    # 三表 updated_at 属 0001，不回退（配合 upgrade 修复）
    pass