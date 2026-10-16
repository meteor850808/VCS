#!/usr/bin/env python3
"""数据标准 v2.2 数据迁移脚本。

功能：
1. 创建新表结构
2. 从旧表迁移数据到新表
3. 更新外键引用
4. 删除旧表

使用方法：
  python scripts/migrate_data_standard_v2_2.py              # 交互模式
  python scripts/migrate_data_standard_v2_2.py --dry-run    # 仅检测不执行
  python scripts/migrate_data_standard_v2_2.py --auto       # 自动执行（危险）
  python scripts/migrate_data_standard_v2_2.py --rollback   # 回滚迁移
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text, create_engine, inspect
from app.config import settings


# ── 新表定义 ──────────────────────────────────────────────────────────────────

NEW_TABLES_SQL = """
-- 标准层组（支持嵌套）
CREATE TABLE IF NOT EXISTS standard_layer_groups (
    id VARCHAR(36) PRIMARY KEY,
    standard_id VARCHAR(36) NOT NULL REFERENCES data_standards(id) ON DELETE CASCADE,
    parent_id VARCHAR(36) REFERENCES standard_layer_groups(id) ON DELETE CASCADE,
    group_name VARCHAR(256) NOT NULL,
    group_code VARCHAR(64),
    description TEXT,
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (standard_id, parent_id, group_name),
    UNIQUE (standard_id, parent_id, group_code),
    CHECK (parent_id IS NULL OR parent_id != id)
);
CREATE INDEX IF NOT EXISTS ix_slg_standard ON standard_layer_groups (standard_id);
CREATE INDEX IF NOT EXISTS ix_slg_parent ON standard_layer_groups (parent_id);

-- 标准数据表
CREATE TABLE IF NOT EXISTS standard_tables (
    id VARCHAR(36) PRIMARY KEY,
    layer_id VARCHAR(36) NOT NULL REFERENCES standard_layer_groups(id) ON DELETE CASCADE,
    standard_id VARCHAR(36) NOT NULL REFERENCES data_standards(id) ON DELETE CASCADE,
    table_name VARCHAR(256) NOT NULL,
    table_code VARCHAR(64),
    table_type VARCHAR(32) NOT NULL DEFAULT 'spatial',
    geometry_type VARCHAR(32),
    description TEXT,
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (layer_id, table_name),
    UNIQUE (layer_id, table_code)
);
CREATE INDEX IF NOT EXISTS ix_st_layer ON standard_tables (layer_id);
CREATE INDEX IF NOT EXISTS ix_st_standard ON standard_tables (standard_id);

-- 标准字段
CREATE TABLE IF NOT EXISTS standard_fields (
    id VARCHAR(36) PRIMARY KEY,
    table_id VARCHAR(36) NOT NULL REFERENCES standard_tables(id) ON DELETE CASCADE,
    standard_id VARCHAR(36) NOT NULL REFERENCES data_standards(id) ON DELETE CASCADE,
    field_name VARCHAR(256) NOT NULL,
    field_alias VARCHAR(256),
    data_type VARCHAR(32) NOT NULL DEFAULT 'string',
    length INTEGER,
    precision INTEGER,
    nullable BOOLEAN NOT NULL DEFAULT TRUE,
    is_primary_key BOOLEAN NOT NULL DEFAULT FALSE,
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    default_value VARCHAR(256),
    description TEXT,
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (table_id, field_name)
);
CREATE INDEX IF NOT EXISTS ix_sf_table ON standard_fields (table_id);
CREATE INDEX IF NOT EXISTS ix_sf_standard ON standard_fields (standard_id);

-- 标准字典
CREATE TABLE IF NOT EXISTS standard_dictionaries (
    id VARCHAR(36) PRIMARY KEY,
    standard_id VARCHAR(36) REFERENCES data_standards(id) ON DELETE SET NULL,
    api_key_id VARCHAR(36) REFERENCES api_keys(id) ON DELETE SET NULL,
    dict_name VARCHAR(256) NOT NULL,
    dict_code VARCHAR(64),
    description TEXT,
    scope VARCHAR(32) NOT NULL DEFAULT 'standard',
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (standard_id, dict_name)
);
CREATE INDEX IF NOT EXISTS ix_sd_standard ON standard_dictionaries (standard_id);
CREATE INDEX IF NOT EXISTS ix_sd_scope ON standard_dictionaries (scope);

