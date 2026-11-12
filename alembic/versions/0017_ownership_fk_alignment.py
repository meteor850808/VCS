"""归属列与外键追平迁移链（补 17 个 FK、放宽 3 个归属列、归一孤立归属）

Revision ID: 0017_ownership_fk_alignment
Revises: 0016_schema_sync_cols_types
Create Date: 2026-11-12

背景（2026-11-12 生产库核查）：`vcs_db` 的 `alembic_version` 曾被人工 stamp 成仓库
不存在的 `0014_schema_drift_repair`，其 schema 由迁移链之外的 DDL 建成，与链/模型
存在两类差异：

1. **外键缺失 17 个**：10 个 `api_key_id → api_keys.id`，另外 7 个是父子引用
   （`conversion_jobs.batch_job_id`、`quality_reports.{job_id, batch_job_id,
   data_standard_id, standard_table_id}`、`data_standard_sheets.standard_id`、
   `data_standard_fields.sheet_id`）。这些列可空，写错 ID 不报错，只会静默留下
   孤立引用。
2. **三个归属列仍是 NOT NULL**：`stored_files` / `source_tables` /
   `source_dictionaries` 的 `api_key_id`。链上 0001/0002 建表时就是
   `nullable=True`；超管（`ADMIN_INIT_KEY`）写 `owner_id=None` 会被 NOT NULL 打回。

本迁移只让既有库追平迁移链，**不改模型**：

- a. 放宽 3 个归属列为可空；
- b. 把历史上写入的孤立归属归一为 NULL（超管虚拟 ID
  `00000000-0000-0000-0000-000000000001`、已被删除的 Key）——与 FK
  `ON DELETE SET NULL` 的语义、以及「超管归属 = NULL」的设计一致；
- c. 补 17 个外键。判重看 `(conrelid, conkey) → (confrelid, confkey)` 而不是
  约束名，因此链上已建好的库、以及曾用自定义命名的库都能安全跳过（幂等）。

降级：按实际约束名删除外键；若三个列中仍有 NULL（超管归属），**拒绝**恢复
NOT NULL 并提示先补归属——历史上的虚拟 ID 无法还原，不伪造。
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_ownership_fk_alignment"
down_revision = "0016_schema_sync_cols_types"
branch_labels = None
depends_on = None

# (表, 列, 目标表, 目标列, ondelete)
_FKS: list[tuple[str, str, str, str, str | None]] = [
    ("batch_jobs", "api_key_id", "api_keys", "id", "SET NULL"),
    ("conversion_jobs", "api_key_id", "api_keys", "id", "SET NULL"),
    ("conversion_jobs", "batch_job_id", "batch_jobs", "id", "SET NULL"),
    ("data_standard_sheets", "standard_id", "data_standards", "id", "CASCADE"),
    ("data_standard_fields", "sheet_id", "data_standard_sheets", "id", "CASCADE"),
    ("data_standards", "api_key_id", "api_keys", "id", "SET NULL"),
    ("quality_daily_snapshots", "api_key_id", "api_keys", "id", "CASCADE"),
    ("quality_reports", "api_key_id", "api_keys", "id", "SET NULL"),
    ("quality_reports", "standard_table_id", "standard_tables", "id", "SET NULL"),
    ("quality_reports", "job_id", "conversion_jobs", "id", "SET NULL"),
    ("quality_reports", "batch_job_id", "batch_jobs", "id", "SET NULL"),
    ("quality_reports", "data_standard_id", "data_standards", "id", "SET NULL"),
    ("quality_rules", "api_key_id", "api_keys", "id", None),
    ("source_dictionaries", "api_key_id", "api_keys", "id", "SET NULL"),
    ("source_tables", "api_key_id", "api_keys", "id", "SET NULL"),
    ("standard_versions", "api_key_id", "api_keys", "id", "SET NULL"),
    ("stored_files", "api_key_id", "api_keys", "id", "SET NULL"),
]

# 链上建表即可空，本库被链外 DDL 建成 NOT NULL 的归属列
_RELAX = ("stored_files", "source_tables", "source_dictionaries")

# 允许 NULL 归属的 api_key_id 列（quality_daily_snapshots 在链/模型中都要求非空）
_OWNER_TABLES = (
    "batch_jobs",
    "conversion_jobs",
    "data_standards",
    "quality_reports",
    "quality_rules",
    "source_dictionaries",
    "source_tables",
    "standard_versions",
    "stored_files",
)

_FK_NAME = sa.text(
    """
    SELECT c.conname
    FROM pg_constraint c
    WHERE c.contype = 'f'
      AND c.conrelid = to_regclass(:tbl)
      AND c.confrelid = to_regclass(:reftbl)
      AND c.conkey = ARRAY[
            (SELECT a.attnum FROM pg_attribute a
              WHERE a.attrelid = to_regclass(:tbl) AND a.attname = :col)
          ]
      AND c.confkey = ARRAY[
            (SELECT a.attnum FROM pg_attribute a
              WHERE a.attrelid = to_regclass(:reftbl) AND a.attname = :refcol)
          ]
    LIMIT 1
    """
)


def _existing_fk_name(bind, table, column, ref_table, ref_column) -> str | None:
    """返回已存在的同列同目标 FK 名；没有则 None。按列而非按名字判重。"""
    return bind.execute(
        _FK_NAME,
        {"tbl": table, "col": column, "reftbl": ref_table, "refcol": ref_column},
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 放宽归属列（链上即可空）────────────────────────────────────────
    for table in _RELAX:
        bind.execute(
            sa.text(f'ALTER TABLE "{table}" ALTER COLUMN api_key_id DROP NOT NULL')
        )

    # ── 2. 归一孤立归属：超管虚拟 ID / 已删除的 Key → NULL ────────────────
    # 等价于 FK ON DELETE SET NULL 的语义；不引入伪造的归属值。
    for table in _OWNER_TABLES:
        bind.execute(
            sa.text(
                f'UPDATE "{table}" AS x SET api_key_id = NULL '
                f'WHERE x.api_key_id IS NOT NULL '
                f'AND NOT EXISTS (SELECT 1 FROM api_keys k WHERE k.id = x.api_key_id)'
            )
        )

    # ── 3. 补外键（幂等：已存在同列同目标则跳过）──────────────────────────
    for table, column, ref_table, ref_column, ondelete in _FKS:
        if _existing_fk_name(bind, table, column, ref_table, ref_column):
            continue
        name = f"{table}_{column}_fkey"
        ondelete_sql = f" ON DELETE {ondelete}" if ondelete else ""
        bind.execute(
            sa.text(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
                f'FOREIGN KEY ("{column}") REFERENCES "{ref_table}" ("{ref_column}")'
                f"{ondelete_sql}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    # 恢复 NOT NULL 前必须没有 NULL：超管归属在旧 schema 无法表达，
    # 编造归属值会把「无归属」伪装成某个 Key 的数据。
    problems = []
    for table in _RELAX:
        nulls = bind.execute(
            sa.text(f'SELECT count(*) FROM "{table}" WHERE api_key_id IS NULL')
        ).scalar()
        if nulls:
            problems.append(f"{table}.api_key_id 仍有 {nulls} 行 NULL")
    if problems:
        raise RuntimeError(
            "降级中止（历史归属无法还原）：" + "；".join(problems)
            + "。请先补齐归属或删除这些行后再降级。"
        )

    for table, column, ref_table, ref_column, _ondelete in reversed(_FKS):
        name = _existing_fk_name(bind, table, column, ref_table, ref_column)
        if name:
            bind.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'))

    for table in _RELAX:
        bind.execute(
            sa.text(f'ALTER TABLE "{table}" ALTER COLUMN api_key_id SET NOT NULL')
        )
