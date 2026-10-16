#!/usr/bin/env python3
"""清理孤立数据脚本 — 在添加外键约束前执行。

功能：
1. 检测所有表中的孤立数据（引用了不存在的父记录）
2. 交互式确认后执行清理
3. 支持 dry-run 模式预览

使用方法：
  python scripts/cleanup_orphan_data.py          # 交互模式
  python scripts/cleanup_orphan_data.py --dry-run # 仅检测不清理
  python scripts/cleanup_orphan_data.py --auto    # 自动清理（危险）
"""

import argparse
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text, create_engine
from app.config import settings


# ── 孤立数据检测规则 ─────────────────────────────────────────────────────────
ORPHAN_CHECKS = [
    {
        "name": "stored_files → api_keys",
        "table": "stored_files",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "conversion_jobs → api_keys",
        "table": "conversion_jobs",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "conversion_jobs → batch_jobs",
        "table": "conversion_jobs",
        "column": "batch_job_id",
        "ref_table": "batch_jobs",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "batch_jobs → api_keys",
        "table": "batch_jobs",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_reports → conversion_jobs",
        "table": "quality_reports",
        "column": "job_id",
        "ref_table": "conversion_jobs",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_reports → batch_jobs",
        "table": "quality_reports",
        "column": "batch_job_id",
        "ref_table": "batch_jobs",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_reports → api_keys",
        "table": "quality_reports",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_reports → data_standards",
        "table": "quality_reports",
        "column": "data_standard_id",
        "ref_table": "data_standards",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "data_standards → api_keys",
        "table": "data_standards",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "data_standard_sheets → data_standards",
        "table": "data_standard_sheets",
        "column": "standard_id",
        "ref_table": "data_standards",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "data_standard_fields → data_standard_sheets",
        "table": "data_standard_fields",
        "column": "sheet_id",
        "ref_table": "data_standard_sheets",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "quality_rules → api_keys",
        "table": "quality_rules",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "quality_rules → data_standard_fields",
        "table": "quality_rules",
        "column": "standard_field_id",
        "ref_table": "data_standard_fields",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_rule_results → quality_reports",
        "table": "quality_rule_results",
        "column": "report_id",
        "ref_table": "quality_reports",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "quality_rule_results → quality_rules",
        "table": "quality_rule_results",
        "column": "rule_id",
        "ref_table": "quality_rules",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_violations → quality_reports",
        "table": "quality_violations",
        "column": "report_id",
        "ref_table": "quality_reports",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "quality_violations → quality_rule_results",
        "table": "quality_violations",
        "column": "rule_result_id",
        "ref_table": "quality_rule_results",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "quality_daily_snapshots → api_keys",
        "table": "quality_daily_snapshots",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "source_tables → api_keys",
        "table": "source_tables",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "source_tables → data_standards",
        "table": "source_tables",
        "column": "standard_id",
        "ref_table": "data_standards",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "source_fields → source_tables",
        "table": "source_fields",
        "column": "table_id",
        "ref_table": "source_tables",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "source_dictionaries → api_keys",
        "table": "source_dictionaries",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "dictionary_items → source_dictionaries",
        "table": "dictionary_items",
        "column": "dict_id",
        "ref_table": "source_dictionaries",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "field_dict_links → source_fields",
        "table": "field_dict_links",
        "column": "field_id",
        "ref_table": "source_fields",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "field_dict_links → source_dictionaries",
        "table": "field_dict_links",
        "column": "dict_id",
        "ref_table": "source_dictionaries",
        "ref_column": "id",
        "severity": "critical",
    },
    {
        "name": "standard_versions → data_standards",
        "table": "standard_versions",
        "column": "standard_id",
        "ref_table": "data_standards",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "standard_versions → api_keys",
        "table": "standard_versions",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "point_features → stored_files",
        "table": "point_features",
        "column": "stored_file_id",
        "ref_table": "stored_files",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "point_features → api_keys",
        "table": "point_features",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "line_features → stored_files",
        "table": "line_features",
        "column": "stored_file_id",
        "ref_table": "stored_files",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "line_features → api_keys",
        "table": "line_features",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "polygon_features → stored_files",
        "table": "polygon_features",
        "column": "stored_file_id",
        "ref_table": "stored_files",
        "ref_column": "id",
        "severity": "warning",
    },
    {
        "name": "polygon_features → api_keys",
        "table": "polygon_features",
        "column": "api_key_id",
        "ref_table": "api_keys",
        "ref_column": "id",
        "severity": "warning",
    },
]