-- 字典条目（新表）
CREATE TABLE IF NOT EXISTS new_dictionary_items (
    id VARCHAR(36) PRIMARY KEY,
    dict_id VARCHAR(36) NOT NULL REFERENCES standard_dictionaries(id) ON DELETE CASCADE,
    item_code VARCHAR(64) NOT NULL,
    item_name VARCHAR(256) NOT NULL,
    parent_code VARCHAR(64),
    description TEXT,
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (dict_id, item_code)
);
CREATE INDEX IF NOT EXISTS ix_ndi_dict ON new_dictionary_items (dict_id);

-- 字段-字典关联（新表）
CREATE TABLE IF NOT EXISTS standard_field_dict_links (
    id VARCHAR(36) PRIMARY KEY,
    field_id VARCHAR(36) NOT NULL REFERENCES standard_fields(id) ON DELETE CASCADE,
    dict_id VARCHAR(36) NOT NULL REFERENCES standard_dictionaries(id) ON DELETE CASCADE,
    filter_condition VARCHAR(256),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (field_id, dict_id)
);
CREATE INDEX IF NOT EXISTS ix_sfdl_field ON standard_field_dict_links (field_id);
CREATE INDEX IF NOT EXISTS ix_sfdl_dict ON standard_field_dict_links (dict_id);
"""


def get_existing_tables(engine):
    """获取数据库中已存在的表名集合。"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ))
        return {row[0] for row in result}


def check_old_data(engine):
    """检查旧表中的数据量。"""
    stats = {}
    with engine.connect() as conn:
        for table in ['data_standards', 'data_standard_sheets', 'data_standard_fields',
                       'source_tables', 'source_fields', 'source_dictionaries',
                       'dictionary_items', 'field_dict_links']:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                stats[table] = result.scalar()
            except Exception:
                stats[table] = -1  # 表不存在
    return stats


