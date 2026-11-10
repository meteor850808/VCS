"""0014 迁移与 ORM 模型的静态一致性（不连数据库）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0014_scoring_profile.py"


def test_revision_chain():
    src = MIG.read_text(encoding="utf-8")
    assert 'revision = "0014_scoring_profile"' in src
    assert 'down_revision = "0013_cad_enhancement"' in src


def test_upgrade_adds_three_columns():
    src = MIG.read_text(encoding="utf-8")
    for col in ("scoring_profile", "score_evaluated", "evaluation_errors"):
        assert f'op.add_column("quality_reports"' in src
        assert col in src


def test_upgrade_relaxes_scoring_columns():
    src = MIG.read_text(encoding="utf-8")
    for col in ("completeness_score", "score_completeness", "score_accuracy",
                "score_consistency", "score_uniqueness", "score_topology"):
        assert f'"{col}", nullable=True' in src


def test_downgrade_is_symmetric():
    src = MIG.read_text(encoding="utf-8")
    down = src.split("def downgrade")[1]
    for col in ("scoring_profile", "score_evaluated", "evaluation_errors"):
        assert col in down
    assert down.count("nullable=False") == 6
    assert "completeness_score = COALESCE(completeness_score, 0.0)" in down


def test_orm_model_matches():
    from app.models.quality_report import QualityReport

    cols = QualityReport.__table__.c
    assert cols.scoring_profile.nullable is False
    assert cols.score_evaluated.nullable is True
    assert cols.evaluation_errors.nullable is True
    for name in ("completeness_score", "score_completeness", "score_accuracy",
                 "score_consistency", "score_uniqueness", "score_topology"):
        assert cols[name].nullable is True


def test_apply_script_is_idempotent_and_leaves_alembic_alone():
    """脱管库（alembic_version 指向仓库不存在的 revision）用直连脚本落地 0014。"""
    src = (ROOT / "scripts" / "apply_0014.py").read_text(encoding="utf-8")
    # 幂等：加列用 IF NOT EXISTS，放宽可空性用 DROP NOT NULL（在可空列循环里）
    assert src.count("ADD COLUMN IF NOT EXISTS") == 3
    assert "DROP NOT NULL" in src
    assert "for col in NULLABLE_COLS" in src
    assert "completeness_score" in src
    # 不得伪造迁移历史（alembic_version 只在说明文字里出现）
    for verb in ("INSERT INTO alembic_version", "UPDATE alembic_version",
                 "DELETE FROM alembic_version"):
        assert verb not in src
