"""自定义坐标系 SRID 解析与注册 — v2.10.5 新增。

背景（v2.10.4 面裂隙/极大面/极小面质检 500 的根因）：
    自定义投影坐标系（如上海地方坐标系 shanghaicity，PROJCS 全文无 EPSG 编码）
    在入库时被 `crs_to_srid()` 回退标记为 SRID=4326（WGS84 经纬度），
    而坐标仍是投影米值。下游 ST_Transform(geom, 4549) 把米值当经纬度解释，
    PROJ 抛出 "transform: Invalid coordinate (2049)"。

本模块的职责：
    1. 解析 CRS 字符串（EPSG:xxxx / 别名 / WKT / proj4），能映射 EPSG 则用 EPSG；
    2. 无法映射 EPSG 但可被 pyproj 解析时 —— 确定性生成自定义 SRID 并幂等注册到
       PostGIS spatial_ref_sys（写入 srtext + proj4text），使 ST_Transform 全链路可用；
    3. 坐标域合理性校验助手（地理坐标系下坐标超出 ±180/±90 时告警）。

设计约束：
    - 自定义 SRID 必须跨进程/跨重启稳定 → 用 hashlib（不能用内建 hash()，其带随机盐）
    - 注册必须幂等（ON CONFLICT DO NOTHING + proj4text 一致性校验）
    - SRID 取值范围 910000~998999（避开 EPSG 官方区段与 ESRI/IAU 保留区段）
"""

import hashlib
import logging
import re
import warnings
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 自定义 SRID 区段（PostGIS 惯例：>90000 为非 EPSG；此处取高位段避免冲突）
CUSTOM_SRID_BASE = 910_000
CUSTOM_SRID_SPAN = 89_000          # 910000 ~ 998999
AUTH_NAME = "VCS-CUSTOM"

# 无任何 CRS 信息时的兜底（唯一允许回退 4326 的场景）
DEFAULT_SRID = 4326


@dataclass(frozen=True)
class SridResolution:
    """SRID 解析结果。"""
    srid: int
    is_custom: bool        # True = 注册到 spatial_ref_sys 的自定义 SRID
    epsg: int | None       # 能映射 EPSG 时非空
    source: str            # epsg / alias / wkt-custom / fallback-empty / fallback-unparseable


# ── 进程内缓存（同一 CRS 字符串只解析+确认注册一次）─────────────────────────
_resolution_cache: dict[str, SridResolution] = {}


def _normalize_crs_text(crs_str: str | None) -> str:
    """规范化 CRS 文本用于缓存键与哈希（压缩连续空白）。"""
    return " ".join((crs_str or "").split())


def _deterministic_custom_srid(proj4_norm: str, salt: int = 0) -> int:
    """由 proj4 文本的 SHA-256 确定性推导自定义 SRID（跨进程稳定）。

    注意：哈希输入必须是「源文件声明的原始 proj4」（未做假东/假北纠正），
    以保证同一份历史文件在任何代码版本下推导出同一个 SRID；
    纠正只影响入库的 proj4text（PostGIS 实际用于变换的字段）。
    """
    digest = hashlib.sha256(f"{salt}:{proj4_norm}".encode("utf-8")).hexdigest()
    return CUSTOM_SRID_BASE + (int(digest[:12], 16) % CUSTOM_SRID_SPAN)


# 合法横轴墨卡托类投影的假东上限：UTM 为 500km，带号编码制 GK 最大约 835km。
# 超过该值的"假东"几乎必然是把假北（子午线弧长量级，如上海 ~3457km）误写进假东。
_SUSPECT_FALSE_EASTING_M = 999_000.0


