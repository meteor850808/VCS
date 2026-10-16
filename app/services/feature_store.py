"""FeatureStore — 统一跨 point / line / polygon 三张分区表的查询与写入。

v2.10 变更（分区化）：
- 三张表为 LIST 分区父表（分区键 stored_file_id），每数据源一个物理分区
- 写入前 `_ensure_partition*` 自动建分区（CREATE TABLE ... PARTITION OF）
- 新增同步写入路径（asyncio.to_thread 内使用，供大规模要素入库）
- 移除 max_features 截断（流式全量入库）
"""

import logging
import uuid
from typing import AsyncIterator

logger = logging.getLogger(__name__)

from geoalchemy2 import functions as geo_func
from geoalchemy2 import WKTElement
from shapely import wkt as shp_wkt
from sqlalchemy import select, func, text, union_all, literal_column, case, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spatial_feature import (
    ALL_FEATURE_TABLES,
    FEATURE_MODEL_MAP,
    PointFeature,
    LineFeature,
    PolygonFeature,
    partition_name,
)
from app.services.spatial_loader import geojson_to_wkt_status


def _model_for_type(geometry_type: str):
    """根据 GeoJSON geometry type 返回对应的 ORM 模型类。"""
    return FEATURE_MODEL_MAP.get(geometry_type, PolygonFeature)


# S3：3D 复合类型 → 2D 归一化（MultiPatch/TIN 降维后统一为面语义）
_GEOM_TYPE_NORMALIZE: dict[str, str] = {
    "MultiPatch": "MultiPolygon",
    "PolyhedralSurface": "MultiPolygon",
    "TIN": "Polygon",
}


# ── 分区管理 ────────────────────────────────────────────────────────────────

def _ensure_partition_sync(session, model, stored_file_id: str) -> None:
    """（同步）确保某数据源在对应分区父表下有物理分区，无则创建。

    幂等：SELECT to_regclass 判存，存在即跳过。stored_file_id 为内部 uuid，
    分区名安全（-/_ 均为 PG 标识符合法字符）；FOR VALUES 内联前做引号转义。
    """
    table = model.__tablename__
    pname = partition_name(table, stored_file_id)
    exists = session.execute(
        text("SELECT to_regclass(:p)"), {"p": pname}
    ).scalar()
    if not exists:
        safe_sid = stored_file_id.replace("'", "''")
        session.execute(text(
            f"CREATE TABLE IF NOT EXISTS {pname} PARTITION OF {table} "
            f"FOR VALUES IN ('{safe_sid}')"
        ))
        session.flush()


async def _ensure_partition(db: AsyncSession, model, stored_file_id: str) -> None:
    """（异步）确保分区存在（SHP/GeoJSON 等异步写入路径使用）。

    注意：asyncpg 不支持 DDL 语句参数绑定，FOR VALUES 必须内联。
    """
    table = model.__tablename__
    pname = partition_name(table, stored_file_id)
    exists = (await db.execute(
        text("SELECT to_regclass(:p)"), {"p": pname}
    )).scalar()
    if not exists:
        safe_sid = stored_file_id.replace("'", "''")
        await db.execute(text(
            f"CREATE TABLE IF NOT EXISTS {pname} PARTITION OF {table} "
            f"FOR VALUES IN ('{safe_sid}')"
        ))
        await db.flush()


async def drop_partition(db: AsyncSession, stored_file_id: str) -> None:
    """删除某数据源在全部三张表上的物理分区（DETACH + DROP，秒级）。"""
    for model in ALL_FEATURE_TABLES:
        table = model.__tablename__
        pname = partition_name(table, stored_file_id)
        exists = (await db.execute(
            text("SELECT to_regclass(:p)"), {"p": pname}
        )).scalar()
        if exists:
            await db.execute(text(
                f"ALTER TABLE {table} DETACH PARTITION {pname}"
            ))
            await db.execute(text(f"DROP TABLE {pname}"))


