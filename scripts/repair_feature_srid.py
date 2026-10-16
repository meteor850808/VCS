"""修复存量空间要素的 SRID 标记（v2.10.5）。

背景：
    v2.10.4 及之前，自定义投影坐标系（无 EPSG 编码，如上海地方坐标系
    shanghaicity）入库时被 `crs_to_srid()` 回退标记为 SRID=4326，
    而坐标仍是投影米值。导致：
      - 质检 T03/T04/T06/T07/T08/T09 的 ST_Transform 抛
        "transform: Invalid coordinate (2049)"
      - 地图预览把投影坐标当经纬度显示（错位）
      - bbox 空间查询语义错误

本脚本做什么：
    对每个含空间要素的 StoredFile：
      1. 用 custom_crs.resolve_ingest_srid_async 重新解析 detected_crs
         （自定义坐标系会幂等注册到 spatial_ref_sys）
      2. 对比要素表中的实际 ST_SRID 与解析结果
      3. 不一致时（dry-run 仅报告；--apply 实际执行）：
         UPDATE ... SET geometry = ST_SetSRID(geometry, 目标SRID)
         并回写 stored_files.detected_crs_srid / crs_is_custom

    另外检测"无法自动修复"的文件：detected_crs 为空/不可解析、但坐标明显
    不是经纬度（超出 ±180/±90）→ 列入需人工确认清单。

用法：
    python scripts/repair_feature_srid.py              # dry-run 只报告
    python scripts/repair_feature_srid.py --apply      # 执行修复
    python scripts/repair_feature_srid.py --file-id xx # 仅处理指定文件
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.spatial_feature import ALL_FEATURE_TABLES  # noqa: E402
from app.models.stored_file import StoredFile  # noqa: E402
from app.services.custom_crs import (  # noqa: E402
    DEFAULT_SRID,
    coord_domain_warning,
    resolve_ingest_srid_async,
)

GEO_DOMAIN = (-180.0, -90.0, 180.0, 90.0)


async def get_extent(session, table_name: str, fid: str):
    """取某分区表该文件的坐标范围，无要素返回 None。"""
    q = text(f"""
        SELECT ST_XMin(e.x), ST_YMin(e.x), ST_XMax(e.x), ST_YMax(e.x)
        FROM (
            SELECT ST_Extent(geometry) AS x
            FROM {table_name} WHERE stored_file_id = :fid AND geometry IS NOT NULL
        ) e WHERE e.x IS NOT NULL
    """)
    return (await session.execute(q, {"fid": fid})).first()


async def get_actual_srids(session, fid: str) -> dict[str, list[int]]:
    """各分区表中该文件几何的实际 SRID 分布。"""
    out: dict[str, list[int]] = {}
    for tbl in ALL_FEATURE_TABLES:
        q = text(f"""
            SELECT DISTINCT ST_SRID(geometry) FROM {tbl.__tablename__}
            WHERE stored_file_id = :fid AND geometry IS NOT NULL
        """)
        rows = (await session.execute(q, {"fid": fid})).all()
        if rows:
            out[tbl.__tablename__] = sorted(r[0] for r in rows)
    return out


async def process_file(session, sf: StoredFile, apply_mode: bool) -> dict:
    """处理单个 StoredFile，返回报告条目。"""
    report = {
        "file_id": sf.id,
        "name": sf.original_name,
        "crs": (sf.detected_crs or "")[:60],
        "action": "skip",
        "detail": "",
    }

    actual = await get_actual_srids(session, sf.id)
    if not actual:
        report["action"] = "no-features"
        return report

    all_actual = sorted({s for srids in actual.values() for s in srids})

    # ── 解析目标 SRID（同时注册自定义坐标系到 spatial_ref_sys）─────────────
    res = await resolve_ingest_srid_async(session, sf.detected_crs)
    target = res.srid

    # ── 情形 1：实际与目标一致 → 无需修复 ────────────────────────────────
    if all(srids == [target] for srids in actual.values()):
        # 元数据补全（历史行可能为 NULL / false）
        if apply_mode and (sf.detected_crs_srid != target or not sf.crs_is_custom):
            sf.detected_crs_srid = target
            sf.crs_is_custom = res.is_custom
            report["action"] = "meta-only"
            report["detail"] = f"仅补全元数据 srid={target}"
        else:
            report["action"] = "ok"
            report["detail"] = f"srid={target}" + ("(custom)" if res.is_custom else "")
        return report

    # ── 情形 2：目标仍是 4326 且坐标域矛盾 → 无法自动修复 ────────────────
    if target == DEFAULT_SRID and not res.is_custom:
        extents = [await get_extent(session, t.__tablename__, sf.id) for t in ALL_FEATURE_TABLES]
        extents = [e for e in extents if e]
        if extents:
            xmin = min(e[0] for e in extents)
            ymin = min(e[1] for e in extents)
            xmax = max(e[2] for e in extents)
            ymax = max(e[3] for e in extents)
            warn = coord_domain_warning(True, (xmin, ymin, xmax, ymax))
            if warn:
                report["action"] = "manual-review"
                report["detail"] = (
                    f"CRS 缺失/不可解析且坐标非经纬度 "
                    f"[{xmin:.0f},{ymin:.0f}]~[{xmax:.0f},{ymax:.0f}]；"
                    f"actual={all_actual}，请补充源数据 CRS 后重传或人工指定"
                )
                return report

    # ── 情形 3：可修复 —— 重标 SRID ──────────────────────────────────────
    if len(all_actual) > 1:
        report["action"] = "multi-srid"
        report["detail"] = (
            f"同一文件出现多个 SRID {all_actual}（异常），跳过自动修复，请人工核查"
        )
        return report

    src_srid = all_actual[0]
    report["action"] = "fixable"
    report["detail"] = f"{src_srid} → {target}" + ("(custom)" if res.is_custom else "")
    if not apply_mode:
        return report

    for tbl in ALL_FEATURE_TABLES:
        await session.execute(text(f"""
            UPDATE {tbl.__tablename__}
            SET geometry = ST_SetSRID(geometry, :target)
            WHERE stored_file_id = :fid AND ST_SRID(geometry) = :src
        """), {"fid": sf.id, "src": src_srid, "target": target})
    sf.detected_crs_srid = target
    sf.crs_is_custom = res.is_custom
    report["action"] = "fixed"
    return report


async def main() -> int:
    # Windows GBK 控制台兼容：统一 UTF-8 输出，无法编码的字符替换而非崩溃
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="修复存量空间要素的 SRID 标记")
    parser.add_argument("--apply", action="store_true", help="执行修复（默认 dry-run）")
    parser.add_argument("--file-id", help="仅处理指定 stored_file_id")
    args = parser.parse_args()

    print(f"=== SRID 存量修复 — {'APPLY' if args.apply else 'DRY-RUN'} ===")

    async with AsyncSessionLocal() as session:
        q = select(StoredFile).order_by(StoredFile.created_at)
        if args.file_id:
            q = q.where(StoredFile.id == args.file_id)
        files = (await session.execute(q)).scalars().all()

        # 仅处理在要素表里有数据的文件
        targets = []
        for sf in files:
            actual = await get_actual_srids(session, sf.id)
            if actual:
                targets.append(sf)

        print(f"共 {len(files)} 个 StoredFile，其中 {len(targets)} 个含空间要素\n")

        reports = []
        for i, sf in enumerate(targets, 1):
            try:
                r = await process_file(session, sf, args.apply)
            except Exception as e:
                r = {"file_id": sf.id, "name": sf.original_name,
                     "crs": "", "action": "error", "detail": str(e)[:200]}
            reports.append(r)
            mark = {"ok": "[OK] ", "fixed": "[FIX]", "fixable": "[NEED]",
                    "meta-only": "[META]", "manual-review": "[MANUAL]",
                    "multi-srid": "[MANUAL]", "error": "[ERR] ",
                    "skip": "[SKIP]", "no-features": "[SKIP]"}.get(r["action"], "[?]   ")
            print(f"[{i}/{len(targets)}] {mark} {r['action']:<13} {r['name'][:52]}")
            print(f"{'':>18} {r['detail']}")

        fixable = [r for r in reports if r["action"] in ("fixed", "fixable")]
        manual = [r for r in reports if r["action"] in ("manual-review", "multi-srid")]

        if args.apply:
            await session.commit()
            print(f"\n已提交。修复 {len([r for r in reports if r['action']=='fixed'])} 个文件，"
                  f"元数据补全 {len([r for r in reports if r['action']=='meta-only'])} 个。")
        else:
            print(f"\nDRY-RUN 结束：可修复 {len(fixable)} 个，"
                  f"需人工确认 {len(manual)} 个。加 --apply 执行修复。")

        # 自定义 SRID 注册发生在 resolve 阶段，即使 dry-run 也提交注册（幂等、无害）
        await session.commit()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