def _fix_swapped_false_origin(proj4_norm: str) -> tuple[str, str | None]:
    """检测并纠正「假东/假北互换」的导出缺陷（仅 proj4 文本）。

    背景（v2.10.5，FW.gdb 上海地方坐标系实案）：
        源 WKT 声明 False_Easting=-3457147.81 / False_Northing=0，但数据实测
        x∈[-25km,+33km]（中央子午线偏移量级）、y∈[15km,65km]。
        数值验证：按声明参数变换落点 (150.99E, 0.18N)（西太平洋），
        按 x_0/y_0 互换后落点 (121.39E, 31.44N)（上海）✓。

    判据（保守，避免误伤）：
        |x_0| > 999km 且 y_0 == 0 且 x_0 不是带号制 GK 假东 → 判定互换。
    带号制 GK 例外（v2.10.6，MDB 实案）：
        CGCS2000 3° GK Zone 41 的 false_easting = 41,500,000 m
        = 带号 41 × 1,000,000 + 500,000，是【合法标准定义】（EPSG:4549），
        坐标 x ∈ [41,000,000, 42,000,000) 域内。若按旧判据误判为互换，
        会注册成 x_0=0 / y_0=41,500,000，ST_Transform 直接报
        "Point outside of projection domain (2050)"。
    排除规则：x_0 > 0 且 (x_0 - 500,000) % 1,000,000 == 0 →
        形如 n×1e6+500k 的标准带号 GK 假东，不判定互换。
        上海地方坐标系 x_0=-3,457,147.81（负值/非带号形式）不受影响。

    重要：PostGIS 3 + PROJ6 在 srtext 可解析时【优先用 srtext】做变换，
        proj4text 只是回退 —— 因此必须同时纠正 srtext
        （见 _fix_swapped_false_origin_wkt）。源文件原始声明已完整保存在
        stored_files.detected_crs，spatial_ref_sys 中做纠正不影响溯源。

    返回 (纠正后 proj4, 告警文案|None)。
    """
    m_x = re.search(r"\+x_0=(-?[\d.]+)", proj4_norm)
    m_y = re.search(r"\+y_0=(-?[\d.]+)", proj4_norm)
    if not m_x or not m_y:
        return proj4_norm, None
    try:
        x0, y0 = float(m_x.group(1)), float(m_y.group(1))
    except ValueError:
        return proj4_norm, None
    if abs(x0) <= _SUSPECT_FALSE_EASTING_M or y0 != 0.0:
        return proj4_norm, None

    # 带号制 GK 例外：合法假东形如 n×1e6+500k（n=带号），不判定互换
    if x0 > 0 and (x0 - 500_000.0) % 1_000_000.0 == 0:
        return proj4_norm, None

    fixed = proj4_norm.replace(
        f"+x_0={m_x.group(1)}", "+x_0=0", 1
    ).replace(
        f"+y_0={m_y.group(1)}", f"+y_0={m_x.group(1)}", 1
    )
    warning = (
        f"CRS 假东/假北疑似互换（导出缺陷）：声明的假东 {x0:.2f} 超出合法范围"
        f"(>{_SUSPECT_FALSE_EASTING_M:.0f}m) 且假北为 0，已按 x_0=0 / y_0={x0:.2f} "
        f"纠正注册。此缺陷仅影响地理定位（预览落点），不影响面积/长度等量算结果。"
    )
    return fixed, warning


def _fix_swapped_false_origin_wkt(wkt: str | None, x0: float) -> str | None:
    """同步纠正 WKT1/WKT2 文本中的 False easting/northing 参数值。

    仅在 _fix_swapped_false_origin 判定互换后调用；无法解析时原样返回。
    """
    if not wkt:
        return wkt
    try:
        # WKT1 (ESRI/ArcGIS): PARAMETER["False_Easting",-3457147.81]
        # WKT2 (OGC):         PARAMETER["False easting",-3457147.81,LENGTHUNIT[...]]
        # 两种大小写变体统一按不区分大小写处理
        wkt = re.sub(
            r'(PARAMETER\["False[_ ]easting",\s*)-?[\d.]+',
            r'\g<1>0',
            wkt, flags=re.IGNORECASE,
        )
        wkt = re.sub(
            r'(PARAMETER\["False[_ ]northing",\s*)-?[\d.]+',
            rf'\g<1>{x0:.2f}',
            wkt, flags=re.IGNORECASE,
        )
        return wkt
    except Exception:
        return wkt