def drop_partition_sync(session, stored_file_id: str) -> None:
    """（同步）删除某数据源的全部物理分区。"""
    for model in ALL_FEATURE_TABLES:
        table = model.__tablename__
        pname = partition_name(table, stored_file_id)
        exists = session.execute(
            text("SELECT to_regclass(:p)"), {"p": pname}
        ).scalar()
        if exists:
            session.execute(text(
                f"ALTER TABLE {table} DETACH PARTITION {pname}"
            ))
            session.execute(text(f"DROP TABLE {pname}"))


# ── 要素写入 ─────────────────────────────────────────────────────────────────

def _write_feature_common(model_cls, stored_file_id, api_key_id, filename, feat, srid):
    """构造分表 ORM 对象（async/sync 共用）。返回 (obj, status)。"""
    geom = feat.get("geometry")
    if not geom or not geom.get("type"):
        return None, "skipped_no_geometry"
    geom_type = geom["type"]
    wkt_str, ok = geojson_to_wkt_status(geom)
    sf = model_cls(
        id=str(uuid.uuid4()),
        stored_file_id=stored_file_id,
        api_key_id=api_key_id,
        layer_name=feat.get("layer_name", ""),
        source_name=filename,
        geometry=WKTElement(wkt_str, srid=srid) if ok else None,
        geometry_type=_GEOM_TYPE_NORMALIZE.get(geom_type, geom_type),
        properties=feat.get("properties"),
        feature_index=feat.get("feature_index", 0),
    )
    return sf, ("written" if ok else "skipped_empty")


async def write_feature(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str,
    filename: str,
    feat: dict,
    srid: int = 4326,
    ensure: bool = True,
) -> str:
    """将单条要素写入对应的分区表。

    Args:
        srid: 源数据坐标系 SRID。S2 起各要素写入其源 CRS 的 SRID
              （列 typmod 已移除 SRID 约束），预览查询端统一 ST_Transform 到 4326。
        ensure: 写入前确保分区存在（默认 True）。

    Returns:
        "written" — 写入成功（含 make_valid 修复后的几何）
        "skipped_empty" — 几何无法解析/修复，以 geometry=NULL 落库（属性保留）
        "skipped_no_geometry" — 无几何字段，未写入
    """
    model_cls = _model_for_type(feat.get("geometry", {}).get("type") or "")
    if ensure:
        await _ensure_partition(db, model_cls, stored_file_id)
    sf, status = _write_feature_common(
        model_cls, stored_file_id, api_key_id, filename, feat, srid
    )
    if sf is not None:
        db.add(sf)
    return status


async def write_features_batch(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str,
    filename: str,
    features: list,
    flush_every: int = 1000,
    srid: int = 4326,
) -> dict:
    """批量写入要素并分批 flush（v2.10：不截断，全量入库）。

    Returns:
        {"written": int, "skipped_empty": int, "skipped_no_geometry": int, "total": int}
    """
    stats = {"written": 0, "skipped_empty": 0, "skipped_no_geometry": 0,
             "total": len(features)}
    # 批量前一次性确保全部三类分区（幂等；同一源跨类型图层时三表都可能用到）
    for model in ALL_FEATURE_TABLES:
        await _ensure_partition(db, model, stored_file_id)
    for i, feat in enumerate(features):
        status = await write_feature(
            db, stored_file_id, api_key_id, filename, feat, srid, ensure=False,
        )
        stats[status] = stats.get(status, 0) + 1
        if (i + 1) % flush_every == 0:
            await db.flush()
    return stats


