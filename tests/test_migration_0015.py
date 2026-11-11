"""0015 迁移与 ORM 模型的静态一致性（不连数据库）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0015_quality_rule_owner_nullable.py"


def test_revision_chain():
    src = MIG.read_text(encoding="utf-8")
    assert 'revision = "0015_quality_rule_owner_nullable"' in src
    assert 'down_revision = "0014_scoring_profile"' in src


def test_upgrade_relaxes_quality_rule_owner():
    src = MIG.read_text(encoding="utf-8")
    up = src.split("def downgrade")[0]
    assert '"quality_rules", "api_key_id"' in up
    assert "nullable=True" in up


def test_downgrade_removes_unrepresentable_rows_first():
    src = MIG.read_text(encoding="utf-8")
    down = src.split("def downgrade")[1]
    assert "DELETE FROM quality_rules WHERE api_key_id IS NULL" in down
    assert "nullable=False" in down


def test_orm_model_matches():
    from app.models.quality_rule import QualityRule

    col = QualityRule.__table__.c.api_key_id
    assert col.nullable is True
    # 外键仍指向 api_keys.id，只是允许为空
    assert [fk.target_fullname for fk in col.foreign_keys] == ["api_keys.id"]