def _parse_with_pyproj(crs_str: str):
    """用 pyproj 解析 CRS 字符串。返回 (epsg|None, proj4|None, wkt|None)；不可解析返回 None。"""
    try:
        from pyproj import CRS
        with warnings.catch_warnings():
            # to_proj4 在新版 PROJ 中有 deprecation warning，但对 spatial_ref_sys 是必需格式
            warnings.simplefilter("ignore")
            crs = CRS.from_user_input(crs_str)
            epsg = crs.to_epsg()
            proj4 = crs.to_proj4()
            wkt = crs.to_wkt()
        return epsg, (proj4.strip() if proj4 else None), wkt
    except Exception:
        return None


def parse_crs_definition(crs_str: str | None) -> tuple[int | None, str | None, str | None]:
    """纯解析（不触数据库）：返回 (epsg|None, proj4|None, wkt|None)。

    - None 输入 → (4326, None, None)
    - EPSG:xxxx / 纯数字 / 中文别名 → 对应 EPSG
    - pyproj 可解析的任意 WKT/proj4/URN → epsg 或 (None, proj4, wkt)
    - 完全不可解析 → (None, None, None)
    """
    from app.services.crs_transform import resolve_crs

    if not crs_str or not crs_str.strip():
        return DEFAULT_SRID, None, None

    info = resolve_crs(crs_str)
    if info.epsg:
        return info.epsg, info.proj4, info.wkt

    # resolve_crs 已尝试过 pyproj；再补一次以获取 proj4/wkt（resolve_crs 可能只回了原始 WKT）
    parsed = _parse_with_pyproj(crs_str)
    if parsed and parsed[0]:
        return parsed[0], parsed[1], parsed[2]
    if parsed and parsed[1]:
        return None, parsed[1], parsed[2] or info.wkt

    if info.wkt:
        # resolve_crs 认为形如 GEOGCS/PROJCS 开头但 pyproj 解析失败（畸形 WKT）
        return None, None, None
    return None, None, None


# ── 同步版本（脚本 / asyncio.to_thread 内使用）───────────────────────────────

def resolve_ingest_srid_sync(session, crs_str: str | None) -> SridResolution:
    """同步解析并确保注册。session 为 SQLAlchemy 同步 Session。

    流程：
        EPSG 可映射 → 直接用 EPSG SRID（无需注册）
        可解析但无 EPSG → 计算确定性自定义 SRID → 幂等注册 spatial_ref_sys
        不可解析 / 空 → 回退 4326
    """
    key = _normalize_crs_text(crs_str)
    cached = _resolution_cache.get(key)
    if cached:
        return cached

    epsg, proj4, wkt = parse_crs_definition(crs_str)

    if epsg:
        res = SridResolution(srid=epsg, is_custom=False, epsg=epsg, source="epsg")
    elif proj4:
        res = _ensure_registered_sync(session, proj4, wkt)
    else:
        if crs_str and crs_str.strip():
            logger.warning(
                "CRS 无法解析也无法映射 EPSG，回退 SRID=%d（原文前 200 字符: %.200s）",
                DEFAULT_SRID, crs_str,
            )
        res = SridResolution(
            srid=DEFAULT_SRID, is_custom=False, epsg=DEFAULT_SRID,
            source="fallback-empty" if not (crs_str or "").strip() else "fallback-unparseable",
        )

    _resolution_cache[key] = res
    return res


def _apply_false_origin_fix(
    proj4_norm: str, wkt: str | None,
) -> tuple[str, str | None, str | None]:
    """对 (proj4, wkt) 应用假东/假北互换纠正。

    返回 (纠正后 proj4, 纠正后 wkt, 告警文案|None)。
    无缺陷时原样返回，告警为 None。
    """
    proj4_fixed, quirk = _fix_swapped_false_origin(proj4_norm)
    if not quirk:
        return proj4_norm, wkt, None
    m_x = re.search(r"\+x_0=(-?[\d.]+)", proj4_norm)
    x0 = float(m_x.group(1)) if m_x else 0.0
    wkt_fixed = _fix_swapped_false_origin_wkt(wkt, x0)
    return proj4_fixed, wkt_fixed, quirk