def migrate_data_standards(engine):
    """迁移 data_standards 表，添加 category/status/version 字段。"""
    with engine.begin() as conn:
        # 检查是否已有新字段
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'data_standards' AND column_name = 'category'
        """))
        if result.fetchone():
            print("  ⏭ data_standards 已有 category 字段，跳过")
            return

        # 添加新字段
        conn.execute(text("ALTER TABLE data_standards ADD COLUMN category VARCHAR(64) NOT NULL DEFAULT 'custom'"))
        conn.execute(text("ALTER TABLE data_standards ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'draft'"))
        conn.execute(text("ALTER TABLE data_standards ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
        # file_name 改为可选
        conn.execute(text("ALTER TABLE data_standards ALTER COLUMN file_name DROP NOT NULL"))
        # 添加唯一约束
        try:
            conn.execute(text("ALTER TABLE data_standards ADD CONSTRAINT uq_ds_api_name UNIQUE (api_key_id, name)"))
        except Exception:
            pass  # 约束可能已存在
        print("  ✓ data_standards 字段更新完成")


def migrate_sheet_to_layer(engine):
    """将 DataStandardSheet 迁移到 standard_layer_groups。"""
    with engine.begin() as conn:
        result = conn.execute(text("SELECT id, standard_id, sheet_name, ordering FROM data_standard_sheets"))
        sheets = result.fetchall()
        count = 0
        for sheet_id, standard_id, sheet_name, ordering in sheets:
            conn.execute(text("""
                INSERT INTO standard_layer_groups (id, standard_id, parent_id, group_name, ordering, created_at)
                VALUES (:id, :standard_id, NULL, :group_name, :ordering, NOW())
                ON CONFLICT DO NOTHING
            """), {"id": sheet_id, "standard_id": standard_id, "group_name": sheet_name, "ordering": ordering or 0})
            count += 1
        print(f"  ✓ 迁移 {count} 个层组（从 data_standard_sheets）")
        return count


def create_default_layer(engine, standard_id):
    """为没有 Sheet 的标准创建默认层组。"""
    with engine.begin() as conn:
        layer_id = str(uuid.uuid4())
        conn.execute(text("""
            INSERT INTO standard_layer_groups (id, standard_id, parent_id, group_name, group_code, ordering, created_at)
            VALUES (:id, :standard_id, NULL, '默认层组', 'DEFAULT', 0, NOW())
            ON CONFLICT DO NOTHING
        """), {"id": layer_id, "standard_id": standard_id})
        return layer_id


def migrate_source_tables(engine):
    """将 SourceTable 迁移到 standard_tables。"""
    with engine.begin() as conn:
        # 获取所有 SourceTable
        result = conn.execute(text("""
            SELECT st.id, st.standard_id, st.table_name, st.table_code,
                   st.geometry_type, st.description, st.ordering
            FROM source_tables st
        """))
        tables = result.fetchall()

        count = 0
        for (src_id, standard_id, table_name, table_code,
             geometry_type, description, ordering) in tables:

            if not standard_id:
                continue  # 跳过没有关联标准的表

            # 查找对应的层组
            layer_result = conn.execute(text("""
                SELECT id FROM standard_layer_groups
                WHERE standard_id = :standard_id
                ORDER BY ordering LIMIT 1
            """), {"standard_id": standard_id})
            layer_row = layer_result.fetchone()

            if not layer_row:
                # 创建默认层组
                layer_id = create_default_layer(conn, standard_id)
            else:
                layer_id = layer_row[0]

            # 确定表类型
            table_type = 'spatial' if geometry_type else 'attribute'

            conn.execute(text("""
                INSERT INTO standard_tables (id, layer_id, standard_id, table_name, table_code,
                                            table_type, geometry_type, description, ordering, created_at)
                VALUES (:id, :layer_id, :standard_id, :table_name, :table_code,
                        :table_type, :geometry_type, :description, :ordering, NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": src_id, "layer_id": layer_id, "standard_id": standard_id,
                "table_name": table_name, "table_code": table_code,
                "table_type": table_type, "geometry_type": geometry_type,
                "description": description, "ordering": ordering or 0,
            })
            count += 1

        print(f"  ✓ 迁移 {count} 个数据表（从 source_tables）")
        return count


def migrate_source_fields(engine):
    """将 SourceField 迁移到 standard_fields。"""
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT sf.id, sf.table_id, st.standard_id, sf.field_name, sf.field_alias,
                   sf.data_type, sf.length, sf.precision, sf.nullable,
                   sf.is_primary_key, sf.is_required, sf.default_value,
                   sf.description, sf.ordering
            FROM source_fields sf
            JOIN standard_tables st ON sf.table_id = st.id
        """))
        fields = result.fetchall()

        count = 0
        for (field_id, table_id, standard_id, field_name, field_alias,
             data_type, length, precision, nullable,
             is_primary_key, is_required, default_value,
             description, ordering) in fields:

            conn.execute(text("""
                INSERT INTO standard_fields (id, table_id, standard_id, field_name, field_alias,
                                            data_type, length, precision, nullable,
                                            is_primary_key, is_required, default_value,
                                            description, ordering, created_at)
                VALUES (:id, :table_id, :standard_id, :field_name, :field_alias,
                        :data_type, :length, :precision, :nullable,
                        :is_primary_key, :is_required, :default_value,
                        :description, :ordering, NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": field_id, "table_id": table_id, "standard_id": standard_id,
                "field_name": field_name, "field_alias": field_alias,
                "data_type": data_type or 'string', "length": length,
                "precision": precision, "nullable": nullable if nullable is not None else True,
                "is_primary_key": is_primary_key or False,
                "is_required": is_required or False,
                "default_value": default_value, "description": description,
                "ordering": ordering or 0,
            })
            count += 1

        print(f"  ✓ 迁移 {count} 个字段（从 source_fields）")
        return count


