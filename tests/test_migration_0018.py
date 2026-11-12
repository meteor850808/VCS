"""0018 迁移的静态一致性（不连数据库）。

守住五件事：
1. 链指针接在 0017 之后；
2. 类型目标清单 = 模型里 8 个 JSONB 列 + `score_overall`，且改窄前有越界预检；
3. 37 个 NOT NULL 目标与模型声明一致，且「先查 NULL 再 SET NOT NULL」；
4. 索引增删都按 `to_regclass` 判存在；`key_groups.name` 是「非唯一 → 唯一」；
5. 三张要素父表的 `stored_file_id` 外键被删除（模型刻意不建）。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "0018_model_schema_alignment.py"

EXPECTED_JSONB = {
    ("quality_reports", "attribute_summary"),
    ("quality_reports", "topology_summary"),
    ("quality_reports", "topology_config"),
    ("quality_rule_results", "detail"),
    ("quality_rules", "params"),
    ("source_fields", "allowed_values"),
    ("standard_versions", "snapshot_data"),
    ("standard_versions", "diff_data"),
}

EXPECTED_NOT_NULL = {
    ("line_features", "stored_file_id"),
    ("point_features", "stored_file_id"),
    ("polygon_features", "stored_file_id"),
    ("quality_daily_snapshots", "checked_files"),
    ("quality_daily_snapshots", "created_at"),
    ("quality_daily_snapshots", "total_files"),
    ("quality_daily_snapshots", "violations_total"),
    ("quality_plans", "created_at"),
    ("quality_plans", "updated_at"),
    ("quality_rule_results", "created_at"),
    ("quality_rule_results", "failed_count"),
    ("quality_rule_results", "report_id"),
    ("quality_rule_results", "total_checked"),
    ("quality_rules", "created_at"),
    ("quality_rules", "enabled"),
    ("quality_rules", "field_pattern"),
    ("quality_rules", "is_auto"),
    ("quality_rules", "severity"),
    ("quality_rules", "updated_at"),
    ("quality_violations", "created_at"),
    ("quality_violations", "report_id"),
    ("source_dictionaries", "created_at"),
    ("source_dictionaries", "is_system"),
    ("source_fields", "created_at"),
    ("source_fields", "data_type"),
    ("source_fields", "is_primary_key"),
    ("source_fields", "is_required"),
    ("source_fields", "nullable"),
    ("source_fields", "ordering"),
    ("source_tables", "created_at"),
    ("source_tables", "is_active"),
    ("source_tables", "ordering"),
    ("standard_layer_group_tables", "created_at"),
    ("standard_versions", "created_at"),
    ("stored_file_layers", "created_at"),
    ("stored_files", "chunk_index"),
    ("stored_files", "total_chunks"),
}

FEATURE_FKS = {
    ("point_features", "point_features_stored_file_id_fkey"),
    ("line_features", "line_features_stored_file_id_fkey"),
    ("polygon_features", "polygon_features_stored_file_id_fkey"),
}


def _source() -> str:
    return MIG.read_text(encoding="utf-8")


def _literal(name: str):
    """从迁移源码里取字面量常量（不能 import：项目根 `alembic/` 会遮蔽已安装包）。"""
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"迁移里找不到常量 {name}")


def _upgrade_body() -> str:
    return _source().split("def upgrade")[1].split("def downgrade")[0]


def test_revision_chain():
    src = _source()
    assert 'revision = "0018_model_schema_alignment"' in src
    assert 'down_revision = "0017_ownership_fk_alignment"' in src


def test_jsonb_targets_match_model():
    targets = {tuple(x) for x in _literal("_JSONB_COLUMNS")}
    assert targets == EXPECTED_JSONB
    assert len(_literal("_JSONB_COLUMNS")) == 8

    import app.models  # noqa: F401  注册全部模型
    from app.db.session import Base

    for table, column in targets:
        col_type = Base.metadata.tables[table].c[column].type
        assert col_type.__class__.__name__.upper() == "JSONB", f"{table}.{column} 模型未改成 JSONB"


def test_score_overall_guard_before_narrowing():
    src = _source()
    assert '_SCORE_OVERALL = ("quality_reports", "score_overall")' in src
    up = _upgrade_body()
    # 越界预检必须先于 ALTER TYPE
    assert up.index("out_of_range") < up.index("TYPE NUMERIC(5, 4)")
    assert 'TYPE NUMERIC(5, 4) USING' in up


def test_not_null_targets_and_guard_order():
    targets = {tuple(x) for x in _literal("_NOT_NULL")}
    assert targets == EXPECTED_NOT_NULL
    assert len(_literal("_NOT_NULL")) == 37

    up = _upgrade_body()
    assert "_guard_no_nulls(bind)" in up
    # 必须先跑 NULL 预检，再逐列 SET NOT NULL
    assert up.index("_guard_no_nulls(bind)") < up.index("SET NOT NULL")


def test_not_null_targets_are_not_null_in_model():
    import app.models  # noqa: F401
    from app.db.session import Base

    for table, column in _literal("_NOT_NULL"):
        col = Base.metadata.tables[table].c[column]
        assert col.nullable is False, f"{table}.{column} 模型是可空列，不该收紧"


def test_comments_match_model_comments():
    """注释清单必须与 ORM 的 comment 一字不差，否则等于把漂移写进迁移。"""
    import app.models  # noqa: F401
    from app.db.session import Base

    for table, column, comment in _literal("_COMMENTS"):
        col = Base.metadata.tables[table].c[column]
        assert col.comment == comment, f"{table}.{column} 注释与模型不一致"


def test_index_ops_are_guarded_and_rename_key_groups_name():
    src = _source()
    creates = _literal("_CREATE_INDEXES")
    drops = _literal("_DROP_INDEXES")

    # 判存在后再动手
    assert "def _relation_exists" in src
    assert "to_regclass(:n)" in src
    assert "_relation_exists(bind, name)" in src
    assert "DROP INDEX" in src

    # 名字不重复
    for ops in (creates, drops):
        names = [row[0] for row in ops]
        assert len(names) == len(set(names))

    create_map = {row[0]: row for row in creates}
    drop_map = {row[0]: row for row in drops}

    # key_groups.name：非唯一 → 唯一
    assert drop_map["ix_key_groups_name"][3] is False
    assert create_map["ix_key_groups_name"][3] is True
    assert create_map["ix_key_groups_name"][2] == ["name"]

    # 要素表的重复 GIST 必须被删，且不重建同名索引
    for table in ("point_features", "line_features", "polygon_features"):
        dup = f"idx_{table}_geometry"
        assert dup in drop_map
        assert dup not in create_map
        assert f"ix_{table}_geometry" not in create_map  # 模型已声明，无需新建


def test_gin_indexes_only_on_properties():
    gin = [row for row in _literal("_CREATE_INDEXES") if row[4] == "gin"]
    assert {row[1] for row in gin} == {"point_features", "line_features", "polygon_features"}
    assert all(row[2] == ["properties"] for row in gin)
    src = _source()
    assert "不是 jsonb，无法建 GIN 索引" in src


def test_feature_foreign_keys_are_dropped():
    drops = {tuple(x) for x in _literal("_DROP_FOREIGN_KEYS")}
    assert drops == FEATURE_FKS
    up = _upgrade_body()
    assert "_DROP_FOREIGN_KEYS" in up
    assert "DROP CONSTRAINT" in up


def test_unique_constraint_rename_targets():
    assert [tuple(x) for x in _literal("_DROP_UNIQUE_CONSTRAINTS")] == [
        ("key_groups", "uq_key_groups_name")
    ]
    rename = [tuple(x) for x in _literal("_RENAME_UNIQUE_CONSTRAINTS")]
    assert rename == [
        (
            "quality_daily_snapshots",
            "quality_daily_snapshots_api_key_id_snapshot_date_key",
            "uq_qds_api_date",
            ["api_key_id", "snapshot_date"],
        )
    ]
    # 迁移只改名字，不改模型里的约束名
    from app.models.quality_daily_snapshot import QualityDailySnapshot

    names = {
        c.name for c in QualityDailySnapshot.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_qds_api_date" in names


def test_downgrade_documented_as_lossy():
    down = _source().split("def downgrade")[1]
    assert "不可完全还原" in down
    # 降级要能重建被删的索引与唯一约束
    assert "_DROP_INDEXES" in down
    assert "ADD CONSTRAINT" in down
    assert "DROP NOT NULL" in down
