"""空间数据处理 API — 坐标转换（独立结果表）/ 几何修复（跨三表）。"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import authenticated
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.stored_file import StoredFile
from app.models.transform_result import TransformResult
from app.schemas.transform_result import TransformResultBrief
from app.services.custom_crs import CUSTOM_SRID_BASE
from app.services.feature_store import repair_features_by_file

logger = logging.getLogger(__name__)
router = APIRouter()


# ── CRS 目录（下拉选择参考）─────────────────────────────────
from app.services.transform_engine import get_custom_crs_catalog

# 自定义 CRS（与 transform_engine 内置定义对齐，避免两处维护不一致）
_CUSTOM_CRS = get_custom_crs_catalog()


@router.get("/spatial/crs/catalog", summary="CRS 坐标系目录（供下拉选择）")
async def crs_catalog(
    db: AsyncSession = Depends(get_db),
):
    """从 PostGIS spatial_ref_sys + 自定义 CRS 聚合常用坐标系目录。

    返回分组列表，前端可直接渲染为 <optgroup> 下拉。
    """
    # 常用中国 CRS SRID 列表
    common_srds = [
        # 地理坐标系
        4326, 4490,
        # CGCS2000 3度带投影 (zone 25-38，覆盖中国全境)
        4513, 4514, 4515, 4516, 4517, 4518, 4519, 4520,
        4521, 4522, 4523, 4524, 4525, 4526,
        # CGCS2000 6度带投影 (zone 13-23)
        4491, 4492, 4493, 4494, 4495, 4496, 4497, 4498,
        4499, 4500, 4501,
        # Beijing 1954 3度带 (zone 25-45)
        2401, 2402, 2403, 2404, 2405, 2406, 2407, 2408,
        2409, 2410, 2411, 2412, 2413, 2414, 2415, 2416,
        2417, 2418, 2419, 2420, 2421,
        # Xian 1980 3度带 (zone 25-37)
        2349, 2350, 2351, 2352, 2353, 2354, 2355, 2356,
        2357, 2358, 2359, 2360, 2361,
        # Xian 1980 6度带 (zone 13-23)
        2327, 2328, 2329, 2330, 2331, 2332, 2333, 2334,
        2335, 2336, 2337,
        # WGS 84 UTM (中国相关 zone 43-53N)
        32643, 32644, 32645, 32646, 32647, 32648, 32649,
        32650, 32651, 32652, 32653,
    ]
    placeholders = ",".join(str(s) for s in common_srds)

    rows = await db.execute(text(f"""
        SELECT srid, auth_name, auth_srid,
               REPLACE(REPLACE(srtext,
                   'PROJCS["', ''),
                   'GEOGCS["', '') AS raw_name
        FROM spatial_ref_sys
        WHERE srid IN ({placeholders})
        ORDER BY srid
    """))
    db_rows = rows.fetchall()

    # 分组映射
    def _categorize(srid: int, raw: str) -> tuple[str, str]:
        """返回 (group_name, display_name)"""
        r = raw.lower()
        if srid == 4326:
            return ("地理坐标系", "WGS 84 (EPSG:4326)")
        if srid == 4490:
            return ("地理坐标系", "CGCS2000 (EPSG:4490)")
        if "cgcs2000" in r and "3-degree" in r:
            # 提取 zone 号
            zone = srid - 4513 + 25
            return ("CGCS2000 3°投影带", f"CGCS2000 3° GK Zone {zone} (EPSG:{srid})")
        if "cgcs2000" in r and ("gauss-kruger zone" in r or "cm " in r):
            zone = srid - 4491 + 13
            return ("CGCS2000 6°投影带", f"CGCS2000 6° GK Zone {zone} (EPSG:{srid})")
        if "beijing 1954" in r and "3-degree" in r:
            zone = srid - 2401 + 25
            return ("北京1954 3°投影带", f"Beijing 1954 3° GK Zone {zone} (EPSG:{srid})")
        if "xian 1980" in r and "3-degree" in r:
            zone = srid - 2349 + 25
            return ("西安1980 3°投影带", f"Xian 1980 3° GK Zone {zone} (EPSG:{srid})")
        if "xian 1980" in r and ("gauss-kruger zone" in r or "cm " in r):
            zone = srid - 2327 + 13
            return ("西安1980 6°投影带", f"Xian 1980 6° GK Zone {zone} (EPSG:{srid})")
        if "wgs 84" in r and "utm" in r:
            # 提取 zone（r 已小写化，须匹配小写 n/s；显示时恢复大写）
            import re
            m = re.search(r"zone (\d+)([ns])", r)
            if m:
                z, ns = m.group(1), m.group(2)
                return ("WGS 84 UTM", f"WGS 84 UTM Zone {z}{ns.upper()} (EPSG:{srid})")
        return ("其他", f"EPSG:{srid}")

    groups: dict[str, list[dict]] = {}
    for row in db_rows:
        srid, auth_name, auth_srid, raw = row
        grp, name = _categorize(srid, raw or "")
        groups.setdefault(grp, []).append({
            "srid": srid,
            "code": f"EPSG:{srid}",
            "name": name,
        })

    # 自定义 CRS 置顶
    # 自定义 CRS（解析真实 SRID；引擎内置定义，幂等注册 cached）
    from app.services.transform_engine import resolve_transform_crs
    custom_items = []
    for c in _CUSTOM_CRS:
        try:
            resolved = await resolve_transform_crs(db, c["code"])
            srid = resolved.srid
        except Exception:
            srid = c["srid"]
        custom_items.append({
            "srid": srid, "code": c["code"], "name": f"{c['name']} (Custom:{c['code']})",
        })
    result = [{"group": "自定义坐标系", "items": custom_items}]
    # 常用预设
    result.append({"group": "常用", "items": [
        {"srid": 4326, "code": "EPSG:4326", "name": "WGS 84 — GPS/国际"},
        {"srid": 4490, "code": "EPSG:4490", "name": "CGCS2000 — 国家2000"},
        {"srid": 4519, "code": "EPSG:4519", "name": "CGCS2000 3° GK 31 — 上海"},
        {"srid": 4520, "code": "EPSG:4520", "name": "CGCS2000 3° GK 32 — 江浙"},
        {"srid": 2406, "code": "EPSG:2406", "name": "Beijing 1954 3° GK 31 — 上海"},
        {"srid": 32651, "code": "EPSG:32651", "name": "WGS 84 UTM 51N — 东亚"},
    ]})
    # 数据库分组追加
    for grp_name in ["地理坐标系", "CGCS2000 3°投影带", "CGCS2000 6°投影带",
                     "北京1954 3°投影带", "西安1980 3°投影带", "西安1980 6°投影带",
                     "WGS 84 UTM", "其他"]:
        if grp_name in groups:
            result.append({"group": grp_name, "items": groups[grp_name]})

    return {"catalog": result, "custom": _CUSTOM_CRS}


# ── 坐标转换（副本模式：原文件不动，结果存 transform_results）─────────────

def _inject_crs_member(payload: bytes, target_srid: int | None) -> bytes:
    """为转换结果 GeoJSON 补 `crs` 成员（声明目标坐标系），避免被下游工具误判为 WGS84。

    RFC 7946 规定 FeatureCollection 若省略 `crs` 成员，坐标系统一按 WGS84(4326) 解释，
    因此 EPSG:4490/4519 等非 WGS84 转换结果若不声明 CRS，会被 QGIS/ArcGIS/在线地图
    当成 WGS84 显示。

    按 SRID 类型区分写法：
    - 标准 EPSG SRID（< CUSTOM_SRID_BASE=910000，如 4326/4490/4519/3857）：
      写 `urn:ogc:def:crs:EPSG::<srid>`，下游工具可正确识别。
    - 自定义 SRID（>= 910000，如 918984=shanghaicity）：
      EPSG 库里并无该编码，写 `urn:ogc:def:crs:EPSG::918984` 属**无效 URN**，会让工具
      报「未知 CRS」。此处改以 VCS 自定义命名空间 `urn:ogc:def:crs:VCS-CUSTOM::<srid>`
      诚实声明，避免伪装成 EPSG 编码导致误导/报错。

    已含 `crs` 或解析失败时原样返回（不阻断下载）。但若自定义 SRID 上已错误写明
    `urn:ogc:def:crs:EPSG::<srid>`（早期版本写入的无效 URN），会一并纠正为
    VCS-CUSTOM 命名空间。
    """
    if not target_srid or not payload:
        return payload
    try:
        fc = json.loads(payload)
        if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
            cur = fc.get("crs")
            cur_name = None
            if isinstance(cur, dict):
                cur_name = (cur.get("properties") or {}).get("name")
            if target_srid >= CUSTOM_SRID_BASE:
                # 自定义 SRID：目标命名空间必须是 VCS-CUSTOM，绝非 EPSG
                valid_ns = f"urn:ogc:def:crs:VCS-CUSTOM::{target_srid}"
                if cur_name != valid_ns:
                    fc["crs"] = {"type": "name", "properties": {"name": valid_ns}}
                    return json.dumps(fc, ensure_ascii=False).encode("utf-8")
            else:
                # 标准 EPSG SRID：缺失时补写，已有则保持
                if "crs" not in fc:
                    fc["crs"] = {"type": "name",
                                 "properties": {"name": f"urn:ogc:def:crs:EPSG::{target_srid}"}}
                    return json.dumps(fc, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        logger.debug("GeoJSON CRS 注入失败: %s", e)
    return payload


@router.post("/spatial/{file_id}/transform", summary="坐标转换：生成 GeoJSON 副本（原文件不动）")
async def transform_spatial_features(
    file_id: str,
    target_crs: str = Query("EPSG:4490", description="目标坐标系"),
    source_crs: str | None = Query(None, description="源坐标系（缺省时自动读取文件 detected_crs）"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """将已入库空间要素的几何坐标从源 CRS 转换到目标 CRS，结果以 GeoJSON 保存到 transform_results。

    原文件 feature 表不做任何修改。转换结果可通过 /spatial/transform-results 管理和下载。

    统一变换引擎（v2.13）：
        - CRS 经 `resolve_transform_crs` 解析（复用 custom_crs 权威链路，天然支持
          `Custom:code`、WKT、proj4、中文别名等手动输入，不再静默回退 WGS84）；
        - 变换前后对每个要素做坐标域合理性校验并统计失败/跳过。
    """
    sf = await db.get(StoredFile, file_id)
    if not sf or not api_key.owns(sf):
        raise HTTPException(status_code=404, detail="文件不存在")

    from app.services.transform_engine import (
        resolve_transform_crs,
        transform_db_features,
    )

    # ── 统一解析源/目标 CRS ─────────────────────────────────────────
    src_str = source_crs or sf.detected_crs or "EPSG:4326"
    src_res = await resolve_transform_crs(db, src_str)
    dst_res = await resolve_transform_crs(db, target_crs)

    src_srid, dst_srid = src_res.srid, dst_res.srid
    src_display = src_res.display if source_crs else (sf.detected_crs or src_res.display)
    dst_display = dst_res.display

    # 源 CRS 无法解析（回退 4326）且用户显式指定了源坐标系 → 告警
    transform_warnings: list[str] = []
    if source_crs and src_res.source in ("fallback-empty", "fallback-unparseable"):
        transform_warnings.append(
            f"源坐标系「{source_crs}」无法解析，已回退使用 EPSG:4326"
        )

    # ── 统一变更引擎：读取要素 + 变换 + 校验 ─────────────────────────
    features_geojson, stats = await transform_db_features(
        db, file_id, src_res, dst_res, include_table_name=True,
    )
    transform_warnings.extend(stats.warnings)

    if not features_geojson:
        # 全部要素变换失败/跳过：给出可诊断的错误（避免产出损坏/空数据集）
        detail = "该文件无可转换的几何要素"
        if transform_warnings:
            detail += "。" + "；".join(transform_warnings[:2])
        if stats.failed > 0:
            detail += f"（{stats.failed} 个要素变换失败，多为源/目标坐标系域不匹配，请核对源坐标系是否选对）"
        raise HTTPException(status_code=400, detail=detail)

    # 组装 FeatureCollection 并序列化
    fc = {"type": "FeatureCollection", "features": features_geojson}
    # 显式声明目标坐标系（RFC 7946 缺省为 WGS84/4326，声明后下游工具不再误判）。
    output_bytes = _inject_crs_member(
        json.dumps(fc, ensure_ascii=False, default=str).encode("utf-8"),
        dst_srid,
    )

    # 写入 transform_results 表
    # api_key_id 可能为虚拟管理员 ID（不在 api_keys 表），此时置 NULL 避免 FK 违规
    stored_key_id = sf.api_key_id
    if stored_key_id and stored_key_id.startswith("00000000"):
        stored_key_id = None
    tr = TransformResult(
        id=str(uuid.uuid4()),
        api_key_id=stored_key_id,
        source_file_id=file_id,
        source_file_name=sf.original_name,
        source_crs=src_display,
        target_crs=dst_display,
        source_srid=src_srid,
        target_srid=dst_srid,
        feature_count=stats.transformed,
        output_size=len(output_bytes),
        output_data=output_bytes,
    )
    db.add(tr)
    await db.commit()
    await db.refresh(tr)

    logger.info(
        "坐标转换完成: %s → %s, %d/%d 要素 (跳过=%d 失败=%d), result_id=%s",
        src_display, dst_display, stats.transformed, stats.total,
        stats.skipped, stats.failed, tr.id,
    )
    return {
        "result_id": tr.id,
        "source_crs": src_display,
        "target_crs": dst_display,
        "transformed": stats.transformed,
        "total": stats.total,
        "skipped": stats.skipped,
        "failed": stats.failed,
        "warnings": transform_warnings,
    }


@router.get("/spatial/transform-results", summary="转换结果列表")
async def list_transform_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    base = select(TransformResult).where(
        api_key.scope_clause(TransformResult),
    )
    # 总数
    cnt = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = cnt.scalar() or 0
    # 分页
    rows = await db.execute(
        base.order_by(TransformResult.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = rows.scalars().all()
    return {
        "items": [TransformResultBrief.model_validate(i) for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/spatial/transform-results/{result_id}/download", summary="下载转换结果（GeoJSON/Shapefile/GeoPackage）")
async def download_transform_result(
    result_id: str,
    format: str = Query("geojson", description="导出格式: geojson / shp / gpkg"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    tr = await db.get(TransformResult, result_id)
    if not tr or not api_key.owns(tr):
        raise HTTPException(status_code=404, detail="转换结果不存在")
    if not tr.output_data:
        raise HTTPException(status_code=410, detail="输出数据已清理")

    fmt = (format or "geojson").lower()
    if fmt not in ("geojson", "shp", "gpkg"):
        raise HTTPException(status_code=400, detail="不支持的导出格式: 可选 geojson / shp / gpkg")

    # ── 目标坐标系 WKT ─────────────────────────────────────────────
    # 对**所有**有 SRID 的目标都从 spatial_ref_sys 取 srtext（PostGIS 对标准 EPSG 也有
    # 完整 srtext），确保 SHP/GPKG 输出一定携带 `.prj`/CRS 定义。自定义 SRID(>=910000)
    # 也走同一查询；查不到则用 pyproj 按 EPSG 生成 WKT 兜底。
    target_wkt = None
    if tr.target_srid:
        row = (await db.execute(text(
            "SELECT srtext FROM spatial_ref_sys WHERE srid = :s"
        ), {"s": tr.target_srid})).mappings().first()
        target_wkt = row["srtext"] if row else None
        if not target_wkt and tr.target_srid < CUSTOM_SRID_BASE:
            try:
                from pyproj import CRS
                target_wkt = CRS.from_epsg(tr.target_srid).to_wkt()
            except Exception as _crs_e:
                logger.warning(f"pyproj EPSG 解析失败 srid={tr.target_srid}: {_crs_e}")
                target_wkt = None

    from app.services.export_service import (
        export_geojson,
        export_gpkg,
        export_shapefile_zip,
    )

    stem = Path(tr.source_file_name).stem
    base = f"{stem}_{tr.source_crs}_to_{tr.target_crs}"

    if fmt == "geojson":
        # 兼容历史结果：下载时若未声明 CRS（RFC 7946 缺省为 WGS84/4326，会导致
        # 4490/4519 等结果被下游工具误判为 WGS84），按 target_srid 补 `crs` 成员；
        # 自定义 SRID 的无效 EPSG::<custom> URN 也会被纠正为 VCS-CUSTOM。
        payload = _inject_crs_member(tr.output_data, tr.target_srid)
        return Response(
            content=payload,
            media_type="application/geo+json",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{base}.geojson"},
        )

    if fmt == "shp":
        payload = await _run_sync(export_shapefile_zip, tr.output_data, target_wkt, base)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{base}.zip"},
        )

    # gpkg
    payload = await _run_sync(export_gpkg, tr.output_data, target_wkt, base)
    return Response(
        content=payload,
        media_type="application/geopackage+sqlite3",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{base}.gpkg"},
    )


async def _run_sync(func, *args):
    """在事件循环外执行同步导出函数，避免阻塞 FastAPI 事件循环。"""
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args))


@router.delete("/spatial/transform-results/{result_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除转换结果")
async def delete_transform_result(
    result_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    tr = await db.get(TransformResult, result_id)
    if not tr or not api_key.owns(tr):
        raise HTTPException(status_code=404, detail="转换结果不存在")
    await db.delete(tr)
    await db.commit()


@router.post("/spatial/{file_id}/repair", summary="几何修复：对已入库空间要素执行几何修复")
async def repair_spatial_features(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """对已入库空间要素执行几何修复（buffer(0) + make_valid）（跨三表）。"""
    sf = await db.get(StoredFile, file_id)
    if not sf or not api_key.owns(sf):
        raise HTTPException(status_code=404, detail="文件不存在")

    result = await repair_features_by_file(db, file_id, api_key.id)
    await db.commit()
    return {"file_id": file_id, **result}