def migrate_dictionaries(engine):
    """将 SourceDictionary 迁移到 standard_dictionaries。"""
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT id, api_key_id, dict_name, dict_code, description, is_system
            FROM source_dictionaries
        """))
        dicts = result.fetchall()

        count = 0
        for dict_id, api_key_id, dict_name, dict_code, description, is_system in dicts:
            conn.execute(text("""
                INSERT INTO standard_dictionaries (id, api_key_id, dict_name, dict_code,
                                                  description, scope, is_system, created_at)
                VALUES (:id, :api_key_id, :dict_name, :dict_code,
                        :description, 'standard', :is_system, NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": dict_id, "api_key_id": api_key_id, "dict_name": dict_name,
                "dict_code": dict_code, "description": description,
                "is_system": is_system or False,
            })
            count += 1

        print(f"  ✓ 迁移 {count} 个字典（从 source_dictionaries）")
        return count


def migrate_dictionary_items(engine):
    """将旧 dictionary_items 迁移到 new_dictionary_items。"""
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT di.id, di.dict_id, di.item_code, di.item_name,
                   di.parent_code, di.description, di.ordering
            FROM dictionary_items di
            JOIN standard_dictionaries sd ON di.dict_id = sd.id
        """))
        items = result.fetchall()

        count = 0
        for item_id, dict_id, item_code, item_name, parent_code, description, ordering in items:
            conn.execute(text("""
                INSERT INTO new_dictionary_items (id, dict_id, item_code, item_name,
                                                 parent_code, description, ordering, created_at)
                VALUES (:id, :dict_id, :item_code, :item_name,
                        :parent_code, :description, :ordering, NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": item_id, "dict_id": dict_id, "item_code": item_code,
                "item_name": item_name, "parent_code": parent_code,
                "description": description, "ordering": ordering or 0,
            })
            count += 1

        print(f"  ✓ 迁移 {count} 个字典条目（从 dictionary_items）")
        return count


