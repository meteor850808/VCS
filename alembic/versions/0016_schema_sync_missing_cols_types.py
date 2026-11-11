"""同步迁移链与模型：补齐缺失列、修正 JSON/JSONB 与 CRS 列宽

Revision ID: 0016_schema_sync_cols_types
Revises: 0015_quality_rule_owner_nullable
Create Date: 2026-11-11

背景（2026-11-11 复核）：在「只用迁移链从空库升级到 head」的库上跑 `alembic check`，
发现迁移链建出的 schema 与 ORM 模型不一致，且集中在两类：

1. **缺失列**（模型有、迁移链未建）
   - `stored_files.layer_name`
   - `quality_reports.score_overall`（模型注释称「列在库中早已存在」——说明生产库
     是迁移链之外的 DDL 建的，本迁移用 `IF NOT EXISTS` 补齐空库，生产上为幂等 no-op）
   - `standard_versions.{version_number, snapshot_data, diff_data, is_rollback}`
2. **类型不符**
   - `point/line/polygon_features.properties`：库中仍是 `JSON`，而模型/文档（v2.10）
     声明为 `JSONB`（GIN 索引要求 jsonb）
   - `stored_files.detected_crs`：库中仍是 `VARCHAR(64)`，模型要求 `VARCHAR(2048)`
     以容纳完整 CRS WKT（如 PROJCS）

本迁移只做「加列 + 类型对齐」这类增改，**不触碰**索引 / 外键 / 可空 / 唯一约束 / 注释
（其余漂移多为刻意设计分歧或重命名，`DROP` 会伤生产；`quality_reports.checked_layers`
等 3 列的 JSONB/JSON 差异已在模型侧对齐为 JSONB，无需 DDL）。所有加列用
`IF NOT EXISTS`、类型变更先探测现状再决定，故对已由其它方式建好的生产库幂等。

运维提示：`properties: JSON -> JSONB` 会重写分区表数据，大表请在维护窗口执行。
降级会把 `detected_crs` 收窄回 `VARCHAR(64)`，超长值将被截断。
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_schema_sync_cols_types"
down_revision = "0015_quality_rule_owner_nullable"
branch_labels = None
depends_on = None

_FEATURE_TABLES = ("point_features", "line_features", "polygon_features")


def _data_type(bind, table: str, column: str) -> str | None:
    """返回 information_schema 中的 data_type（json / jsonb / character varying ...）。"""
    return bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()


def _char_len(bind, table: str, column: str) -> int | None:
    return bind.execute(
        sa.text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 补齐缺失列（幂等）──────────────────────────────────────────────
    op.execute(
        "ALTER TABLE stored_files ADD COLUMN IF NOT EXISTS layer_name VARCHAR(512)"
    )
    op.execute(
        "COMMENT ON COLUMN stored_files.layer_name IS "
        "'图层名/逻辑名称（从 original_name 中拆分，用于前端展示）'"
    )

    op.execute(
        "ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_overall FLOAT"
    )
    op.execute(
        "COMMENT ON COLUMN quality_reports.score_overall IS "
        "'v2.20 五维加权综合分（quality_checker.WEIGHTS）'"
    )

    op.execute(
        "ALTER TABLE standard_versions ADD COLUMN IF NOT EXISTS "
        "version_number INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE standard_versions ADD COLUMN IF NOT EXISTS snapshot_data JSON"
    )
    op.execute(
        "ALTER TABLE standard_versions ADD COLUMN IF NOT EXISTS diff_data JSON"
    )
    op.execute(
        "ALTER TABLE standard_versions ADD COLUMN IF NOT EXISTS "
        "is_rollback BOOLEAN NOT NULL DEFAULT false"
    )

    # ── 2. 类型对齐（先探测，已在目标类型则跳过）──────────────────────────
    for table in _FEATURE_TABLES:
        if _data_type(bind, table, "properties") != "jsonb":
            bind.execute(
                sa.text(
                    f"ALTER TABLE {table} ALTER COLUMN properties TYPE JSONB "
                    f"USING properties::jsonb"
                )
            )

    if (_char_len(bind, "stored_files", "detected_crs") or 0) < 2048:
        bind.execute(
            sa.text(
                "ALTER TABLE stored_files ALTER COLUMN detected_crs TYPE VARCHAR(2048)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    # ── 类型回退 ─────────────────────────────────────────────────────────
    for table in _FEATURE_TABLES:
        if _data_type(bind, table, "properties") == "jsonb":
            bind.execute(
                sa.text(
                    f"ALTER TABLE {table} ALTER COLUMN properties TYPE JSON "
                    f"USING properties::json"
                )
            )

    if (_char_len(bind, "stored_files", "detected_crs") or 0) > 64:
        bind.execute(
            sa.text(
                "ALTER TABLE stored_files ALTER COLUMN detected_crs "
                "TYPE VARCHAR(64) USING substr(detected_crs, 1, 64)"
            )
        )

    # ── 删回新增列 ───────────────────────────────────────────────────────
    op.execute("ALTER TABLE standard_versions DROP COLUMN IF EXISTS is_rollback")
    op.execute("ALTER TABLE standard_versions DROP COLUMN IF EXISTS diff_data")
    op.execute("ALTER TABLE standard_versions DROP COLUMN IF EXISTS snapshot_data")
    op.execute("ALTER TABLE standard_versions DROP COLUMN IF EXISTS version_number")
    op.execute("ALTER TABLE quality_reports DROP COLUMN IF EXISTS score_overall")
    op.execute("ALTER TABLE stored_files DROP COLUMN IF EXISTS layer_name")