def write_features_batch_sync(
    session,
    stored_file_id: str,
    api_key_id: str,
    filename: str,
    features,
    flush_every: int = 1000,
    srid: int = 4326,
) -> dict:
    """（同步）批量写入要素 — asyncio.to_thread 内调用，避免阻塞事件循环。

    features 可为 list 或生成器（GDB 流式读取直接消费）。
    """
    stats = {"written": 0, "skipped_empty": 0, "skipped_no_geometry": 0, "total": 0}
    ensured: set = set()
    for i, feat in enumerate(features):
        geom = feat.get("geometry") or {}
        model_cls = _model_for_type(geom.get("type") or "")
        tname = model_cls.__tablename__
        if tname not in ensured:
            _ensure_partition_sync(session, model_cls, stored_file_id)
            ensured.add(tname)
        sf, status = _write_feature_common(
            model_cls, stored_file_id, api_key_id, filename, feat, srid
        )
        stats["total"] += 1
        stats[status] = stats.get(status, 0) + 1
        if sf is not None:
            session.add(sf)
        if (i + 1) % flush_every == 0:
            session.flush()
    return stats


# ── 图层 × 几何类型聚合（v2.10.5）─────────────────────────────────────────────

async def aggregate_layer_stats(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str = "",
) -> dict:
    """从三张分区要素表实时聚合「图层 × 几何类型」统计并落库。

    - upsert stored_file_layers（先删后插，幂等）
    - 回写 stored_files.point_count / line_count / polygon_count 冗余列
      （调用方负责 commit；对象若已在会话中则直接赋值避免 flush 覆盖）

    Returns:
        {"layers": int, "point": int, "line": int, "polygon": int, "total": int}
    """
    from app.models.stored_file import StoredFile as SFModel
    from app.models.stored_file_layer import StoredFileLayer

    # ① 三表 GROUP BY layer_name 聚合（源即真相）
    merged: dict[str, dict[str, int]] = {}
    table_key = [(PointFeature, "point"), (LineFeature, "line"), (PolygonFeature, "polygon")]
    for tbl, key in table_key:
        q = (
            select(tbl.layer_name, func.count().label("c"))
            .where(tbl.stored_file_id == stored_file_id)
            .group_by(tbl.layer_name)
        )
        for row in (await db.execute(q)).all():
            name = row.layer_name or ""
            merged.setdefault(name, {"point": 0, "line": 0, "polygon": 0})[key] = row.c

    # ② 图层行：先删后插（幂等；单文件图层数通常 <100，代价可忽略）
    await db.execute(
        delete(StoredFileLayer).where(StoredFileLayer.stored_file_id == stored_file_id)
    )
    totals = {"point": 0, "line": 0, "polygon": 0}
    for name, cnt in merged.items():
        total_layer = cnt["point"] + cnt["line"] + cnt["polygon"]
        present = [k for k in ("point", "line", "polygon") if cnt[k] > 0]
        geom_type = (
            {"point": "Point", "line": "LineString", "polygon": "Polygon"}.get(present[0])
            if len(present) == 1 else "mixed"
        )
        db.add(StoredFileLayer(
            id=str(uuid.uuid4()),
            stored_file_id=stored_file_id,
            api_key_id=api_key_id,
            layer_name=name or "(默认图层)",
            geometry_type=geom_type,
            point_count=cnt["point"],
            line_count=cnt["line"],
            polygon_count=cnt["polygon"],
            feature_count=total_layer,
        ))
        for k in totals:
            totals[k] += cnt[k]

    # ③ 文件级冗余计数回写（update 语句绕过身份映射，不与调用方挂起的对象状态冲突）
    await db.execute(
        update(SFModel)
        .where(SFModel.id == stored_file_id)
        .values(
            point_count=totals["point"],
            line_count=totals["line"],
            polygon_count=totals["polygon"],
        )
    )
    return {
        "layers": len(merged),
        "point": totals["point"], "line": totals["line"], "polygon": totals["polygon"],
        "total": sum(totals.values()),
    }


