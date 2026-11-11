"""0016 迁移的静态一致性 + ORM 列存在性（不连数据库）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0016_schema_sync_missing_cols_types.py"


def test_revision_chain():
    src = MIG.read_text(encoding="utf-8")
    assert 'revision = "0016_schema_sync_cols_types"' in src
    assert 'down_revision = "0015_quality_rule_owner_nullable"' in src


def test_upgrade_adds_missing_columns_idempotently():
    src = MIG.read_text(encoding="utf-8")
    up = src.split("def downgrade")[0]
    # 每个加列都是 IF NOT EXISTS；列名逐个断言（迁移里表名/列名跨行拼接）
    assert up.count("ADD COLUMN IF NOT EXISTS") >= 6
    for frag in (
        "stored_files ADD COLUMN IF NOT EXISTS layer_name",
        "quality_reports ADD COLUMN IF NOT EXISTS score_overall",
        "version_number INTEGER NOT NULL DEFAULT 1",
        "snapshot_data JSON",
        "diff_data JSON",
        "is_rollback BOOLEAN NOT NULL DEFAULT false",
    ):
        assert frag in up, frag


def test_upgrade_fixes_types():
    src = MIG.read_text(encoding="utf-8")
    up = src.split("def downgrade")[0]
    assert "properties TYPE JSONB" in up
    assert "properties::jsonb" in up
    assert "detected_crs TYPE VARCHAR(2048)" in up


def test_downgrade_reverses():
    src = MIG.read_text(encoding="utf-8")
    down = src.split("def downgrade")[1]
    assert "DROP COLUMN IF EXISTS layer_name" in down
    assert "DROP COLUMN IF EXISTS score_overall" in down
    assert "properties TYPE JSON" in down


def test_orm_has_missing_columns():
    from app.models.stored_file import StoredFile
    from app.models.quality_report import QualityReport
    from app.models.source_data import StandardVersion

    assert "layer_name" in StoredFile.__table__.c
    assert "score_overall" in QualityReport.__table__.c
    for col in ("version_number", "snapshot_data", "diff_data", "is_rollback"):
        assert col in StandardVersion.__table__.c, col


def test_quality_report_json_columns_are_jsonb():
    from sqlalchemy.dialects.postgresql import JSONB
    from app.models.quality_report import QualityReport

    for col in ("checked_layers", "score_evaluated", "evaluation_errors"):
        assert isinstance(QualityReport.__table__.c[col].type, JSONB), col