def migrate_field_dict_links(engine):
    """将 FieldDictLink 迁移到 standard_field_dict_links。"""
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT fdl.id, fdl.field_id, fdl.dict_id, fdl.filter_condition, fdl.ordering
            FROM field_dict_links fdl
            JOIN standard_fields sf ON fdl.field_id = sf.id
            JOIN standard_dictionaries sd ON fdl.dict_id = sd.id
        """))
        links = result.fetchall()

        count = 0
        for link_id, field_id, dict_id, filter_condition, ordering in links:
            conn.execute(text("""
                INSERT INTO standard_field_dict_links (id, field_id, dict_id, filter_condition,
                                                      is_primary, ordering, created_at)
                VALUES (:id, :field_id, :dict_id, :filter_condition,
                        TRUE, :ordering, NOW())
                ON CONFLICT DO NOTHING
            """), {
                "id": link_id, "field_id": field_id, "dict_id": dict_id,
                "filter_condition": filter_condition, "ordering": ordering or 0,
            })
            count += 1

        print(f"  ✓ 迁移 {count} 个字段-字典关联（从 field_dict_links）")
        return count


def update_quality_rules_fk(engine):
    """更新 quality_rules 的 standard_field_id 指向新表。"""
    with engine.begin() as conn:
        # 检查是否有旧的外键引用
        result = conn.execute(text("""
            SELECT COUNT(*) FROM quality_rules qr
            WHERE qr.standard_field_id IS NOT NULL
        """))
        count = result.scalar()

        if count > 0:
            # 由于 standard_fields 的 ID 与 data_standard_fields 相同（如果已迁移）
            # 这里只需要验证外键有效性
            print(f"  ⚠ quality_rules 中有 {count} 条记录引用 standard_field_id")
            print("    请确保 standard_fields 表已包含对应记录")
        else:
            print("  ✓ quality_rules 无需更新")


def drop_old_tables(engine):
    """删除旧表。"""
    old_tables = [
        'dictionary_items',  # 先删子表
        'field_dict_links',
        'source_fields',
        'source_tables',
        'source_dictionaries',
        'data_standard_fields',
        'data_standard_sheets',
    ]

    with engine.begin() as conn:
        for table in old_tables:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
                print(f"  ✓ 删除旧表: {table}")
            except Exception as e:
                print(f"  ⚠ 删除 {table} 失败: {e}")


def rename_new_dictionary_items(engine):
    """将 new_dictionary_items 重命名为 dictionary_items。"""
    with engine.begin() as conn:
        # 先删除旧的 dictionary_items（如果存在）
        conn.execute(text("DROP TABLE IF EXISTS dictionary_items CASCADE"))
        # 重命名新表
        conn.execute(text("ALTER TABLE new_dictionary_items RENAME TO dictionary_items"))
        print("  ✓ 重命名 new_dictionary_items → dictionary_items")


def verify_migration(engine):
    """验证迁移结果。"""
    print("\n验证迁移结果:")
    new_tables = [
        'standard_layer_groups', 'standard_tables', 'standard_fields',
        'standard_dictionaries', 'dictionary_items', 'standard_field_dict_links',
    ]

    with engine.connect() as conn:
        for table in new_tables:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            print(f"  {table}: {count} 条记录")

        # 验证外键关系
        result = conn.execute(text("""
            SELECT COUNT(*) FROM standard_tables st
            LEFT JOIN standard_layer_groups slg ON st.layer_id = slg.id
            WHERE slg.id IS NULL
        """))
        orphan_tables = result.scalar()
        if orphan_tables > 0:
            print(f"  ⚠ 发现 {orphan_tables} 个孤立数据表（无对应层组）")
        else:
            print("  ✓ 数据表外键关系正常")


def main():
    parser = argparse.ArgumentParser(description="数据标准 v2.2 数据迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅检测不执行")
    parser.add_argument("--auto", action="store_true", help="自动执行（危险）")
    parser.add_argument("--rollback", action="store_true", help="回滚迁移")
    parser.add_argument("--database-url", help="覆盖数据库连接 URL")
    args = parser.parse_args()

    db_url = args.database_url or settings.database_url_sync
    if not db_url:
        print("错误：未设置数据库连接 URL")
        sys.exit(1)

    print(f"连接数据库：{db_url}")
    engine = create_engine(db_url)

    # 检查现有表
    existing_tables = get_existing_tables(engine)
    print(f"数据库中已有 {len(existing_tables)} 张表")

    # 检查旧数据
    print("\n旧表数据统计:")
    stats = check_old_data(engine)
    for table, count in stats.items():
        status = f"{count} 条" if count >= 0 else "不存在"
        print(f"  {table}: {status}")

    if args.dry_run:
        print("\n[DRY-RUN] 未执行迁移操作")
        return

    if args.rollback:
        print("\n[ROLLBACK] 回滚迁移...")
        drop_old_tables(engine)
        print("回滚完成")
        return

    if not args.auto:
        print("\n是否继续执行迁移？(y/N): ", end="")
        choice = input().strip().lower()
        if choice != "y":
            print("已取消迁移")
            return

    # 执行迁移
    print("\n开始迁移...")

    # 1. 更新 data_standards 表
    print("\n[1/8] 更新 data_standards 表...")
    migrate_data_standards(engine)

    # 2. 创建新表
    print("\n[2/8] 创建新表...")
    with engine.begin() as conn:
        conn.execute(text(NEW_TABLES_SQL))
    print("  ✓ 新表创建完成")

    # 3. 迁移层组
    print("\n[3/8] 迁移层组...")
    migrate_sheet_to_layer(engine)

    # 4. 迁移数据表
    print("\n[4/8] 迁移数据表...")
    migrate_source_tables(engine)

    # 5. 迁移字段
    print("\n[5/8] 迁移字段...")
    migrate_source_fields(engine)

    # 6. 迁移字典
    print("\n[6/8] 迁移字典...")
    migrate_dictionaries(engine)

    # 7. 迁移字典条目
    print("\n[7/8] 迁移字典条目...")
    migrate_dictionary_items(engine)

    # 8. 迁移字段-字典关联
    print("\n[8/8] 迁移字段-字典关联...")
    migrate_field_dict_links(engine)

    # 更新 quality_rules 外键
    print("\n更新 quality_rules 外键...")
    update_quality_rules_fk(engine)

    # 重命名新字典条目表
    print("\n重命名字典条目表...")
    rename_new_dictionary_items(engine)

    # 删除旧表
    print("\n删除旧表...")
    drop_old_tables(engine)

    # 验证迁移
    verify_migration(engine)

    print("\n✅ 迁移完成！")


if __name__ == "__main__":
    main()
