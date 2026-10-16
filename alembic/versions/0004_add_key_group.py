"""Migration: add key_groups table and group_id to api_keys.

- Creates key_groups table
- Adds group_id FK column to api_keys table
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_add_key_group"
down_revision = "0003f_add_spatial_updated_at"
branch_labels = None
depends_on = None


def upgrade():
    # ── Create key_groups table ──────────────────────────────────────────
    op.create_table(
        "key_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False, index=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_key_groups_name"),
    )

    # ── Add group_id to api_keys ─────────────────────────────────────────
    op.add_column(
        "api_keys",
        sa.Column(
            "group_id", sa.String(36),
            sa.ForeignKey("key_groups.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ),
    )


def downgrade():
    op.drop_column("api_keys", "group_id")
    op.drop_table("key_groups")