def check_orphan_data(engine, check: dict) -> list[str]:
    """检测孤立数据，返回孤立记录 ID 列表。"""
    sql = text(f"""
        SELECT t.id
        FROM {check['table']} t
        LEFT JOIN {check['ref_table']} r ON t.{check['column']} = r.id
        WHERE t.{check['column']} IS NOT NULL
          AND r.id IS NULL
        LIMIT 100
    """)
    with engine.connect() as conn:
        result = conn.execute(sql)
        return [row[0] for row in result]


def cleanup_orphan_data(engine, check: dict, orphan_ids: list[str]):
    """清理孤立数据：将外键置为 NULL。"""
    if not orphan_ids:
        return
    placeholders = ", ".join([f"'{oid}'" for oid in orphan_ids])
    sql = text(f"""
        UPDATE {check['table']}
        SET {check['column']} = NULL
        WHERE id IN ({placeholders})
    """)
    with engine.begin() as conn:
        conn.execute(sql)


def main():
    parser = argparse.ArgumentParser(description="清理孤立数据")
    parser.add_argument("--dry-run", action="store_true", help="仅检测不清理")
    parser.add_argument("--auto", action="store_true", help="自动清理所有孤立数据（危险）")
    parser.add_argument("--database-url", help="覆盖数据库连接 URL")
    args = parser.parse_args()

    db_url = args.database_url or settings.database_url_sync
    if not db_url:
        print("错误：未设置数据库连接 URL，请设置 DATABASE_URL 环境变量或使用 --database-url 参数")
        sys.exit(1)

    print(f"连接数据库：{db_url}")
    engine = create_engine(db_url)

    # 检查表是否存在
    with engine.connect() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )).fetchall()
        existing_tables = {t[0] for t in tables}

    if not existing_tables:
        print("数据库中没有表，请先运行迁移创建表结构")
        sys.exit(0)

    print(f"发现 {len(existing_tables)} 张表")
    print("=" * 70)

    # 检测孤立数据
    total_orphans = 0
    results = []

    for check in ORPHAN_CHECKS:
        if check["table"] not in existing_tables or check["ref_table"] not in existing_tables:
            continue

        orphan_ids = check_orphan_data(engine, check)
        if orphan_ids:
            results.append((check, orphan_ids))
            total_orphans += len(orphan_ids)
            severity_icon = "🔴" if check["severity"] == "critical" else "🟡"
            print(f"{severity_icon} {check['name']}: {len(orphan_ids)} 条孤立记录")
            print(f"   示例 ID: {orphan_ids[:3]}")

    print("=" * 70)
    print(f"总计发现 {total_orphans} 条孤立记录")

    if not results:
        print("✅ 没有发现孤立数据，可以安全添加外键约束")
        return

    if args.dry_run:
        print("\n[DRY-RUN] 未执行清理操作")
        return

    if args.auto:
        print("\n[ AUTO ] 自动清理所有孤立数据...")
        for check, orphan_ids in results:
            cleanup_orphan_data(engine, check, orphan_ids)
            print(f"  ✓ 已清理 {check['name']}: {len(orphan_ids)} 条")
        print("\n✅ 清理完成")
        return

    # 交互模式
    print("\n是否清理这些孤立数据？(y/N): ", end="")
    choice = input().strip().lower()
    if choice == "y":
        for check, orphan_ids in results:
            print(f"  清理 {check['name']}...", end=" ")
            cleanup_orphan_data(engine, check, orphan_ids)
            print("✓")
        print("\n✅ 清理完成")
    else:
        print("\n已取消清理操作")


if __name__ == "__main__":
    main()
