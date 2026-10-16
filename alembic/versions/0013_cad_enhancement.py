"""CAD enhancement: stored_files + stored_file_layers new columns

Revision ID: 0013_cad_enhancement
Revises: 0012_field_dict_links_fk
Create Date: 2026-09-11

v2.18 CAD 功能增强:
- stored_files: +cad_metadata (JSONB CAD 元数据缓存), +cad_preview_path (预览文件路径)
- stored_file_layers: +color_index/+color_rgb/+linetype/+is_visible/+is_frozen (CAD 图层样式)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_cad_enhancement"
down_revision = "0012_field_dict_links_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stored_files", sa.Column("cad_metadata", JSONB(), nullable=True))
    op.add_column("stored_files", sa.Column("cad_preview_path", sa.String(1024), nullable=True))
    op.add_column("stored_file_layers", sa.Column("color_index", sa.Integer(), nullable=True))
    op.add_column("stored_file_layers", sa.Column("color_rgb", sa.String(16), nullable=True))
    op.add_column("stored_file_layers", sa.Column("linetype", sa.String(64), nullable=True))
    op.add_column("stored_file_layers", sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("stored_file_layers", sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("stored_file_layers", "is_frozen")
    op.drop_column("stored_file_layers", "is_visible")
    op.drop_column("stored_file_layers", "linetype")
    op.drop_column("stored_file_layers", "color_rgb")
    op.drop_column("stored_file_layers", "color_index")
    op.drop_column("stored_files", "cad_preview_path")
    op.drop_column("stored_files", "cad_metadata")