def _ensure_registered_sync(session, proj4: str, wkt: str | None) -> SridResolution:
    """确定性选号 + 幂等注册（同步）。proj4text 冲突时换盐重试。

    - SRID 哈希基于源文件声明的原始 proj4（保证跨版本稳定）
    - 入库的 proj4text 经过假东/假北互换纠正（PostGIS 变换实际使用该字段）
    - 已有 VCS-CUSTOM 行的 proj4text 与最新纠正逻辑不一致时原地自愈更新
    """
    proj4_norm = _normalize_crs_text(proj4)
    proj4_stored, srtext_stored, quirk = _apply_false_origin_fix(proj4_norm, wkt)
    if quirk:
        logger.warning("%s", quirk)
    for salt in range(10):
        srid = _deterministic_custom_srid(proj4_norm, salt)
        row = session.execute(
            text("SELECT auth_name, proj4text, srtext FROM spatial_ref_sys WHERE srid = :srid"),
            {"srid": srid},
        ).first()
        if row is None:
            session.execute(text("""
                INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text)
                VALUES (:srid, :auth, :srid, :srtext, :proj4text)
            """), {
                "srid": srid, "auth": AUTH_NAME,
                "srtext": (srtext_stored or proj4_stored)[:8000],
                "proj4text": proj4_stored,
            })
            logger.info("已注册自定义坐标系 SRID=%d (%s)", srid, proj4_stored[:120])
            return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
        auth, stored_p4 = row[0], _normalize_crs_text(row[1] or "")
        stored_srt = _normalize_crs_text((row[2] or "")[:8000])
        expected_srt = _normalize_crs_text((srtext_stored or proj4_stored)[:8000])
        if auth == AUTH_NAME:
            if stored_p4 == proj4_stored and stored_srt == expected_srt:
                return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
            # 自愈：纠正逻辑升级后，旧注册行的 proj4text/srtext 落后于最新版本
            session.execute(text("""
                UPDATE spatial_ref_sys SET proj4text = :p4, srtext = :srt
                WHERE srid = :srid AND auth_name = :auth
            """), {"srid": srid, "auth": AUTH_NAME, "p4": proj4_stored,
                   "srt": (srtext_stored or proj4_stored)[:8000]})
            logger.info("自愈更新自定义坐标系 SRID=%d 的定义 → %s", srid, proj4_stored[:120])
            return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
        # 非 VCS 注册的外部占用（极小概率哈希碰撞）→ 换盐
        logger.warning("自定义 SRID=%d 已被其他坐标系占用(auth=%s)，换盐重试", srid, auth)
    raise RuntimeError("无法为自定义坐标系分配 SRID：连续 10 次哈希碰撞（异常情况）")


# ── 异步版本（FastAPI 请求路径使用）──────────────────────────────────────────

async def resolve_ingest_srid_async(db: AsyncSession, crs_str: str | None) -> SridResolution:
    """异步解析并确保注册。语义与 resolve_ingest_srid_sync 完全一致。"""
    key = _normalize_crs_text(crs_str)
    cached = _resolution_cache.get(key)
    if cached:
        return cached

    epsg, proj4, wkt = parse_crs_definition(crs_str)

    if epsg:
        res = SridResolution(srid=epsg, is_custom=False, epsg=epsg, source="epsg")
    elif proj4:
        res = await _ensure_registered_async(db, proj4, wkt)
    else:
        if crs_str and crs_str.strip():
            logger.warning(
                "CRS 无法解析也无法映射 EPSG，回退 SRID=%d（原文前 200 字符: %.200s）",
                DEFAULT_SRID, crs_str,
            )
        res = SridResolution(
            srid=DEFAULT_SRID, is_custom=False, epsg=DEFAULT_SRID,
            source="fallback-empty" if not (crs_str or "").strip() else "fallback-unparseable",
        )

    _resolution_cache[key] = res
    return res