async def count_by_file(
    db: AsyncSession, stored_file_id: str, api_key_id: str | None = None
) -> int:
    """统计某文件在三张表中的要素总数。

    api_key_id 为 None 时不做归属过滤（超级管理员场景——其写入的要素归属为 NULL）。
    """
    total = 0
    for tbl in ALL_FEATURE_TABLES:
        conds = [tbl.stored_file_id == stored_file_id]
        if api_key_id is not None:
            conds.append(tbl.api_key_id == api_key_id)
        q = select(func.count()).select_from(tbl).where(*conds)
        total += (await db.execute(q)).scalar() or 0
    return total


async def query_features_by_file(
    db: AsyncSession, stored_file_id: str, api_key_id: str | None = None,
    offset: int = 0, limit: int = 2000,
):
    """跨三表 UNION ALL 查询某文件的空间要素。

    返回 (features_list, total_count)。
    features 每项: {id, feature_index, layer_name, source_name, geometry_type,
                    geometry(GeoJSON dict), properties}
    api_key_id 为 None 时不做归属过滤（超级管理员场景）。
    """
    total = await count_by_file(db, stored_file_id, api_key_id)
    if total == 0:
        return [], 0

    # 逐表查询 + 合并排序（用 feature_index 排序）
    # S2：输出前按每行要素自身 SRID 自适应 ST_Transform 到 4326（Leaflet/GeoJSON 预览要求 WGS84）
    all_rows = []
    for tbl in ALL_FEATURE_TABLES:
        conds = [tbl.stored_file_id == stored_file_id]
        if api_key_id is not None:
            conds.append(tbl.api_key_id == api_key_id)
        q = select(
            tbl.id, tbl.feature_index, tbl.layer_name, tbl.source_name,
            tbl.geometry_type, tbl.properties,
            geo_func.ST_AsGeoJSON(
                case(
                    (func.ST_SRID(tbl.geometry) == 4326, tbl.geometry),
                    else_=func.ST_Transform(tbl.geometry, 4326),
                )
            ).label("geojson"),
        ).where(*conds).order_by(tbl.feature_index)
        rows = (await db.execute(q)).all()
        for r in rows:
            all_rows.append({
                "id": r.id,
                "feature_index": r.feature_index,
                "layer_name": r.layer_name,
                "source_name": r.source_name,
                "geometry_type": r.geometry_type,
                "geometry": __import__("json").loads(r.geojson) if r.geojson else None,
                "properties": r.properties or {},
            })

    # 按 feature_index 排序后分页
    all_rows.sort(key=lambda x: x["feature_index"])
    page = all_rows[offset:offset + limit]
    return page, total


async def transform_features_by_file(
    db: AsyncSession, stored_file_id: str, api_key_id: str,
    source_crs: str, target_crs: str,
    target_srid: int = 4490,
) -> dict:
    """对某文件的空间要素执行 CRS 坐标变换（跨三表，**原地写回**）。

    注意：本接口为「写回」语义（直接改写要素表几何），与 `/spatial/{id}/transform`
    （生成 transform_results 副本、原文件不动）不同。
    v2.13.0 收敛到统一变换引擎：CRS 经 `resolve_transform_crs` 解析（支持
    `Custom:`/纯数字/别名/WKT/proj4，不再静默回退 WGS84），坐标变换复用
    `transform_geometry`（含坐标域校验）；本接口对单要素失败仍逐条跳过并累计。

    S2：写回时按目标 CRS 标注 SRID（不再硬编码 4326）；
    调用方应同步更新 StoredFile.detected_crs，使预览查询 ST_Transform 基于新 SRID。

    v2.10.6：移除内部 commit()，由调用方统一管理事务（避免与后续元数据更新冲突）。
    """
    from shapely.geometry import shape, mapping

    from app.services.transform_engine import resolve_transform_crs, transform_geometry

    src_res = await resolve_transform_crs(db, source_crs)
    dst_res = await resolve_transform_crs(db, target_crs)
    total = 0
    transformed = 0
    failed = 0

    for tbl in ALL_FEATURE_TABLES:
        rows = await db.execute(
            select(tbl).where(
                tbl.stored_file_id == stored_file_id,
            )
        )
        features = rows.scalars().all()
        for feat in features:
            total += 1
            wkt_row = await db.execute(
                select(geo_func.ST_AsText(feat.geometry)).where(tbl.id == feat.id)
            )
            wkt_str = wkt_row.scalar()
            if not wkt_str:
                continue
            geom = shp_wkt.loads(wkt_str)
            if geom.is_empty:
                continue
            try:
                geojson = mapping(geom)
                warnings: list[str] = []
                new_geom = transform_geometry(geojson, src_res, dst_res, warnings=warnings)
                if new_geom is None:
                    failed += 1
                    continue
                new_wkt = shape(new_geom).wkt
                feat.geometry = WKTElement(new_wkt, srid=target_srid)
                transformed += 1
            except Exception as e:
                logger.warning("feature CRS transform failed: %s", e)
                failed += 1

    return {"transformed": transformed, "total": total, "failed": failed}


