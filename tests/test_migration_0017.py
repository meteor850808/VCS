"""0017 迁移的静态一致性（不连数据库）。

守住三件事：
1. 外键清单与模型声明的父引用一一对应（17 条，ondelete 不能漂）；
2. 补外键必须**按列**判重（链上已建库/自定义命名库都要跳过），不能盲加；
3. 降级在仍有 NULL 归属时必须拒绝，不能把「无归属」伪造成某个 Key 的数据。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0017_ownership_fk_alignment.py"

# (表, 列, 目标表, 目标列, ondelete)
EXPECTED_FKS = {
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
}


def _source() -> str:
    return MIG.read_text(encoding="utf-8")


def _literal(name: str):
    """从迁移源码里取字面量常量。

    不能直接 import 迁移模块：项目根的 `alembic/`（迁移目录）会遮蔽同名已安装包，
    `from alembic import op` 会 ImportError。
    """
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"迁移里找不到常量 {name}")


def test_revision_chain():
    src = _source()
    assert 'revision = "0017_ownership_fk_alignment"' in src
    assert 'down_revision = "0016_schema_sync_cols_types"' in src


def test_fk_list_matches_expected():
    fks = _literal("_FKS")
    assert {tuple(x) for x in fks} == EXPECTED_FKS
    assert len(fks) == len(EXPECTED_FKS) == 17


def test_all_api_key_ownership_columns_are_covered():
    """到 api_keys 的外键必须覆盖所有可空归属列（各自只有一条）。"""
    fks = [tuple(x) for x in _literal("_FKS")]

    api_key_fks = [(t, c) for t, c, rt, rc, _ in fks if rt == "api_keys"]
    assert len(api_key_fks) == len(set(api_key_fks)) == 10
    assert ("quality_rules", "api_key_id") in api_key_fks
    assert ("stored_files", "api_key_id") in api_key_fks
    assert ("source_tables", "api_key_id") in api_key_fks
    assert ("source_dictionaries", "api_key_id") in api_key_fks


def test_relax_targets_three_ownership_columns():
    assert _literal("_RELAX") == ("stored_files", "source_tables", "source_dictionaries")
    src = _source()
    assert 'ALTER COLUMN api_key_id DROP NOT NULL' in src


def test_fk_add_is_guarded_by_columns_not_by_name():
    src = _source()
    # 判重 SQL 必须看 pg_constraint 的 conkey/confkey
    assert "pg_constraint" in src
    assert "c.conkey" in src and "c.confkey" in src
    assert "to_regclass(:tbl)" in src
    # upgrade 里必须先查出已有外键名再决定是否 ADD
    up = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "_existing_fk_name(" in up
    assert "if _existing_fk_name(" in up


def test_orphan_cleanup_is_key_existence_based():
    """归一孤立归属靠「Key 不存在」判断，而不是硬编码虚拟 ID。"""
    src = _source()
    up = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "NOT EXISTS (SELECT 1 FROM api_keys k WHERE k.id = x.api_key_id)" in up
    assert "00000000-0000-0000-0000-000000000001" not in up


def test_downgrade_refuses_when_owner_is_null():
    src = _source()
    down = src.split("def downgrade")[1]
    assert "RuntimeError" in down
    assert "raise RuntimeError" in down
    # 先检查再 SET NOT NULL
    assert down.index("WHERE api_key_id IS NULL") < down.index("SET NOT NULL")
    assert "DROP CONSTRAINT" in down


def test_orm_models_agree_with_migration():
    from app.models.quality_daily_snapshot import QualityDailySnapshot
    from app.models.quality_rule import QualityRule
    from app.models.source_data import SourceDictionary, SourceTable
    from app.models.stored_file import StoredFile

    assert StoredFile.__table__.c.api_key_id.nullable is True
    assert SourceTable.__table__.c.api_key_id.nullable is True
    assert SourceDictionary.__table__.c.api_key_id.nullable is True
    assert QualityRule.__table__.c.api_key_id.nullable is True
    # 链/模型都要求非空，不能顺手放宽
    assert QualityDailySnapshot.__table__.c.api_key_id.nullable is False
