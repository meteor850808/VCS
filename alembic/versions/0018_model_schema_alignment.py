"""对齐模型与库：注释、JSONB / NUMERIC 类型、NOT NULL、索引、唯一约束、要素表外键

Revision ID: 0018_model_schema_alignment
Revises: 0017_ownership_fk_alignment
Create Date: 2026-11-12

背景（2026-11-12 复核）：把「只用迁移链从空库升级到 head」的库（`vcs_mig_probe`）和
生产库 `vcs_db` 都按 `alembic check` 的口径（`compare_type=True`、不比较 server_default、
排除扩展表 / 遗留表 / 运行时分区子表）比对 `Base.metadata`，两库都还有漂移，且漂移
集合不同——链上缺索引/类型，生产上缺注释/可空性/约束名。本迁移把两侧都收敛到模型：

1. **注释**（62 列）：生产库多数列没有 `COMMENT`。逐列比对 `col_description` 后只在
   不一致时下发 `COMMENT ON COLUMN`，重复执行是 no-op。
2. **类型**（链上 8 个 `json` → `jsonb`，1 个 `double precision` → `numeric(5,4)`）：
   生产库已是目标类型，探测后自动跳过。`score_overall` 改窄前先检查存量值是否越界
   （`numeric(5,4)` 只能存 ±9.9999），越界直接中止而不是静默截断。
3. **NOT NULL**（37 列）：模型声明非空、库里仍可空。收紧前逐列 `count(*) WHERE IS NULL`，
   有 NULL 就汇总报错中止——不猜默认值，不删数据。
4. **索引**：先把历史命名的索引删掉（`idx_qrr_report` 等），再按模型名建回来
   （`ix_<table>_<col>`）；删掉三张要素父表上与 `ix_*_geometry` 重复的
   `idx_*_geometry`（geoalchemy2 建表副产物）；补齐要素表的 GIN / 复合索引；
   `key_groups.name` 由「唯一约束 + 非唯一索引」收敛为单个唯一索引。
   全部按 `to_regclass` 判存在，两库现状不同也能各自跑通。
5. **唯一约束名**：`quality_daily_snapshots` 的
   `quality_daily_snapshots_api_key_id_snapshot_date_key` 重命名为模型名
   `uq_qds_api_date`（只改名字，不动数据）。
6. **外键**：链上 0001 建三张要素表时带了 `stored_file_id → stored_files` 的
   `ON DELETE CASCADE`；模型刻意不建（子任务先写要素、父记录后提交，FK 会违反时序），
   删文件走 `feature_store.drop_partition` 显式清理分区。本迁移删掉这 3 个 FK。

降级是**尽力而为且不可完全还原**的：注释的原始值（多数为空）未被记录，`jsonb → json`
与 `numeric → double precision` 会丢精度/顺序，被删的 `idx_*` 只能按列定义重建。
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "0018_model_schema_alignment"
down_revision = "0017_ownership_fk_alignment"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# ── 1. 列注释（模型 comment → 库）───────────────────────────────────────────
_COMMENTS: list[tuple[str, str, str]] = [
    ("api_keys", "group_id", "所属分组 ID，同组 Key 共享数据访问权限"),
    ("data_standards", "category", "national (国标) / industry (行标) / custom (自定义)"),
    ("data_standards", "code", "标准编码（导入回传的匹配键，create 时可填）"),
    ("data_standards", "status", "draft (草稿) / published (发布) / archived (归档)"),
    ("dictionary_items", "parent_code", "父级码值，支持层级结构"),
    ("field_dict_links", "filter_condition", "码值筛选条件"),
    ("key_groups", "name", "组名称（唯一）"),
    ("line_features", "source_name", "来源文件名（不含路径，如 data.geojson）"),
    ("line_features", "stored_file_id", "数据源/文件 ID，也是 LIST 分区键（每源一个分区）"),
    ("point_features", "source_name", "来源文件名（不含路径，如 data.geojson）"),
    ("point_features", "stored_file_id", "数据源/文件 ID，也是 LIST 分区键（每源一个分区）"),
    ("polygon_features", "source_name", "来源文件名（不含路径，如 data.geojson）"),
    ("polygon_features", "stored_file_id", "数据源/文件 ID，也是 LIST 分区键（每源一个分区）"),
    ("quality_plans", "extra", "扩展字段（预留）"),
    ("quality_reports", "attribute_summary", "{total,passed,failed,by_rule:{...}}"),
    ("quality_reports", "checked_layers", "质检圈定的图层名列表（v2.10.5）；NULL=全部图层"),
    ("quality_reports", "topology_config", "拓扑检查配置快照"),
    ("quality_reports", "topology_summary", "{total,passed,failed,by_rule:{...}}"),
    ("quality_rule_results", "detail", "{sample_values:[...], top_errors:[...]}"),
    ("quality_rules", "field_pattern", "匹配字段名（支持通配符 * 和 ?）"),
    ("quality_rules", "is_auto", "True=从数据标准自动生成, False=用户手动创建"),
    (
        "quality_rules",
        "params",
        "{allowed_values:[...], max_length:100, min:0, max:999, pattern:...}",
    ),
    (
        "quality_rules",
        "rule_type",
        "NOT_NULL / UNIQUE / VALUE_IN_SET / TYPE_MATCH / LENGTH_LIMIT / PATTERN_MATCH / RANGE_CHECK",
    ),
    ("source_fields", "allowed_values", "兼容旧版：内嵌允许值列表（推荐用字典）"),
    ("source_fields", "data_type", "数据类型：string/integer/float/date/geometry"),
    ("source_fields", "field_alias", "字段别名（中文名）"),
    ("source_fields", "field_type", "字段分类：属性/几何/时间"),
    ("source_fields", "precision", "小数精度"),
    ("source_tables", "geometry_type", "Point/Polygon/None=属性表"),
    ("source_tables", "source", "数据来源：国标/行标/自定义"),
    ("source_tables", "version", "版本号：GB/T 13923-2022"),
    ("standard_dictionaries", "is_system", "系统预置字典不可删除"),
    ("standard_dictionaries", "scope", "standard (标准级) / global (全局共享)"),
    ("standard_dictionaries", "standard_id", "所属标准 ID，NULL 表示全局字典"),
    ("standard_field_dict_links", "filter_condition", "码值筛选条件，如 'status=active'"),
    ("standard_field_dict_links", "is_primary", "是否为主要值域"),
    ("standard_fields", "data_type", "string/integer/float/date/datetime/geometry/boolean"),
    ("standard_fields", "field_alias", "中文名，如 '控制点名称'"),
    ("standard_fields", "precision", "小数精度，如 float(12,3)"),
    (
        "standard_layer_group_tables",
        "is_primary",
        "是否主归属（每表唯一，与 standard_tables.layer_id 保持一致）",
    ),
    ("standard_layer_group_tables", "layer_group_id", "层组 ID"),
    ("standard_layer_group_tables", "table_id", "数据表 ID"),
    ("standard_layer_groups", "parent_id", "父层组 ID，NULL 表示顶层"),
    ("standard_tables", "geometry_type", "Point/LineString/Polygon/None"),
    ("standard_tables", "table_type", "spatial (空间) / attribute (属性) / relationship (关系)"),
    ("stored_file_layers", "color_index", "DXF ACI 颜色索引 (1-255)"),
    ("stored_file_layers", "color_rgb", "真彩色 'R,G,B'（如有）"),
    ("stored_file_layers", "feature_count", "要素总数（冗余 = 三类之和，便于排序）"),
    ("stored_file_layers", "is_frozen", "冻结状态"),
    ("stored_file_layers", "is_visible", "可见性"),
    ("stored_file_layers", "linetype", "线型名"),
    (
        "stored_files",
        "cad_metadata",
        "CAD 元数据缓存: {version, units, extent_min, extent_max, entity_type_counts, ...}",
    ),
    ("stored_files", "cad_preview_path", "CAD 预览文件路径 (data/previews/{file_id}.svg)"),
    ("stored_files", "chunk_index", "分片序号，0 表示完整文件"),
    (
        "stored_files",
        "detected_crs",
        "检测到的坐标系: EPSG:4490 / EPSG:4326 / 完整 CRS WKT（如 PROJCS）",
    ),
    (
        "stored_files",
        "detected_crs_srid",
        "入库要素实际使用的 SRID（EPSG 编码或 VCS-CUSTOM 自定义注册号）；NULL=历史数据未解析",
    ),
    ("stored_files", "file_hash", "文件 SHA-256 哈希，用于去重"),
    (
        "stored_files",
        "geometry_type",
        "几何细分类型: Point/LineString/Polygon/MultiPolygon/3d/table/null",
    ),
    ("stored_files", "line_count", "线要素数（聚合冗余列）"),
    ("stored_files", "point_count", "点要素数（聚合冗余列）"),
    ("stored_files", "polygon_count", "面要素数（聚合冗余列）"),
    ("stored_files", "total_chunks", "总分片数"),
]

# ── 2. 类型：json → jsonb（链上缺失）───────────────────────────────────────
_JSONB_COLUMNS: list[tuple[str, str]] = [
    ("quality_reports", "attribute_summary"),
    ("quality_reports", "topology_summary"),
    ("quality_reports", "topology_config"),
    ("quality_rule_results", "detail"),
    ("quality_rules", "params"),
    ("source_fields", "allowed_values"),
    ("standard_versions", "snapshot_data"),
    ("standard_versions", "diff_data"),
]

# quality_reports.score_overall：float8 → numeric(5,4)（与快照表各维度分一致）
_SCORE_OVERALL = ("quality_reports", "score_overall")
_NUMERIC_LIMIT = "9.9999"

# ── 3. 收紧为 NOT NULL（模型非空、库可空）──────────────────────────────────
_NOT_NULL: list[tuple[str, str]] = [
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
]

# ── 4. 索引：(名字, 表, 列, 是否唯一, method) ───────────────────────────────
# 先删历史命名（与 ix_<table>_<col> 重名或纯重复），再按模型名建回来。
_DROP_INDEXES: list[tuple[str, str, list[str], bool, str]] = [
    # 与 ix_*_geometry 完全重复的 GIST（geoalchemy2 建表副产物）
    ("idx_point_features_geometry", "point_features", ["geometry"], False, "gist"),
    ("idx_line_features_geometry", "line_features", ["geometry"], False, "gist"),
    ("idx_polygon_features_geometry", "polygon_features", ["geometry"], False, "gist"),
    # 短名 → 模型名
    ("idx_qrr_report", "quality_rule_results", ["report_id"], False, "btree"),
    ("idx_qrules_api_key", "quality_rules", ["api_key_id"], False, "btree"),
    ("idx_qv_field", "quality_violations", ["field_name"], False, "btree"),
    ("idx_qv_report", "quality_violations", ["report_id"], False, "btree"),
    ("idx_sd_api_key", "source_dictionaries", ["api_key_id"], False, "btree"),
    ("idx_sf_table", "source_fields", ["table_id"], False, "btree"),
    ("idx_st_api_key", "source_tables", ["api_key_id"], False, "btree"),
    ("idx_sv_standard", "standard_versions", ["standard_id"], False, "btree"),
    ("ix_slgt_layer_group_id", "standard_layer_group_tables", ["layer_group_id"], False, "btree"),
    ("ix_slgt_table_id", "standard_layer_group_tables", ["table_id"], False, "btree"),
    # 与唯一约束 (api_key_id, snapshot_date) 前缀重复
    ("ix_quality_daily_snapshots_api_key_id", "quality_daily_snapshots", ["api_key_id"], False, "btree"),
    # 与 UNIQUE 约束 quality_plans_name_key 完全重复
    ("ix_quality_plans_name", "quality_plans", ["name"], True, "btree"),
    # 由非唯一重建为唯一（key_groups.name）
    ("ix_key_groups_name", "key_groups", ["name"], False, "btree"),
]

_CREATE_INDEXES: list[tuple[str, str, list[str], bool, str]] = [
    ("idx_qds_date", "quality_daily_snapshots", ["snapshot_date"], False, "btree"),
    ("ix_key_groups_name", "key_groups", ["name"], True, "btree"),
    ("ix_line_features_props", "line_features", ["properties"], False, "gin"),
    ("ix_line_features_source_name", "line_features", ["source_name"], False, "btree"),
    ("ix_line_features_src_idx", "line_features", ["stored_file_id", "feature_index"], False, "btree"),
    ("ix_point_features_props", "point_features", ["properties"], False, "gin"),
    ("ix_point_features_src_idx", "point_features", ["stored_file_id", "feature_index"], False, "btree"),
    ("ix_polygon_features_props", "polygon_features", ["properties"], False, "gin"),
    ("ix_polygon_features_source_name", "polygon_features", ["source_name"], False, "btree"),
    ("ix_polygon_features_src_idx", "polygon_features", ["stored_file_id", "feature_index"], False, "btree"),
    ("ix_quality_rule_results_report_id", "quality_rule_results", ["report_id"], False, "btree"),
    ("ix_quality_rules_api_key_id", "quality_rules", ["api_key_id"], False, "btree"),
    ("ix_quality_violations_field_name", "quality_violations", ["field_name"], False, "btree"),
    ("ix_quality_violations_report_id", "quality_violations", ["report_id"], False, "btree"),
    ("ix_source_dictionaries_api_key_id", "source_dictionaries", ["api_key_id"], False, "btree"),
    ("ix_source_fields_table_id", "source_fields", ["table_id"], False, "btree"),
    ("ix_source_tables_api_key_id", "source_tables", ["api_key_id"], False, "btree"),
    (
        "ix_standard_layer_group_tables_layer_group_id",
        "standard_layer_group_tables",
        ["layer_group_id"],
        False,
        "btree",
    ),
    ("ix_standard_layer_group_tables_table_id", "standard_layer_group_tables", ["table_id"], False, "btree"),
    ("ix_standard_versions_api_key_id", "standard_versions", ["api_key_id"], False, "btree"),
    ("ix_standard_versions_standard_id", "standard_versions", ["standard_id"], False, "btree"),
]

# ── 5. 唯一约束 ────────────────────────────────────────────────────────────
# key_groups：模型用「唯一索引」，库里有「唯一约束 + 非唯一索引」，删掉冗余约束
_DROP_UNIQUE_CONSTRAINTS: list[tuple[str, str]] = [
    ("key_groups", "uq_key_groups_name"),
]

# quality_daily_snapshots：只改约束名（列组合不变）
_RENAME_UNIQUE_CONSTRAINTS: list[tuple[str, str, str, list[str]]] = [
    (
        "quality_daily_snapshots",
        "quality_daily_snapshots_api_key_id_snapshot_date_key",
        "uq_qds_api_date",
        ["api_key_id", "snapshot_date"],
    ),
]

# ── 6. 要素父表外键（模型刻意不建，见 app/models/spatial_feature.py）───────
_DROP_FOREIGN_KEYS: list[tuple[str, str]] = [
    ("point_features", "point_features_stored_file_id_fkey"),
    ("line_features", "line_features_stored_file_id_fkey"),
    ("polygon_features", "polygon_features_stored_file_id_fkey"),
]

_COLUMN_INFO = sa.text(
    """
    SELECT c.data_type AS data_type,
           c.is_nullable AS is_nullable,
           c.udt_name AS udt_name,
           c.numeric_precision AS numeric_precision,
           c.numeric_scale AS numeric_scale
    FROM information_schema.columns c
    WHERE c.table_schema = current_schema()
      AND c.table_name = :tbl
      AND c.column_name = :col
    """
)

_COLUMN_COMMENT = sa.text(
    """
    SELECT col_description(to_regclass(:tbl), a.attnum)
    FROM pg_attribute a
    WHERE a.attrelid = to_regclass(:tbl)
      AND a.attname = :col
      AND NOT a.attisdropped
    """
)


def _q(ident: str) -> str:
    """双引号包裹标识符（表名/列名均来自本文件常量）。"""
    return '"' + ident.replace('"', '""') + '"'


def _lit(value: str) -> str:
    """SQL 字符串字面量（DDL 里不能绑定参数）。"""
    return "'" + value.replace("'", "''") + "'"


def _column_info(bind, table: str, column: str):
    row = bind.execute(_COLUMN_INFO, {"tbl": table, "col": column}).mappings().first()
    if row is None:
        raise RuntimeError(f"列不存在：{table}.{column}（本迁移的前提是 0001~0017 已执行）")
    return row


def _relation_exists(bind, name: str) -> bool:
    return bind.execute(sa.text("SELECT to_regclass(:n)"), {"n": name}).scalar() is not None


def _constraint_exists(bind, table: str, name: str) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conname = :n AND conrelid = to_regclass(:t)"
            ),
            {"n": name, "t": table},
        ).scalar()
        is not None
    )


def _create_index(bind, name: str, table: str, columns: list[str], unique: bool, method: str) -> None:
    if _relation_exists(bind, name):
        return
    if method == "gin":
        # GIN 只能建在 jsonb 上；若仍是 json 说明第 2 步没覆盖到，明确报错而不是让 PG 抛难懂的错
        for column in columns:
            if _column_info(bind, table, column)["udt_name"] != "jsonb":
                raise RuntimeError(
                    f"{table}.{column} 不是 jsonb，无法建 GIN 索引 {name}"
                )
    unique_sql = "UNIQUE " if unique else ""
    cols = ", ".join(_q(c) for c in columns)
    bind.execute(
        sa.text(f"CREATE {unique_sql}INDEX {_q(name)} ON {_q(table)} USING {method} ({cols})")
    )


def _drop_index(bind, name: str) -> None:
    if _relation_exists(bind, name):
        bind.execute(sa.text(f"DROP INDEX {_q(name)}"))


def _guard_no_nulls(bind) -> None:
    """收紧 NOT NULL 前逐列统计 NULL，有值就汇总报错（不猜默认值）。"""
    selects = " UNION ALL ".join(
        f"SELECT {_lit(f'{t}.{c}')} AS col, count(*) AS n "
        f"FROM {_q(t)} WHERE {_q(c)} IS NULL"
        for t, c in _NOT_NULL
    )
    rows = bind.execute(sa.text(selects)).all()
    problems = [f"{col} 有 {n} 行 NULL" for col, n in rows if n]
    if problems:
        raise RuntimeError(
            "收紧 NOT NULL 中止：以下列存在 NULL，请先补数据或改模型声明——" + "；".join(problems)
        )


def _guard_no_duplicates(bind, table: str, column: str, index_name: str) -> None:
    """建唯一索引前检查重复值，给出可读的报错而不是 PG 的 23505。"""
    dups = bind.execute(
        sa.text(
            f"SELECT count(*) FROM ("
            f"SELECT {_q(column)} FROM {_q(table)} "
            f"WHERE {_q(column)} IS NOT NULL "
            f"GROUP BY {_q(column)} HAVING count(*) > 1) d"
        )
    ).scalar()
    if dups:
        raise RuntimeError(
            f"建唯一索引 {index_name} 中止：{table}.{column} 有 {dups} 组重复值"
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 注释（仅在不一致时下发）────────────────────────────────────────
    for table, column, comment in _COMMENTS:
        current = bind.execute(_COLUMN_COMMENT, {"tbl": table, "col": column}).scalar()
        if current == comment:
            continue
        bind.execute(
            sa.text(f"COMMENT ON COLUMN {_q(table)}.{_q(column)} IS {_lit(comment)}")
        )

    # ── 2. 类型对齐 ───────────────────────────────────────────────────────
    for table, column in _JSONB_COLUMNS:
        info = _column_info(bind, table, column)
        if info["udt_name"] == "jsonb":
            continue
        if info["udt_name"] != "json":
            raise RuntimeError(
                f"{table}.{column} 期望 json/jsonb，实际是 {info['data_type']}，需人工确认"
            )
        bind.execute(
            sa.text(
                f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} "
                f"TYPE JSONB USING {_q(column)}::jsonb"
            )
        )

    table, column = _SCORE_OVERALL
    info = _column_info(bind, table, column)
    already_numeric = (
        info["data_type"] == "numeric"
        and info["numeric_precision"] == 5
        and info["numeric_scale"] == 4
    )
    if not already_numeric:
        out_of_range = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {_q(table)} WHERE {_q(column)} IS NOT NULL "
                f"AND ({_q(column)} > {_NUMERIC_LIMIT} OR {_q(column)} < -{_NUMERIC_LIMIT})"
            )
        ).scalar()
        if out_of_range:
            raise RuntimeError(
                f"{table}.{column} 有 {out_of_range} 行超出 numeric(5,4) 范围（±{_NUMERIC_LIMIT}），"
                "改窄会截断，已中止"
            )
        bind.execute(
            sa.text(
                f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} "
                f"TYPE NUMERIC(5, 4) USING {_q(column)}::numeric(5, 4)"
            )
        )

    # ── 3. 收紧 NOT NULL（先验证无 NULL）──────────────────────────────────
    _guard_no_nulls(bind)
    for table, column in _NOT_NULL:
        if _column_info(bind, table, column)["is_nullable"] == "NO":
            continue
        bind.execute(
            sa.text(f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} SET NOT NULL")
        )

    # ── 4. 索引：先删旧的，再建模型声明的（同事务内，对外无空窗）──────────
    for name, _table, _columns, _unique, _method in _DROP_INDEXES:
        _drop_index(bind, name)

    for name, table, columns, unique, method in _CREATE_INDEXES:
        if unique:
            _guard_no_duplicates(bind, table, columns[0], name)
        _create_index(bind, name, table, columns, unique, method)

    # ── 5. 唯一约束 ───────────────────────────────────────────────────────
    for table, name in _DROP_UNIQUE_CONSTRAINTS:
        if _constraint_exists(bind, table, name):
            bind.execute(sa.text(f"ALTER TABLE {_q(table)} DROP CONSTRAINT {_q(name)}"))

    for table, old_name, new_name, columns in _RENAME_UNIQUE_CONSTRAINTS:
        if _constraint_exists(bind, table, new_name):
            continue
        if _constraint_exists(bind, table, old_name):
            bind.execute(
                sa.text(
                    f"ALTER TABLE {_q(table)} RENAME CONSTRAINT {_q(old_name)} TO {_q(new_name)}"
                )
            )
        else:
            cols = ", ".join(_q(c) for c in columns)
            bind.execute(
                sa.text(f"ALTER TABLE {_q(table)} ADD CONSTRAINT {_q(new_name)} UNIQUE ({cols})")
            )

    # ── 6. 删掉要素父表上多余的外键（模型刻意不建）───────────────────────
    for table, name in _DROP_FOREIGN_KEYS:
        if _constraint_exists(bind, table, name):
            bind.execute(sa.text(f"ALTER TABLE {_q(table)} DROP CONSTRAINT {_q(name)}"))


def downgrade() -> None:
    """尽力回退：删除本迁移新建的索引/约束，重建历史命名索引，类型与可空性回退。

    不可完全还原：列注释的原始值、`jsonb` 的键顺序、`numeric` 的精度都已丢失。
    """
    bind = op.get_bind()

    # ── 逆向第 6 步：恢复要素父表的 stored_file_id 外键（链上原样）─────────
    for table, name in _DROP_FOREIGN_KEYS:
        if _constraint_exists(bind, table, name):
            continue
        bind.execute(
            sa.text(
                f"ALTER TABLE {_q(table)} ADD CONSTRAINT {_q(name)} "
                f'FOREIGN KEY ("stored_file_id") REFERENCES "stored_files" ("id") ON DELETE CASCADE'
            )
        )

    # ── 逆向第 5 步：约束名与 key_groups 的唯一约束 ──────────────────────
    for table, old_name, new_name, _columns in _RENAME_UNIQUE_CONSTRAINTS:
        if _constraint_exists(bind, table, old_name):
            continue
        if _constraint_exists(bind, table, new_name):
            bind.execute(
                sa.text(
                    f"ALTER TABLE {_q(table)} RENAME CONSTRAINT {_q(new_name)} TO {_q(old_name)}"
                )
            )

    for table, name in _DROP_UNIQUE_CONSTRAINTS:
        if _constraint_exists(bind, table, name):
            continue
        bind.execute(sa.text(f"ALTER TABLE {_q(table)} ADD CONSTRAINT {_q(name)} UNIQUE ({_q('name')})"))

    # ── 逆向第 4 步：索引 ────────────────────────────────────────────────
    for name, _table, _columns, _unique, _method in _CREATE_INDEXES:
        _drop_index(bind, name)

    for name, table, columns, unique, method in _DROP_INDEXES:
        _create_index(bind, name, table, columns, unique, method)

    # ── 逆向第 3 步：放宽 NOT NULL ───────────────────────────────────────
    for table, column in _NOT_NULL:
        if _column_info(bind, table, column)["is_nullable"] == "YES":
            continue
        bind.execute(
            sa.text(f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} DROP NOT NULL")
        )

    # ── 逆向第 2 步：类型回退 ────────────────────────────────────────────
    table, column = _SCORE_OVERALL
    info = _column_info(bind, table, column)
    if info["data_type"] == "numeric":
        bind.execute(
            sa.text(
                f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} "
                f"TYPE DOUBLE PRECISION USING {_q(column)}::double precision"
            )
        )

    for table, column in _JSONB_COLUMNS:
        if _column_info(bind, table, column)["udt_name"] != "jsonb":
            continue
        bind.execute(
            sa.text(
                f"ALTER TABLE {_q(table)} ALTER COLUMN {_q(column)} "
                f"TYPE JSON USING {_q(column)}::json"
            )
        )

    # ── 逆向第 1 步：清掉本迁移写入的注释（原始值未记录，只能置空）───────
    for table, column, comment in _COMMENTS:
        current = bind.execute(_COLUMN_COMMENT, {"tbl": table, "col": column}).scalar()
        if current != comment:
            continue
        bind.execute(sa.text(f"COMMENT ON COLUMN {_q(table)}.{_q(column)} IS NULL"))