async def repair_features_by_file(
    db: AsyncSession, stored_file_id: str, api_key_id: str,
) -> dict:
    """对某文件的空间要素执行几何修复（跨三表）。

    v2.10.6：移除 api_key_id 过滤（文件级权限已在调用方校验）；
    移除内部 commit()，由调用方统一管理事务。
    """
    from shapely.validation import make_valid

    total = 0
    fixed = 0

    for tbl in ALL_FEATURE_TABLES:
        rows = await db.execute(
            select(tbl).where(
                tbl.stored_file_id == stored_file_id,
            )
        )
        features = rows.scalars().all()
        for feat in features:
            total += 1
            wkt_row = await db.execute(
                select(
                    geo_func.ST_AsText(feat.geometry),
                    geo_func.ST_SRID(feat.geometry),
                ).where(tbl.id == feat.id)
            )
            row = wkt_row.one()
            wkt_str, feat_srid = row[0], row[1] or 4326
            if not wkt_str:
                continue
            geom = shp_wkt.loads(wkt_str)
            if geom.is_valid:
                continue
            try:
                new_geom = geom.buffer(0)
                if not new_geom.is_valid:
                    new_geom = make_valid(new_geom)
                if new_geom and not new_geom.is_empty:
                    feat.geometry = WKTElement(new_geom.wkt, srid=feat_srid)
                    fixed += 1
            except Exception as e:
                logger.warning("feature geometry repair failed: %s", e)
                continue

    return {"repaired": fixed, "total": total}


async def load_file_features_geojson(
    db: AsyncSession, stored_file_id: str,
) -> list[dict]:
    """读取某文件三张分区表的全部要素，组装 GeoJSON Feature 列表（坐标保持不变）。

    v2.19.0：数据「下载 → 导出」的数据源。与 transform_db_features 不同，
    本函数**不做坐标系变换**，忠实还原入库时的几何与属性。
    每条 Feature 的 properties 会补 `layer_name`（供 MDB 分层/展示），
    `_feature_table` 标记来源表。
    """
    from shapely.geometry import mapping as _shp_mapping

    features: list[dict] = []
    for tbl in ALL_FEATURE_TABLES:
        rows = await db.execute(
            select(tbl).where(tbl.stored_file_id == stored_file_id)
        )
        for feat in rows.scalars().all():
            wkt_str = (
                await db.execute(
                    select(geo_func.ST_AsText(feat.geometry)).where(tbl.id == feat.id)
                )
            ).scalar()
            if not wkt_str:
                continue
            geom = shp_wkt.loads(wkt_str)
            if geom.is_empty:
                continue
            props = dict(feat.properties or {})
            props.setdefault("layer_name", feat.layer_name or "")
            features.append({
                "type": "Feature",
                "geometry": _shp_mapping(geom),
                "properties": props,
            })
    return features
