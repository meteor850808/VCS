"""性能优化索引迁移

Revision ID: 0002_performance_indexes
Create Date: 2026-06-15
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_performance_indexes"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── conversion_jobs 复合索引（按状态+时间查询）──────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_status_created
        ON conversion_jobs (status, created_at DESC);
    """)

    # ── 部分索引（仅活跃任务，加速 pending 队列扫描）─────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_active
        ON conversion_jobs (created_at)
        WHERE status IN ('pending', 'processing');
    """)

    # ── API Key 查询索引 ─────────────────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_api_key_id
        ON conversion_jobs (api_key_id)
        WHERE api_key_id IS NOT NULL;
    """)

    # ── 空间索引（GIST，加速 BBOX 查询）— 跨三表 ──────────────────────────────
    for tbl in ("point_features", "line_features", "polygon_features"):
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{tbl}_geom
            ON {tbl} USING GIST (geometry);
        """)

    # ── stored_files 查询优化 ─────────────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_stored_files_api_key
        ON stored_files (api_key_id, created_at DESC)
        WHERE api_key_id IS NOT NULL;
    """)


def downgrade() -> None:
    for tbl in ("point_features", "line_features", "polygon_features"):
        op.execute(f"DROP INDEX IF EXISTS idx_{tbl}_geom;")
    op.execute("DROP INDEX IF EXISTS idx_stored_files_api_key;")
    op.execute("DROP INDEX IF EXISTS idx_jobs_api_key_id;")
    op.execute("DROP INDEX IF EXISTS idx_jobs_active;")
    op.execute("DROP INDEX IF EXISTS idx_jobs_status_created;")