async def _ensure_registered_async(db: AsyncSession, proj4: str, wkt: str | None) -> SridResolution:
    """确定性选号 + 幂等注册（异步）。语义与 _ensure_registered_sync 完全一致。"""
    proj4_norm = _normalize_crs_text(proj4)
    proj4_stored, srtext_stored, quirk = _apply_false_origin_fix(proj4_norm, wkt)
    if quirk:
        logger.warning("%s", quirk)
    for salt in range(10):
        srid = _deterministic_custom_srid(proj4_norm, salt)
        row = (await db.execute(
            text("SELECT auth_name, proj4text, srtext FROM spatial_ref_sys WHERE srid = :srid"),
            {"srid": srid},
        )).first()
        if row is None:
            await db.execute(text("""
                INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text)
                VALUES (:srid, :auth, :srid, :srtext, :proj4text)
            """), {
                "srid": srid, "auth": AUTH_NAME,
                "srtext": (srtext_stored or proj4_stored)[:8000],
                "proj4text": proj4_stored,
            })
            logger.info("已注册自定义坐标系 SRID=%d (%s)", srid, proj4_stored[:120])
            return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
        auth, stored_p4 = row[0], _normalize_crs_text(row[1] or "")
        stored_srt = _normalize_crs_text((row[2] or "")[:8000])
        expected_srt = _normalize_crs_text((srtext_stored or proj4_stored)[:8000])
        if auth == AUTH_NAME:
            if stored_p4 == proj4_stored and stored_srt == expected_srt:
                return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
            # 自愈：纠正逻辑升级后，旧注册行的 proj4text/srtext 落后于最新版本
            await db.execute(text("""
                UPDATE spatial_ref_sys SET proj4text = :p4, srtext = :srt
                WHERE srid = :srid AND auth_name = :auth
            """), {"srid": srid, "auth": AUTH_NAME, "p4": proj4_stored,
                   "srt": (srtext_stored or proj4_stored)[:8000]})
            logger.info("自愈更新自定义坐标系 SRID=%d 的定义 → %s", srid, proj4_stored[:120])
            return SridResolution(srid=srid, is_custom=True, epsg=None, source="wkt-custom")
        logger.warning("自定义 SRID=%d 已被其他坐标系占用(auth=%s)，换盐重试", srid, auth)
    raise RuntimeError("无法为自定义坐标系分配 SRID：连续 10 次哈希碰撞（异常情况）")


# ── 坐标域合理性校验（A3 入库防线）───────────────────────────────────────────

GEOGRAPHIC_DOMAIN = (-180.0, -90.0, 180.0, 90.0)


def coord_domain_warning(is_geographic: bool, extent: tuple[float, float, float, float]) -> str | None:
    """校验坐标域与坐标系类型是否矛盾。矛盾返回告警文案，否则 None。

    地理坐标系（度）下坐标应落在 ±180/±90 内；
    投影坐标系（米）下出现 ±180/±90 内的小数值属可疑但合法（局部工程坐标），
    不做反向告警以免误报。
    """
    if not is_geographic:
        return None
    xmin, ymin, xmax, ymax = extent
    gxmin, gymin, gxmax, gymax = GEOGRAPHIC_DOMAIN
    if xmax < gxmin or xmin > gxmax or ymax < gymin or ymin > gymax:
        return (
            f"坐标域校验失败：SRID 标记为地理坐标系(度)，但坐标范围 "
            f"[{xmin:.2f}, {ymin:.2f}] ~ [{xmax:.2f}, {ymax:.2f}] 超出 "
            f"[-180,-90]~[180,90]，疑似投影坐标被误标或源数据 CRS 缺失"
        )
    return None


async def get_srid_is_geographic(db_or_session, srid: int) -> bool | None:
    """查询 spatial_ref_sys 判断 SRID 是否地理坐标系（+proj=longlat）。

    返回 None 表示查不到该 SRID（未注册/不存在）。异步版本。
    """
    q = text("SELECT proj4text, srtext FROM spatial_ref_sys WHERE srid = :srid")
    row = (await db_or_session.execute(q, {"srid": srid})).first()
    return _classify_srid_row(row)


