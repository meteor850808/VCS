# -*- coding: utf-8 -*-
"""修复 GDB 元数据脏值（v2.10.2 存量数据修复）。

背景:
- v2.10.2 之前 GDB 层子记录的 geometry_type 直接存 fiona schema 原始值
  （如 "3D MultiPolygon"——维度是元数据声明，实际要素可能全 2D），
  且 file_size 硬编码为 0，导致数据管理界面"类型"显示 3D 误导、
  "大小"列全为 0 B。
- 本脚本:
  1) 对所有 stored_files.geometry_type 应用 normalize_stored_geometry_type
     归一化（剥离维度前缀/后缀 + Multi* → 父类）
  2) 将 file_format='gdb' 且 file_size=0 的子记录，按所属父 archive
     记录大小均摊到该父下全部 GDB 子记录（GDB 目录结构无单文件大小，
     均摊保证 total_size 聚合不虚高不丢失）

用法: python -m scripts.fix_gdb_metadata   （在 空间API服务 目录下执行）
"""
import asyncio
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.format_detection import normalize_stored_geometry_type


async def fix_geometry_type(db) -> int:
    """归一化全部 geometry_type，返回修复条数。"""
    rows = (
        await db.execute(text(
            "SELECT id, geometry_type FROM stored_files WHERE geometry_type IS NOT NULL"
        ))
    ).all()
    fixed = 0
    for row in rows:
        raw = row.geometry_type
        norm = normalize_stored_geometry_type(raw)
        if norm != raw:
            await db.execute(
                text("UPDATE stored_files SET geometry_type=:n WHERE id=:i"),
                {"n": norm, "i": row.id},
            )
            fixed += 1
    return fixed


async def fix_gdb_file_size(db) -> int:
    """将 file_size=0 的 GDB 子记录按父 archive 大小均摊，返回修复条数。"""
    rows = (
        await db.execute(text(
            """
            SELECT sub.id AS sub_id, sub.source_archive,
                   par.file_size AS parent_size, cnt.c AS siblings
            FROM stored_files sub
            JOIN stored_files par
              ON par.original_name = sub.source_archive AND par.is_archive
            JOIN (
                SELECT source_archive, count(*) AS c
                FROM stored_files
                WHERE file_format = 'gdb' AND source_archive IS NOT NULL
                GROUP BY source_archive
            ) cnt ON cnt.source_archive = sub.source_archive
            WHERE sub.file_format = 'gdb'
              AND sub.file_size = 0
              AND sub.source_archive IS NOT NULL
            """
        ))
    ).all()

    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r.source_archive, r.parent_size, r.siblings)].append(r.sub_id)

    fixed = 0
    for (_src, parent_size, siblings), sub_ids in groups.items():
        if not siblings or not parent_size:
            continue
        base = parent_size // siblings
        remainder = parent_size % siblings
        for i, sub_id in enumerate(sub_ids):
            size = base + (1 if i < remainder else 0)
            await db.execute(
                text("UPDATE stored_files SET file_size=:sz WHERE id=:i"),
                {"sz": size, "i": sub_id},
            )
            fixed += 1
    return fixed


async def main():
    async with AsyncSessionLocal() as db:
        n_types = await fix_geometry_type(db)
        n_sizes = await fix_gdb_file_size(db)
        await db.commit()

        # 汇总复查
        r = await db.execute(text(
            """
            SELECT geometry_type, count(*) AS c
            FROM stored_files WHERE geometry_type IS NOT NULL
            GROUP BY geometry_type ORDER BY c DESC
            """
        ))
        print("归一化后 geometry_type 分布:")
        for row in r:
            print(f"  {row.geometry_type or '(null)'}: {row.c}")
        r2 = await db.execute(text(
            "SELECT count(*) AS c FROM stored_files WHERE file_format='gdb' AND file_size=0"
        ))
        print(f"剩余 file_size=0 的 GDB 子记录: {r2.scalar()}")
        print(f"Done. 类型修复 {n_types} 条, 大小修复 {n_sizes} 条")


if __name__ == "__main__":
    asyncio.run(main())
