"""统一坐标变换引擎 — v2.13 新增。

汇总此前分散在 `spatial.py` / `feature_store.py` / `script_generation.py` 的
坐标变换逻辑，提供**单一变换入口**，并补齐两项能力：

1. **自定义坐标系解析**：统一复用 `custom_crs.resolve_ingest_srid_async`，
   使「手动输入的坐标系」（含 `Custom:` 前缀别名、WKT、proj4、中文别名）
   都能被正确解析并注册 SRID，而非静默回退 WGS84。
2. **变换正确性校验**：变换前后对每个要素做坐标域合理性校验 + 失败统计，
   避免静默跳过导致的"看起来转换了、实际坐标错位"。

设计要点：
- CRS 经 `resolve_transform_crs` 统一解析（复用 `custom_crs` 权威链路），返回
  `ResolvedCRS`，其中 `srid` 用于落库/审计，`pyproj_crs` 是传给 pyproj 的权威
  定义（标准 EPSG → `EPSG:xxx`；自定义投影坐标系 → WKT/proj4 原文，因 pyproj
  无法识别自定义 SRID 注册号）。
- `pyproj.Transformer` 用 `pyproj_crs` 互转，`always_xy=True` 保证输入输出恒为
  (x, y)/(lon, lat) 顺序，规避轴序颠倒。
- 保留 Z 维（若源要素存在），且不改变几何之外的属性/图层信息。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pyproj

logger = logging.getLogger(__name__)

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.custom_crs import SridResolution, resolve_ingest_srid_async

# 地理坐标系坐标域（度）
GEO_DOMAIN = (-180.0, -90.0, 180.0, 90.0)


# ── 解析结果 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResolvedCRS:
    """统一解析后的坐标系。

    - srid: 权威 SRID（自定义坐标系会注册到 PostGIS spatial_ref_sys，供 ST_Transform 使用，
            仅用于落库记录/审计，**不能**直接作为 pyproj 的 EPSG 编码使用）。
    - pyproj_crs: 传给 `pyproj.Transformer.from_crs` 的权威 CRS 定义。
                  - 标准 EPSG 坐标系 → `EPSG:{epsg}`
                  - 自定义投影坐标系（无 EPSG 编码）→ WKT/proj4 原文（pyproj 直接可解析）
                  这样变换时无需依赖 PROJ 数据库是否认识自定义 SRID。
    - display: 用于记录到 transform_results 的可读标识（如 EPSG:4490 / Custom:shanghaicity）。
    """
    srid: int
    pyproj_crs: str
    epsg: int | None          # 能映射 EPSG 时非空（自定义坐标系为 None）
    is_custom: bool           # True = 注册的自定义 SRID
    source: str               # epsg / alias / wkt-custom / fallback-*
    display: str              # 可读标识


async def resolve_transform_crs(db: AsyncSession, crs_str: str | None) -> ResolvedCRS:
    """统一解析 CRS 字符串 → 权威 SRID + pyproj CRS 定义 + 可读标识。

    兼容：EPSG:xxxx / 纯数字 / 中文别名 / WKT / proj4 / URN / `Custom:code`。
    `Custom:code` 会先映射到内置自定义坐标系定义，再走 `resolve_ingest_srid_async`
    确保 SRID 注册，并携带其 WKT 作为 pyproj_crs。其余交给同一解析链路。

    无法解析时 `resolve_ingest_srid_async` 会回退 SRID=4326（WGS84），
    并标记 `source=fallback-*`，此处透传该信息供调用方决定是否告警。
    """
    src = (crs_str or "").strip()
    if not src:
        return ResolvedCRS(srid=4326, pyproj_crs="EPSG:4326", epsg=4326,
                           is_custom=False, source="fallback-empty", display="EPSG:4326")

    # Custom:code → 展开为对应内置自定义坐标系定义
    if src.lower().startswith("custom:"):
        code = src.split(":", 1)[1].strip().lower()
        custom_def = _CUSTOM_CRS_DEFINITIONS.get(code)
        if custom_def is not None:
            return await _resolve_custom(db, custom_def, display=f"Custom:{code}")
        # 未知 Custom:code，交通用解析（大概率回退 4326）
        return await _resolve_generic(db, src, display=src)

    # 内置自定义 code（shanghaicity / shanghai_local 等）直接映射
    custom_def = _CUSTOM_CRS_DEFINITIONS.get(src.lower())
    if custom_def is not None:
        return await _resolve_custom(db, custom_def, display=src)

    return await _resolve_generic(db, src, display=src)


async def _resolve_custom(db: AsyncSession, custom_def: "CustomCRSDef",
                          display: str) -> ResolvedCRS:
    """解析内置自定义坐标系：注册 SRID（供 ST_Transform/审计），并以 WKT 作为 pyproj_crs。"""
    res = await resolve_ingest_srid_async(db, custom_def.wkt)
    # 以库里注册（可能已应用假东/假北纠正）的 proj4text 作为 pyproj 权威定义，
    # 确保 pyproj 与 PostGIS ST_Transform 使用完全一致的（纠正后）定义。
    pyproj_crs = await _pyproj_crs_for(db, res, fallback=custom_def.wkt)
    return ResolvedCRS(
        srid=res.srid,
        pyproj_crs=pyproj_crs,   # WKT 可直接给 pyproj 解析
        epsg=res.epsg, is_custom=res.is_custom,
        source=res.source, display=display,
    )


async def _pyproj_crs_for(
    db: AsyncSession,
    res: "SridResolution",
    fallback: str,
) -> str:
    """自定义 SRID 时，从 spatial_ref_sys 取回注册（纠正后）的 proj4text 作为 pyproj 定义。

    关键：`resolve_ingest_srid_async` 入库时会应用「假东/假北互换」纠正
    （见 custom_crs._apply_false_origin_fix），因此库里 proj4text 是数学上正确的
    定义。若直接拿用户原始 WKT/proj4 喂 pyproj，可能按错误的 False_Easting 解析，
    导致变换结果漂移到错误位置（如飞到大洋中部）。此处复用纠正后的定义保证一致。
    """
    if not res.is_custom:
        return fallback
    try:
        row = (await db.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid = :srid"),
            {"srid": res.srid},
        )).mappings().first()
        if row and row["proj4text"]:
            return row["proj4text"]
    except Exception as e:
        logger.debug("proj4 lookup failed for SRID %s: %s", res.srid if hasattr(res, 'srid') else "?", e)
    return fallback


async def _resolve_generic(db: AsyncSession, src: str, display: str) -> ResolvedCRS:
    """通用解析：交给 custom_crs 的权威链路。

    能映射 EPSG → pyproj_crs=EPSG:{epsg}；否则（如 WKT/proj4 自定义）→ 复用库里
    注册（纠正后）的 proj4 供 pyproj 解析；完全不可解析回退 4326。
    """
    res = await resolve_ingest_srid_async(db, src)
    if res.epsg:
        pyproj_crs = f"EPSG:{res.epsg}"
    elif res.is_custom:
        # 自定义 SRID：无法从 PROJ 数据库直接引用，回填库里注册（纠正后）的 proj4text
        pyproj_crs = await _pyproj_crs_for(db, res, fallback=src)
    elif res.source in ("fallback-empty", "fallback-unparseable"):
        pyproj_crs = "EPSG:4326"
    else:
        pyproj_crs = src
    display_epsg = f"EPSG:{res.srid}" if (res.epsg or res.srid) else src
    return ResolvedCRS(
        srid=res.srid, pyproj_crs=pyproj_crs, epsg=res.epsg,
        is_custom=res.is_custom, source=res.source, display=display_epsg,
    )


# ── 内置自定义坐标系定义（与 spatial.py 的 _CUSTOM_CRS 对齐）────────────────
# 统一维护在此，spatial.py 可直接 import 以消除重复。
_CUSTOM_CRS_DEFINITIONS: dict[str, "CustomCRSDef"] = {}


@dataclass(frozen=True)
class CustomCRSDef:
    code: str
    name: str
    wkt: str                      # PROJCS/GEOGCS WKT 全文，用于注册 SRID


def _register_custom(code: str, name: str, wkt: str) -> None:
    _CUSTOM_CRS_DEFINITIONS[code.lower()] = CustomCRSDef(code, name, wkt)


def init_custom_crs_definitions() -> None:
    """初始化内置自定义坐标系（幂等，可重复调用）。"""
    # 上海城市坐标系（Beijing 1954 / Krasovsky 1940，横轴墨卡托，CM 121.275°）
    _register_custom(
        "shanghaicity", "上海城市坐标系",
        'PROJCS["ShanghaiCity",GEOGCS["GCS_Beijing_1954",'
        'DATUM["D_Beijing_1954",SPHEROID["Krasovsky_1940",6378245,298.3]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["False_Easting",0],PARAMETER["False_Northing",-3457147.81],'
        'PARAMETER["Central_Meridian",121.275],PARAMETER["Scale_Factor",1],'
        'PARAMETER["Latitude_Of_Origin",0],UNIT["meter",1]]',
    )
    # 上海地方坐标系（新）（CGCS2000，横轴墨卡托，CM 121.467°）
    _register_custom(
        "shanghai_local", "上海地方坐标系(新)",
        'PROJCS["ShanghaiLocal",GEOGCS["CGCS2000",'
        'DATUM["China_2000",SPHEROID["CGCS2000",6378137,298.257222101]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["False_Easting",0],PARAMETER["False_Northing",-3457000],'
        'PARAMETER["Central_Meridian",121.467],PARAMETER["Scale_Factor",1],'
        'PARAMETER["Latitude_Of_Origin",0],UNIT["meter",1]]',
    )


init_custom_crs_definitions()


# ── 返回给 spatial.py 渲染 CRS 目录的自定义列表 ──────────────────────────────

def get_custom_crs_catalog() -> list[dict]:
    """返回自定义坐标系目录（供 `/spatial/crs/catalog` 下拉使用）。"""
    return [
        {
            "srid": 0,  # 真实 SRID 需经 resolve_transform_crs 获取，此处仅作占位
            "code": d.code,
            "name": d.name,
            "definition": d.wkt,
        }
        for d in _CUSTOM_CRS_DEFINITIONS.values()
    ]


# ── 变换统计 ─────────────────────────────────────────────────────────────────

@dataclass
class TransformStats:
    total: int = 0
    transformed: int = 0
    failed: int = 0
    skipped: int = 0                  # 空几何 / 无几何
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


# ── 坐标域校验 ───────────────────────────────────────────────────────────────

def _coord_domain_ok(x: float, y: float) -> bool:
    """坐标点是否落在地理坐标系合法域内（±180/±90）。"""
    gxmin, gymin, gxmax, gymax = GEO_DOMAIN
    return gxmin <= x <= gxmax and gymin <= y <= gymax


def _extent_of(geojson: dict) -> tuple[float, float, float, float] | None:
    """计算 GeoJSON geometry 的 bbox (xmin,ymin,xmax,ymax)。无坐标返回 None。"""
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords, depth=0):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            x, y = coords[0], coords[1]
            xs.append(float(x)); ys.append(float(y))
        else:
            for c in coords:
                walk(c, depth + 1)

    coords = geojson.get("coordinates")
    if not coords:
        return None
    walk(coords)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _coords_finite(geojson: dict) -> bool:
    """GeoJSON geometry 的全部坐标是否都是有限值（非 inf / NaN）。"""
    import math

    def walk(coords):
        if not coords:
            return True
        if isinstance(coords[0], (int, float)):
            return all(math.isfinite(float(v)) for v in coords[:2])
        return all(walk(c) for c in coords)

    # GeometryCollection：递归检查每个子几何
    if geojson.get("type") == "GeometryCollection":
        return all(_coords_finite(sub) for sub in (geojson.get("geometries") or []))

    coords = geojson.get("coordinates")
    if coords is None:
        return True
    return walk(coords)


# ── 单点 / 递归变换 ─────────────────────────────────────────────────────────

def _transform_xy(coord, transformer) -> list:
    """变换单个坐标点，保留 Z 维（若存在）。返回 [x, y] 或 [x, y, z]。"""
    x, y = coord[0], coord[1]
    new_x, new_y = transformer.transform(float(x), float(y))
    return [new_x, new_y] + (list(coord[2:]) if len(coord) > 2 else [])


def _transform_coord_tree(geojson: dict, transformer) -> None:
    """递归变换 GeoJSON geometry 全部坐标（按 type 分发）。原地修改。"""
    gtype = geojson.get("type")
    # GeometryCollection 无 coordinates，需先递归子几何再返回（否则被空 check 提前 return）
    if gtype == "GeometryCollection":
        for sub in (geojson.get("geometries") or []):
            _transform_coord_tree(sub, transformer)
        return
    coords = geojson.get("coordinates")
    if not coords:
        return
    if gtype == "Point":
        geojson["coordinates"] = _transform_xy(coords, transformer)
    elif gtype in ("MultiPoint", "LineString"):
        geojson["coordinates"] = [_transform_xy(c, transformer) for c in coords]
    elif gtype in ("MultiLineString", "Polygon"):
        geojson["coordinates"] = [
            [_transform_xy(c, transformer) for c in ring] for ring in coords
        ]
    elif gtype == "MultiPolygon":
        geojson["coordinates"] = [
            [[_transform_xy(c, transformer) for c in ring] for ring in poly]
            for poly in coords
        ]


# ── 单要素变换（含校验）─────────────────────────────────────────────────────

def transform_geometry(geojson: dict, src: "ResolvedCRS", dst: "ResolvedCRS",
                       warnings: list[str] | None = None) -> dict | None:
    """对单个 GeoJSON geometry 做坐标变换，并在变换前后做坐标域校验。

    Args:
        geojson: GeoJSON geometry dict（Point/LineString/Polygon/…）。
        src / dst: 源/目标坐标系（经 `resolve_transform_crs` 解析，含 pyproj CRS 定义与 SRID）。
        warnings: 可选的告警收集列表（用于记录坐标域疑问）。

    Returns:
        变换后的 GeoJSON geometry dict；几何为空/非法返回 None。

    校验说明（方案2）：
        - 若源 CRS 为地理坐标系（度），源坐标超出 ±180/±90 → 记告警（疑似投影误标），
          但仍尝试变换（交由调用方决定是否阻断）。
        - 变换后结果同样做坐标域合理区间检查，明显异常记告警。
    """
    coords = geojson.get("coordinates") or geojson.get("geometries")
    if not coords and geojson.get("type") != "GeometryCollection":
        # 无坐标的空几何
        return None

    # 判定源 CRS 是否地理（度）；无注册信息时保守为 False（不做域校验误报）
    is_geo_src = _srid_is_geographic(src.srid)
    extent_before = _extent_of(geojson)

    if is_geo_src and extent_before:
        # 源为地理坐标系时，坐标应落在 ±180/±90 内
        if not (_coord_domain_ok(extent_before[0], extent_before[1])
                and _coord_domain_ok(extent_before[2], extent_before[3])):
            if warnings is not None:
                warnings.append(
                    f"源 SRID={src.srid} 标记为地理坐标系(度)，但坐标范围 "
                    f"[{extent_before[0]:.2f}, {extent_before[1]:.2f}]~"
                    f"[{extent_before[2]:.2f}, {extent_before[3]:.2f}] 超出 "
                    f"±180/±90，疑为投影坐标误标源 CRS"
                )

    transformer = pyproj.Transformer.from_crs(
        src.pyproj_crs, dst.pyproj_crs, always_xy=True,
    )

    # 深拷贝后原地变换（避免污染调用方对象）
    import copy
    target = copy.deepcopy(geojson)
    try:
        _transform_coord_tree(target, transformer)
    except Exception as e:
        if warnings is not None:
            warnings.append(f"坐标变换失败: {e}")
        return None

    # 变换结果坐标必须是有限值；含 inf/NaN 说明源/目标 CRS 域不匹配
    # （如把投影米制坐标误作地理经纬度），视为该要素变换失败，返回 None。
    if not _coords_finite(target):
        if warnings is not None:
            warnings.append(
                f"变换结果含无效坐标(inf/NaN)：源 SRID={src.srid}({src.display}) → "
                f"目标 SRID={dst.srid}({dst.display}) 域不匹配，"
                f"请检查源坐标系是否选择正确"
            )
        return None

    # 变换后坐标域合理性检查
    extent_after = _extent_of(target)
    if extent_after and warnings is not None:
        # 目标为地理坐标系时结果应落在 ±180/±90 内
        is_geo_dst = _srid_is_geographic(dst.srid)
        if is_geo_dst and not (_coord_domain_ok(extent_after[0], extent_after[1])
                               and _coord_domain_ok(extent_after[2], extent_after[3])):
            warnings.append(
                f"目标 SRID={dst.srid} 为地理坐标系(度)，但变换后坐标范围 "
                f"[{extent_after[0]:.2f}, {extent_after[1]:.2f}]~"
                f"[{extent_after[2]:.2f}, {extent_after[3]:.2f}] 超出 ±180/±90，"
                f"结果疑似异常"
            )

    return target


def _srid_is_geographic(srid: int) -> bool:
    """快速判定 SRID 是否地理坐标系（度）。

    仅针对内置/常用 SRID 做硬编码判定；未知 SRID 保守返回 False（不做域校验误报）。
    精确判定依赖 spatial_ref_sys 查询，由调用方（带 DB 上下文）补充。
    """
    # EPSG 地理坐标系常见区段（4326/4490/4214/4610 等及 42xx/46xx 系列）
    if srid in (4326, 4490, 4214, 4610, 4979):
        return True
    # 自定义 SRID（91xxxx）默认为投影
    if srid >= 910000:
        return False
    # 42xx（北京54 地理）、46xx（西安80 地理）等
    if 4200 <= srid <= 4299 or 4600 <= srid <= 4699 or 4900 <= srid <= 4999:
        return True
    return False


# ── 批量要素变换（供 spatial.py / 其他调用方使用）───────────────────────────

def transform_feature_dict(feature: dict, src: "ResolvedCRS", dst: "ResolvedCRS",
                           warnings: list[str] | None = None) -> dict | None:
    """变换单条 Feature dict（保留 properties 等原样），返回新 Feature dict 或 None。"""
    geom = feature.get("geometry")
    if not geom or not geom.get("type"):
        return None
    new_geom = transform_geometry(geom, src, dst, warnings=warnings)
    if new_geom is None:
        return None
    return {
        "type": "Feature",
        "geometry": new_geom,
        "properties": (feature.get("properties") or {}),
    }


def transform_feature_list(features: list[dict], src: "ResolvedCRS", dst: "ResolvedCRS",
                           warnings: list[str] | None = None) -> tuple[list[dict], TransformStats]:
    """批量变换 Feature dict 列表。

    Returns:
        (out_features, stats) — out_features 仅含成功变换的要素，stats 汇总计数。
    """
    stats = TransformStats(total=len(features))
    out: list[dict] = []
    local_warnings: list[str] = [] if warnings is None else warnings
    for feat in features:
        geom = feat.get("geometry")
        if not geom or not geom.get("type"):
            stats.skipped += 1
            continue
        new_geom = transform_geometry(geom, src, dst, warnings=local_warnings)
        if new_geom is None:
            stats.failed += 1
            continue
        out.append({
            "type": "Feature",
            "geometry": new_geom,
            "properties": (feat.get("properties") or {}),
        })
        stats.transformed += 1
    stats.warnings = list(local_warnings)
    return out, stats


# ── 数据库要素变换（spatial.py 专用：读取三表要素 → 转换坐标 → 组装 GeoJSON）─

async def transform_db_features(
    db: AsyncSession,
    file_id: str,
    src: "ResolvedCRS",
    dst: "ResolvedCRS",
    include_table_name: bool = False,
) -> tuple[list[dict], TransformStats]:
    """从三张分区表读取某文件全部要素，变换几何坐标，返回 Feature 列表 + 统计。

    Args:
        file_id: StoredFile.id。
        src / dst: 源/目标坐标系（经 `resolve_transform_crs` 解析）。
        include_table_name: True 时在 properties 附加 `_feature_table`（标记来源表）。

    Returns:
        (features_geojson, stats) — features 均为成功变换的 Feature。
    """
    from app.models.spatial_feature import ALL_FEATURE_TABLES
    from geoalchemy2.functions import ST_AsText as ST_AsText_func
    from shapely import wkt as shp_wkt
    from shapely.geometry import mapping

    stats = TransformStats()
    features_geojson: list[dict] = []
    warnings: list[str] = []

    for tbl in ALL_FEATURE_TABLES:
        rows = await db.execute(
            select(tbl).where(tbl.stored_file_id == file_id)
        )
        feats = rows.scalars().all()
        for feat in feats:
            stats.total += 1
            wkt_row = await db.execute(
                select(ST_AsText_func(feat.geometry)).where(tbl.id == feat.id)
            )
            wkt_str = wkt_row.scalar()
            if not wkt_str:
                stats.skipped += 1
                continue
            geom = shp_wkt.loads(wkt_str)
            if geom.is_empty:
                stats.skipped += 1
                continue
            try:
                geojson_geom = mapping(geom)
                new_geom = transform_geometry(
                    geojson_geom, src, dst, warnings=warnings
                )
                if new_geom is None:
                    stats.failed += 1
                    continue
                props = (feat.properties or {})
                if include_table_name:
                    props = {**props, "_feature_table": tbl.__tablename__}
                features_geojson.append({
                    "type": "Feature",
                    "geometry": new_geom,
                    "properties": props,
                })
                stats.transformed += 1
            except Exception as e:
                logger.warning("feature coordinate transform failed: %s", e)
                stats.failed += 1

    stats.warnings = warnings
    return features_geojson, stats