def get_srid_is_geographic_sync(session, srid: int) -> bool | None:
    """同 get_srid_is_geographic，同步 Session 版本（脚本/GDB 同步线程使用）。"""
    q = text("SELECT proj4text, srtext FROM spatial_ref_sys WHERE srid = :srid")
    row = session.execute(q, {"srid": srid}).first()
    return _classify_srid_row(row)


def _classify_srid_row(row) -> bool | None:
    """根据 spatial_ref_sys 行判定是否地理坐标系；row 为 None 返回 None。

    判定优先级：
      1. proj4text 含 +proj=longlat → 地理
      2. proj4text 含其他 +proj=xxx → 投影（tmerc/merc/laea/aea 等均为投影）
      3. 无 proj4 时按 WKT 关键字（兼容 WKT1: GEOGCS/PROJCS、WKT2: GEODCRS/PROJCRS）
      4. 全部无法匹配 → None（调用方走保守兜底）
    """
    if row is None:
        return None
    proj4text, srtext = row[0] or "", row[1] or ""
    if "+proj=longlat" in proj4text:
        return True
    if "+proj=" in proj4text:
        return False
    if any(k in srtext for k in ("PROJCRS[", "PROJCS[", "PROJECTION[")):
        return False
    if any(k in srtext for k in ("GEODCRS[", "GEOGCS[")):
        return True
    # 无法判定时不乱猜
    return None


# 三张分区表的 extent 聚合查询模板
_EXTENT_SQL = """
    SELECT ST_XMin(e.x), ST_YMin(e.x), ST_XMax(e.x), ST_YMax(e.x)
    FROM (
        SELECT ST_Extent(geometry) AS x
        FROM {tbl} WHERE stored_file_id = :fid AND geometry IS NOT NULL
    ) e WHERE e.x IS NOT NULL
"""


async def validate_loaded_extent(db: AsyncSession, stored_file_id: str, srid: int) -> str | None:
    """入库后校验该文件要素的实际坐标域与标称 SRID 是否一致（异步版）。

    对 point/line/polygon 三张分区表逐一取 ST_Extent（无要素则跳过）。
    发现矛盾仅返回告警文案（调用方决定如何呈现），不阻断流程。
    """
    is_geo = await get_srid_is_geographic(db, srid)
    extents = []
    if is_geo is not None:
        from app.models.spatial_feature import ALL_FEATURE_TABLES
        for tbl in ALL_FEATURE_TABLES:
            ext = (await db.execute(
                text(_EXTENT_SQL.format(tbl=tbl.__tablename__)), {"fid": stored_file_id}
            )).first()
            if ext:
                extents.append(ext)
    return _extent_warning(is_geo, extents)


def validate_loaded_extent_sync(session, stored_file_id: str, srid: int) -> str | None:
    """入库后校验坐标域一致性（同步版，语义同 validate_loaded_extent）。"""
    is_geo = get_srid_is_geographic_sync(session, srid)
    extents = []
    if is_geo is not None:
        from app.models.spatial_feature import ALL_FEATURE_TABLES
        for tbl in ALL_FEATURE_TABLES:
            ext = session.execute(
                text(_EXTENT_SQL.format(tbl=tbl.__tablename__)), {"fid": stored_file_id}
            ).first()
            if ext:
                extents.append(ext)
    return _extent_warning(is_geo, extents)


def _extent_warning(is_geo: bool | None, extents: list) -> str | None:
    """由各表 extent 行汇总出告警文案；无矛盾返回 None。"""
    if is_geo is None or not extents:
        return None
    xmin = min(r[0] for r in extents)
    ymin = min(r[1] for r in extents)
    xmax = max(r[2] for r in extents)
    ymax = max(r[3] for r in extents)
    return coord_domain_warning(is_geo, (xmin, ymin, xmax, ymax))
