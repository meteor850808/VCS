"""拓扑检查引擎 v2 — 12 项规则，混合 PostGIS SQL + shapely。

规则列表:
  T1  空几何           empty_geometry       ALL
  T2  自相交           self_intersection    Line/Polygon
  T3  极大面           max_area             Polygon
  T4  极小面           min_area             Polygon
  T5  微短线           short_line           Line
  T6  面重叠           polygon_overlap      Polygon
  T7  点重叠           point_overlap        Point
  T8  线重叠           line_overlap         Line
  T9  面裂隙           polygon_gap          Polygon
  T10 线连通性         line_connectivity    Line
  T11 不规则面         irregular_polygon    Polygon
  T12 环方向           ring_direction       Polygon
  T13 多部件几何       multipart_geometry   ALL

混合策略:
  - 简单统计类 (T1/T3/T4/T5/T12/T13): PostGIS SQL → 高效
  - 空间关系类 (T6/T7/T8/T9): PostGIS SQL + 空间索引 → 高效
  - 复杂几何分析 (T2/T10/T11): shapely 混合 → 精确
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, func, and_, or_, not_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spatial_feature import (
    PointFeature, LineFeature, PolygonFeature,
)

logger = logging.getLogger(__name__)

# ── 数据结构 ─────────────────────────────────────────────────────────────────

@dataclass
class TopologyConfig:
    """拓扑检查配置。"""
    enabled_rules: list[str] = field(default_factory=lambda: [
        "empty_geometry", "self_intersection", "max_area", "min_area",
        "short_line", "polygon_overlap", "point_overlap", "line_overlap",
        "polygon_gap", "multipart_geometry",
    ])
    tolerances: dict[str, float] = field(default_factory=lambda: {
        "min_area": 0.01,        # m²  (UI: 0.01 m²)
        "max_area": 1e10,        # m²  (UI: 10000 km² = 1e10 m²)
        "min_length": 0.001,
        "overlap_tolerance": 0.0001,
        "gap_threshold": 0.001,
        "point_distance": 0.001,
        "convexity_threshold": 0.3,
        "min_sides": 3,
    })


@dataclass
class TopologyCheckResult:
    """单条拓扑检查结果。"""
    rule_code: str
    rule_name: str
    status: str             # pass / fail / info
    severity: str           # error / warning
    layer: str = ""
    subtype: str = ""       # max_area / min_area / etc.
    failed_count: int = 0
    total_checked: int = 0
    message: str = ""
    feature_indices: list[int] = field(default_factory=list)
    pairs: list[tuple[int, int]] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


# ── 规则注册表 ───────────────────────────────────────────────────────────────

ALL_RULES = {
    "empty_geometry":      ("T01", "空几何", "error"),
    "self_intersection":   ("T02", "自相交", "error"),
    "max_area":            ("T03", "极大面", "warning"),
    "min_area":            ("T04", "极小面", "warning"),
    "short_line":          ("T05", "微短线", "warning"),
    "polygon_overlap":     ("T06", "面重叠", "error"),
    "point_overlap":       ("T07", "点重叠", "warning"),
    "line_overlap":        ("T08", "线重叠", "warning"),
    "polygon_gap":         ("T09", "面裂隙", "warning"),
    "line_connectivity":   ("T10", "线连通性", "warning"),
    "irregular_polygon":   ("T11", "不规则面", "info"),
    "ring_direction":      ("T12", "环方向", "warning"),
    "multipart_geometry":  ("T13", "多部件几何", "info"),
}

# 规则 → 适用的几何类型
RULE_GEOM_MAP = {
    "empty_geometry":      ["point", "line", "polygon"],
    "self_intersection":   ["line", "polygon"],
    "max_area":            ["polygon"],
    "min_area":            ["polygon"],
    "short_line":          ["line"],
    "polygon_overlap":     ["polygon"],
    "point_overlap":       ["point"],
    "line_overlap":        ["line"],
    "polygon_gap":         ["polygon"],
    "line_connectivity":   ["line"],
    "irregular_polygon":   ["polygon"],
    "ring_direction":      ["polygon"],
    "multipart_geometry":  ["point", "line", "polygon"],
}

MODEL_MAP = {
    "point": PointFeature,
    "line": LineFeature,
    "polygon": PolygonFeature,
}


# ── 米制几何量算策略（v2.10.5 重构；附6 增强 planar 防线）───────────────────
# 历史教训：
#   ≤v2.10.2 用 geography 球面量算 → 球面面积可为负触发 InternalServerError；
#   v2.10.3/v2.10.4 硬编码 ST_Transform(x, 4549)（CGCS2000/3度GK CM 114E）：
#     - 数据在其他经带（如上海 121.5°E）跨带失真；
#     - 自定义投影坐标（上海地方系，米值）被误标 SRID=4326 时，
#       PROJ 把米值当经纬度解释 → "transform: Invalid coordinate (2049)"
#       拖垮 T03/T04/T06/T07/T08/T09 全部量算规则。
# v2.10.5 运行期自适应（配合入库端 custom_crs 正确标记 SRID）：
#   1) 读取该文件要素的原生 SRID 及 spatial_ref_sys 定义；
#   2) 已是米制投影 → 原生量算（mode=native，零变换风险）；
#   3) 地理坐标系（度）→ 按数据 bbox 动态选 UTM 带（mode=utm，EPSG:326xx/327xx）；
#   4) 其余一律 planar 平面量算（mode=planar，零变换）：
#      - 无 CRS 的 CAD 本地平面坐标被盲标 4326（真实案例：DWG 图纸
#        X∈[2.7万], Y∈[-1900] → 单点选带产出 EPSG:32760 → 全量变换报
#        Invalid coordinate 拖垮 T03/T04/T06/T09 —— 附6 修复）；
#      - SRID=0/未注册（原 legacy(4549) 路径对之必然报 unknown SRID）；
#      - bbox 超出地理度域（|lon|>180 或 |lat|>90，全量 bbox 校验）。


@dataclass
class MetricStrategy:
    """单次拓扑检查运行的米制量算策略。

    mode 语义（v2.10.5 附6 扩展）：
      native  — 原生 SRID 已验证为米制投影，零变换直接量算；
      planar  — 坐标为平面值但无法/不宜投影变换（无 CRS 的 CAD 本地坐标、
                SRID=0/未注册、bbox 超出地理度域的"伪 4326"数据），零变换量算，
                单位 = 数据原生平面单位（CAD 图纸通常即米）；
      utm     — 地理坐标系（真度数），动态选 UTM 带变换量算；
      legacy  — 【已退役 v2.10.5附6】原硬编码 4549 兜底，因对 SRID 缺失/
                越界数据必然触发 transform 异常，全部路径改回退 planar。
    """
    mode: str              # native / planar / utm
    target_srid: int | None  # utm 模式的目标投影 SRID；其余模式无意义

    # 零变换模式集合
    _NO_TRANSFORM = ("native", "planar")

    # ── SQLAlchemy 表达式构造 ──
    def _measure(self, col, fn_name: str):
        if self.mode in self._NO_TRANSFORM:
            return getattr(func, fn_name)(col)
        return getattr(func, fn_name)(func.ST_Transform(col, self.target_srid))

    def area_m2(self, col):
        """面积（m² / 平面单位²）表达式。"""
        return self._measure(col, "ST_Area")

    def length_m(self, col):
        """长度（m / 平面单位）表达式。"""
        return self._measure(col, "ST_Length")

    def distance_m(self, a, b):
        """距离（m / 平面单位）表达式。"""
        if self.mode in self._NO_TRANSFORM:
            return func.ST_Distance(a, b)
        return func.ST_Distance(
            func.ST_Transform(a, self.target_srid),
            func.ST_Transform(b, self.target_srid),
        )

    # ── 原生 SQL 片段构造（T06/T07/T08/T09 使用）──
    def sql_metric(self, expr_sql: str, fn_name: str) -> str:
        """生成如 ST_Area(x) 或 ST_Area(ST_Transform(x, 32651)) 的 SQL 片段。

        target_srid 为服务端 int 计算值（非用户输入），内联安全。
        """
        if self.mode in self._NO_TRANSFORM:
            return f"{fn_name}({expr_sql})"
        return f"{fn_name}(ST_Transform({expr_sql}, {int(self.target_srid)}))"


# 历史兜底 SRID（v2.10.5附6 起退役：所有原 legacy 路径改为 planar 平面量算，
# 因对 SRID 缺失/坐标越界数据 ST_Transform 必然抛异常。常量保留仅供追溯。）
LEGACY_METRIC_SRID = 4549


async def resolve_metric_strategy(
    db: AsyncSession,
    check_models: list[str],
    stored_file_id: str,
) -> MetricStrategy:
    """按数据实际坐标系确定本次检查的量算策略。"""
    from app.services.custom_crs import get_srid_is_geographic

    # 1. 任一要素表的原生 SRID
    native_srid: int | None = None
    for gtype in check_models:
        model = MODEL_MAP[gtype]
        q = select(func.ST_SRID(model.geometry)).where(
            model.stored_file_id == stored_file_id,
            model.geometry.isnot(None),
        ).limit(1)
        srid = (await db.execute(q)).scalar()
        if srid:
            native_srid = int(srid)
            break
    if native_srid is None:
        # 【v2.10.5附6】无 SRID（0/NULL）→ 无法投影变换，回退平面量算。
        # 原 legacy(4549) 路径对 SRID=0 数据必然报 "unknown (0) SRID"。
        logger.info("文件 %s 要素无有效 SRID，量算走平面模式（零变换）", stored_file_id[:12])
        return MetricStrategy("planar", None)

    is_geo = await get_srid_is_geographic(db, native_srid)

    # 2. 投影坐标系：确认米制后原生量算
    if is_geo is False:
        row = (await db.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid = :s"),
            {"s": native_srid},
        )).first()
        p4 = (row[0] or "") if row else ""
        if "+units=m" in p4 or "+to_meter=1" in p4:
            return MetricStrategy("native", native_srid)
        # 投影但非米制（英尺等）→ 转 UTM 量算
        return await _utm_strategy_for_data(db, check_models, stored_file_id, native_srid)

    # 3. 地理坐标系（度）→ 动态 UTM（内部含坐标域合理性防线）
    if is_geo is True:
        return await _utm_strategy_for_data(db, check_models, stored_file_id, native_srid)

    # 4. SRID 未注册/无法判定 → 投影变换不可行，回退平面量算
    # 【v2.10.5附6】原 legacy(4549) 对未注册源 SRID 同样触发 transform 异常
    logger.warning(
        "文件 %s 的 SRID=%s 在 spatial_ref_sys 中无法判定类型，量算走平面模式（零变换）",
        stored_file_id, native_srid,
    )
    return MetricStrategy("planar", None)


async def _utm_strategy_for_data(
    db: AsyncSession,
    check_models: list[str],
    stored_file_id: str,
    native_srid: int | None = None,
) -> MetricStrategy:
    """地理坐标系数据的动态 UTM 选带；含坐标域合理性防线。

    【v2.10.5附6 防线】无 CRS 的 CAD 本地平面坐标常被入库盲标 SRID=4326
    （米值当经纬度，如 X∈[2.7万], Y∈[-1900]）。此前用单要素质心选带会产出
    荒谬 UTM 带（如 EPSG:32760），随后全量 ST_Transform 报
    "Invalid coordinate (2049)" 拖垮 T03/T04/T06/T09。

    现改为：
      1) 取全量数据 bbox（ST_Extent + ST_SetSRID 补回丢失的 SRID）变换到 4326，
         任一角点超出度域（|lon|>180 或 |lat|>90）→ 判定非地理坐标 → planar；
      2) 合法时按 bbox 中心选带。
    """
    for gtype in check_models:
        model = MODEL_MAP[gtype]
        # 全量 bbox 角点（单点质心取样无法发现"部分要素越界"）
        q = text(f"""
            SELECT ST_XMin(lonlat_env) AS min_lon, ST_XMax(lonlat_env) AS max_lon,
                   ST_YMin(lonlat_env) AS min_lat, ST_YMax(lonlat_env) AS max_lat
            FROM (
                SELECT ST_Transform(
                    ST_SetSRID(ST_Extent(geometry), :srid), 4326
                ) AS lonlat_env
                FROM {model.__tablename__}
                WHERE stored_file_id = :fid AND geometry IS NOT NULL
            ) t
        """)
        row = (await db.execute(q, {"fid": stored_file_id, "srid": native_srid or 0})).first()
        if row and row[0] is not None:
            min_lon, max_lon = float(row[0]), float(row[1])
            min_lat, max_lat = float(row[2]), float(row[3])
            # 度域合理性防线：任一角越界 → 数据不是真经纬度 → 平面量算
            if (abs(min_lon) > 180 or abs(max_lon) > 180
                    or abs(min_lat) > 90 or abs(max_lat) > 90):
                logger.warning(
                    "文件 %s 的 bbox (%.1f,%.1f)-(%.1f,%.1f) 超出地理度域，"
                    "判定为平面坐标（疑似无 CRS 的 CAD 本地坐标系），量算走平面模式",
                    stored_file_id[:12], min_lon, min_lat, max_lon, max_lat,
                )
                return MetricStrategy("planar", None)
            lon = (min_lon + max_lon) / 2.0
            lat = (min_lat + max_lat) / 2.0
            zone = min(60, max(1, int((lon + 180.0) // 6) + 1))
            epsg = (32600 if lat >= 0 else 32700) + zone
            logger.info(
                "文件 %s bbox 中心 (%.4f, %.4f) → UTM %d%s → EPSG:%d",
                stored_file_id[:12], lon, lat, zone, "N" if lat >= 0 else "S", epsg,
            )
            return MetricStrategy("utm", epsg)
    # 无可采样要素 → 无法判定投影目标，平面量算兜底
    return MetricStrategy("planar", None)


# ── 主入口 ───────────────────────────────────────────────────────────────────

async def run_topology_checks(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str = "",  # 保留签名兼容，查询不再使用
    geometry_type: str | None = None,  # Point/LineString/Polygon 或 None=全部
    config: TopologyConfig | None = None,
    layer_names: list[str] | None = None,  # 图层圈定（v2.10.5）；None/空=全部图层
) -> tuple[list[TopologyCheckResult], dict]:
    """
    执行拓扑检查。混合 PostGIS SQL + shapely。
    注：API 层已通过 scope_clause 验证文件权限，要素查询仅用 stored_file_id。

    layer_names: 质检图层圈定。给定后所有规则（含空间关系类 T6/T7/T8/T9）
                 仅在所选图层的要素集合内两两计算，不跨圈外要素。
                 计数（total_checked）同步按圈定集合统计。

    Returns:
        (results, summary)
    """
    if config is None:
        config = TopologyConfig()

    # 图层圈定规范化：去空白、去重、排序（空列表视为不限）
    layer_filter: list[str] | None = None
    if layer_names:
        cleaned = sorted({str(n).strip() for n in layer_names if n and str(n).strip()})
        layer_filter = cleaned or None

    def _lc(model):
        """ORM 查询的图层圈定条件构造器。"""
        return model.layer_name.in_(layer_filter) if layer_filter else None

    # 原生 SQL 的圈定子句与参数（psycopg2 将 Python list 适配为 PG 数组）
    _sql_clause = "\n              AND layer_name = ANY(:layers)" if layer_filter else ""
    _sql_clause_b = ("\n                  AND b.layer_name = ANY(:layers)") if layer_filter else ""
    _sql_params_extra: dict = {"layers": layer_filter} if layer_filter else {}

    tol = config.tolerances
    enabled = set(config.enabled_rules)

    # 确定要检查的几何类型
    geom_lower = geometry_type.lower() if geometry_type else None
    check_models: list[str] = []
    if geom_lower in ("point", None):
        check_models.append("point")
    if geom_lower in ("linestring", "line", None):
        check_models.append("line")
    if geom_lower in ("polygon", None):
        check_models.append("polygon")

    # 过滤适用规则
    active_rules = set()
    for rule_name in enabled:
        applicable = RULE_GEOM_MAP.get(rule_name, [])
        if any(g in applicable for g in check_models):
            active_rules.add(rule_name)

    results: list[TopologyCheckResult] = []

    # 获取每种几何的要素数量（按图层圈定集合统计）
    counts = {}
    for gtype in check_models:
        model = MODEL_MAP[gtype]
        q = select(func.count()).select_from(model).where(
            model.stored_file_id == stored_file_id,
        )
        if layer_filter:
            q = q.where(_lc(model))
        counts[gtype] = (await db.execute(q)).scalar() or 0

    # v2.10.5：按数据实际坐标系确定米制量算策略（原生米制 / 平面零变换 / 动态UTM）
    strategy = await resolve_metric_strategy(db, check_models, stored_file_id)
    logger.info("拓扑量算策略: mode=%s target_srid=%s (file=%s)",
                strategy.mode, strategy.target_srid, stored_file_id[:12])

    # ── T1: 空几何 ────────────────────────────────────────────────────
    if "empty_geometry" in active_rules:
        total_empty = 0
        for gtype in check_models:
            model = MODEL_MAP[gtype]
            q = select(func.count()).select_from(model).where(
                model.stored_file_id == stored_file_id,
                or_(model.geometry.is_(None), func.ST_IsEmpty(model.geometry)),
            )
            if layer_filter:
                q = q.where(_lc(model))
            empty_count = (await db.execute(q)).scalar() or 0
            total_empty += empty_count
        results.append(_make_result(
            "empty_geometry", status="fail" if total_empty > 0 else "pass",
            failed_count=total_empty, message=f"发现 {total_empty} 条空几何" if total_empty else "无空几何",
        ))

    # ── T2: 自相交 (shapely 混合) ────────────────────────────────────
    if "self_intersection" in active_rules:
        for gtype in ["line", "polygon"]:
            if gtype not in check_models:
                continue
            model = MODEL_MAP[gtype]
            # 先用 PostGIS 快速筛出 invalid
            q = select(model.feature_index, func.ST_AsText(model.geometry).label("wkt")).where(
                model.stored_file_id == stored_file_id,
                model.geometry.isnot(None),
                not_(func.ST_IsValid(model.geometry)),
            )
            if layer_filter:
                q = q.where(_lc(model))
            invalids = (await db.execute(q)).all()
            if invalids:
                indices = [r.feature_index for r in invalids[:500]]
                results.append(_make_result(
                    "self_intersection", status="fail",
                    failed_count=len(invalids), total_checked=counts.get(gtype, 0),
                    message=f"发现 {len(invalids)} 条自相交/{'无效线' if gtype == 'line' else '无效面'}几何",
                    feature_indices=indices,
                ))
            else:
                results.append(_make_result(
                    "self_intersection", status="pass",
                    total_checked=counts.get(gtype, 0),
                    message=f"{'线' if gtype == 'line' else '面'}几何自相交检查通过",
                ))

    # ── T3: 极大面 ──────────────────────────────────────────────────
    if "max_area" in active_rules and "polygon" in check_models:
        try:
            # max_a 单位 m²（前端 km² 输入已 ×1e6 转换）
            max_a = tol.get("max_area", 1e10)
            q = select(PolygonFeature.feature_index, strategy.area_m2(PolygonFeature.geometry).label("area")).where(
                and_(
                    PolygonFeature.stored_file_id == stored_file_id,
                    PolygonFeature.geometry.isnot(None),
                    strategy.area_m2(PolygonFeature.geometry) > max_a,
                )
            ).order_by(text("area DESC"))
            if layer_filter:
                q = q.where(_lc(PolygonFeature))
            rows = (await db.execute(q)).all()
            if rows:
                results.append(_make_result(
                    "max_area", status="fail",
                    failed_count=len(rows),
                    total_checked=counts.get("polygon", 0),
                    message=f"发现 {len(rows)} 条极大面 (面积 > {max_a/1e6} km²)",
                    feature_indices=[r.feature_index for r in rows[:500]],
                ))
            else:
                results.append(_make_result(
                    "max_area", status="pass",
                    total_checked=counts.get("polygon", 0),
                    message=f"无极大面 (阈值: {max_a/1e6} km²)",
                ))
        except Exception as e:  # 单条规则异常不应拖垮整次检查
            logger.warning("T03 极大面 执行异常: %s", e)
            await db.rollback()
            results.append(_make_result("max_area", status="error",
                                        message=f"规则执行异常: {e}"))

    # ── T4: 极小面 ──────────────────────────────────────────────────
    if "min_area" in active_rules and "polygon" in check_models:
        try:
            # min_a 单位 m²
            min_a = tol.get("min_area", 0.01)
            q = select(PolygonFeature.feature_index, strategy.area_m2(PolygonFeature.geometry).label("area")).where(
                and_(
                    PolygonFeature.stored_file_id == stored_file_id,
                    PolygonFeature.geometry.isnot(None),
                    strategy.area_m2(PolygonFeature.geometry) > 0,
                    strategy.area_m2(PolygonFeature.geometry) < min_a,
                )
            ).order_by(text("area ASC"))
            if layer_filter:
                q = q.where(_lc(PolygonFeature))
            rows = (await db.execute(q)).all()
            if rows:
                results.append(_make_result(
                    "min_area", status="fail",
                    failed_count=len(rows),
                    total_checked=counts.get("polygon", 0),
                    message=f"发现 {len(rows)} 条极小面 (面积 < {min_a} m²)",
                    feature_indices=[r.feature_index for r in rows[:500]],
                ))
            else:
                results.append(_make_result(
                    "min_area", status="pass",
                    total_checked=counts.get("polygon", 0),
                    message=f"无极小面 (阈值: {min_a} m²)",
                ))
        except Exception as e:
            logger.warning("T04 极小面 执行异常: %s", e)
            await db.rollback()
            results.append(_make_result("min_area", status="error",
                                        message=f"规则执行异常: {e}"))

    # ── T5: 微短线 ──────────────────────────────────────────────────
    if "short_line" in active_rules and "line" in check_models:
        min_len = tol.get("min_length", 0.001)
        # v2.10.5：改用量算策略（此前用原生 ST_Length，地理坐标系下是"度"
        # 却与米制阈值比较，0.001 度≈111m 导致漏报/误报）
        len_expr = strategy.length_m(LineFeature.geometry)
        q = select(LineFeature.feature_index, len_expr.label("len")).where(
            and_(
                LineFeature.stored_file_id == stored_file_id,
                LineFeature.geometry.isnot(None),
                len_expr < min_len,
                len_expr > 0,
            )
        ).order_by(text("len ASC"))
        if layer_filter:
            q = q.where(_lc(LineFeature))
        rows = (await db.execute(q)).all()
        if rows:
            results.append(_make_result(
                "short_line", status="fail",
                failed_count=len(rows),
                total_checked=counts.get("line", 0),
                message=f"发现 {len(rows)} 条微短线 (长度 < {min_len})",
                feature_indices=[r.feature_index for r in rows[:500]],
            ))
        else:
            results.append(_make_result(
                "short_line", status="pass",
                total_checked=counts.get("line", 0),
                message=f"无微短线 (阈值: {min_len})",
            ))

    # ── T6: 面重叠 ──────────────────────────────────────────────────
    if "polygon_overlap" in active_rules and "polygon" in check_models:
        try:
            overlap_tol = tol.get("overlap_tolerance", 0.0001)
            overlap_area_expr = strategy.sql_metric(
                "ST_Intersection(a.geometry, b.geometry)", "ST_Area")
            q = text(f"""
                SELECT a.feature_index AS idx_a,
                       b.feature_index AS idx_b,
                       {overlap_area_expr} AS overlap_area
                FROM polygon_features a
                JOIN polygon_features b ON a.id < b.id
                WHERE a.stored_file_id = :sid
                  AND b.stored_file_id = :sid{_sql_clause}{_sql_clause_b}
                  AND ST_Overlaps(a.geometry, b.geometry)
                  AND {overlap_area_expr} > :tol
                LIMIT 1000
            """)
            _params = {"sid": stored_file_id, "tol": overlap_tol, **_sql_params_extra}
            rows = (await db.execute(q, _params)).all()
            if rows:
                pairs = [(r.idx_a, r.idx_b) for r in rows[:500]]
                all_indices = list({i for p in pairs for i in p})[:500]
                results.append(_make_result(
                    "polygon_overlap", status="fail",
                    failed_count=len(rows),
                    total_checked=counts.get("polygon", 0),
                    message=f"发现 {len(rows)} 对面重叠 (重叠面积 > {overlap_tol})",
                    feature_indices=all_indices,
                    pairs=pairs[:200],
                ))
            else:
                results.append(_make_result(
                    "polygon_overlap", status="pass",
                    total_checked=counts.get("polygon", 0),
                    message=f"无面重叠 (容差: {overlap_tol})",
                ))
        except Exception as e:
            logger.warning("T06 面重叠 执行异常: %s", e)
            await db.rollback()
            results.append(_make_result("polygon_overlap", status="error",
                                        message=f"规则执行异常: {e}"))

    # ── T7: 点重叠 ──────────────────────────────────────────────────
    if "point_overlap" in active_rules and "point" in check_models:
        try:
            pt_dist = tol.get("point_distance", 0.001)
            dist_expr = (
                "ST_Distance(a.geometry, b.geometry)"
                if strategy.mode in ("native", "planar")
                else f"ST_Distance(ST_Transform(a.geometry,{int(strategy.target_srid)}), "
                     f"ST_Transform(b.geometry,{int(strategy.target_srid)}))"
            )
            q = text(f"""
                SELECT a.feature_index AS idx_a,
                       b.feature_index AS idx_b,
                       {dist_expr} AS dist
                FROM point_features a
                JOIN point_features b ON a.id < b.id
                WHERE a.stored_file_id = :sid
                  AND b.stored_file_id = :sid{_sql_clause}{_sql_clause_b}
                  AND a.geometry IS NOT NULL AND b.geometry IS NOT NULL
                  AND {dist_expr} < :dist
                  AND {dist_expr} > 0
                LIMIT 1000
            """)
            _params = {"sid": stored_file_id, "dist": pt_dist, **_sql_params_extra}
            rows = (await db.execute(q, _params)).all()
            if rows:
                pairs = [(r.idx_a, r.idx_b) for r in rows[:500]]
                all_indices = list({i for p in pairs for i in p})[:500]
                results.append(_make_result(
                    "point_overlap", status="fail",
                    failed_count=len(rows),
                    total_checked=counts.get("point", 0),
                    message=f"发现 {len(rows)} 对点重叠/过近 (距离 < {pt_dist})",
                    feature_indices=all_indices,
                    pairs=pairs[:200],
                ))
            else:
                results.append(_make_result(
                    "point_overlap", status="pass",
                    total_checked=counts.get("point", 0),
                    message=f"无点重叠 (距离阈值: {pt_dist})",
                ))
        except Exception as e:
            logger.warning("T07 点重叠 执行异常: %s", e)
            await db.rollback()
            results.append(_make_result("point_overlap", status="error",
                                        message=f"规则执行异常: {e}"))

    # ── T8: 线重叠 ──────────────────────────────────────────────────
    if "line_overlap" in active_rules and "line" in check_models:
        try:
            line_tol = tol.get("overlap_tolerance", 0.0001)
            overlap_len_expr = strategy.sql_metric(
                "ST_Intersection(a.geometry, b.geometry)", "ST_Length")
            q = text(f"""
                SELECT a.feature_index AS idx_a,
                       b.feature_index AS idx_b,
                       {overlap_len_expr} AS overlap_len
                FROM line_features a
                JOIN line_features b ON a.id < b.id
                WHERE a.stored_file_id = :sid
                  AND b.stored_file_id = :sid{_sql_clause}{_sql_clause_b}
                  AND ST_Overlaps(a.geometry, b.geometry)
                  AND {overlap_len_expr} > :tol
                LIMIT 1000
            """)
            _params = {"sid": stored_file_id, "tol": line_tol, **_sql_params_extra}
            rows = (await db.execute(q, _params)).all()
            if rows:
                pairs = [(r.idx_a, r.idx_b) for r in rows[:500]]
                all_indices = list({i for p in pairs for i in p})[:500]
                results.append(_make_result(
                    "line_overlap", status="fail",
                    failed_count=len(rows),
                    total_checked=counts.get("line", 0),
                    message=f"发现 {len(rows)} 对线重叠 (重叠长度 > {line_tol})",
                    feature_indices=all_indices,
                    pairs=pairs[:200],
                ))
            else:
                results.append(_make_result(
                    "line_overlap", status="pass",
                    total_checked=counts.get("line", 0),
                    message=f"无线重叠 (容差: {line_tol})",
                ))
        except Exception as e:
            logger.warning("T08 线重叠 执行异常: %s", e)
            await db.rollback()
            results.append(_make_result("line_overlap", status="error",
                                        message=f"规则执行异常: {e}"))

    # ── T9: 面裂隙 ──────────────────────────────────────────────────
    if "polygon_gap" in active_rules and "polygon" in check_models:
        gap_tol = tol.get("gap_threshold", 0.001)
        # 使用 PostGIS: unary_union 后求 envelope 差集，找小面积碎片
        gap_area_expr = strategy.sql_metric("gap_geom", "ST_Area")
        q = text(f"""
            WITH unioned AS (
                SELECT ST_UNION(geometry) AS geom
                FROM polygon_features
                WHERE stored_file_id = :sid
                  AND geometry IS NOT NULL{_sql_clause}
            ),
            gaps AS (
                SELECT ST_Difference(
                    (SELECT ST_Envelope(geom) FROM unioned),
                    (SELECT geom FROM unioned)
                ) AS gap_geom
            )
            SELECT {gap_area_expr} AS gap_area
            FROM gaps
            WHERE gap_geom IS NOT NULL AND NOT ST_IsEmpty(gap_geom)
              AND {gap_area_expr} > 0 AND {gap_area_expr} < :threshold
        """)
        try:
            _params = {"sid": stored_file_id, "threshold": gap_tol, **_sql_params_extra}
            rows = (await db.execute(q, _params)).all()
            gap_count = len(rows)
            if gap_count > 0:
                results.append(_make_result(
                    "polygon_gap", status="fail",
                    failed_count=gap_count,
                    total_checked=counts.get("polygon", 0),
                    message=f"发现 {gap_count} 处面裂隙 (面积 < {gap_tol})",
                ))
            else:
                results.append(_make_result(
                    "polygon_gap", status="pass",
                    total_checked=counts.get("polygon", 0),
                    message=f"无面裂隙 (阈值: {gap_tol})",
                ))
        except Exception as e:
            logger.warning("polygon_gap check failed: %s", e)
            await db.rollback()  # 必须回滚，否则事务中止会拖垮后续规则 → 整次检查 500
            results.append(_make_result(
                "polygon_gap", status="error",
                message=f"面裂隙检查跳过 (SQL异常: {e})",
            ))

    # ── T10: 线连通性 (shapely 混合) ─────────────────────────────────
    if "line_connectivity" in active_rules and "line" in check_models:
        q = select(
            LineFeature.feature_index,
            func.ST_StartPoint(LineFeature.geometry).label("start_pt"),
            func.ST_EndPoint(LineFeature.geometry).label("end_pt"),
        ).where(
            LineFeature.stored_file_id == stored_file_id,
            LineFeature.geometry.isnot(None),
        )
        if layer_filter:
            q = q.where(_lc(LineFeature))
        rows = (await db.execute(q)).all()
        if len(rows) > 1:
            # 收集所有端点
            start_ends = {}
            for r in rows:
                s = str(r.start_pt) if r.start_pt else None
                e = str(r.end_pt) if r.end_pt else None
                if s:
                    start_ends.setdefault(s, []).append(r.feature_index)
                if e:
                    start_ends.setdefault(e, []).append(r.feature_index)
            # 找只出现一次的端点（悬挂点）
            dangling = {pt: indices for pt, indices in start_ends.items() if len(indices) == 1}
            dangling_count = len(dangling)
            if dangling_count > 2:  # 正常线集可以有2个端点（一条线的首尾）
                results.append(_make_result(
                    "line_connectivity", status="fail",
                    failed_count=dangling_count,
                    total_checked=len(rows),
                    message=f"发现 {dangling_count} 个悬挂端点（线连通性问题）",
                ))
            else:
                results.append(_make_result(
                    "line_connectivity", status="pass",
                    total_checked=len(rows),
                    message=f"线连通性检查通过 ({dangling_count} 个端点)",
                ))
        else:
            results.append(_make_result(
                "line_connectivity", status="pass",
                total_checked=len(rows) if rows else 0,
                message="线连通性检查通过（要素不足）",
            ))

    # ── T11: 不规则面 (shapely) ──────────────────────────────────────
    if "irregular_polygon" in active_rules and "polygon" in check_models:
        from shapely import wkt as shp_wkt
        from shapely.validation import explain_validity

        min_sides = int(tol.get("min_sides", 3))
        conv_thresh = tol.get("convexity_threshold", 0.3)

        q = select(
            PolygonFeature.feature_index,
            func.ST_AsText(PolygonFeature.geometry).label("wkt"),
            func.ST_Area(PolygonFeature.geometry).label("area"),
        ).where(
            PolygonFeature.stored_file_id == stored_file_id,
            PolygonFeature.geometry.isnot(None),
        )
        if layer_filter:
            q = q.where(_lc(PolygonFeature))
        rows = (await db.execute(q)).all()
        irregular_count = 0
        irregular_indices = []
        for r in rows[:5000]:  # 限制分析量
            try:
                geom = shp_wkt.loads(r.wkt)
                if geom.is_empty:
                    continue
                # 检查边数
                if hasattr(geom, "exterior"):
                    n_sides = len(geom.exterior.coords) - 1
                    if n_sides < min_sides:
                        irregular_count += 1
                        irregular_indices.append(r.feature_index)
                        continue
                    # 检查凸度
                    area = geom.area
                    hull_area = geom.convex_hull.area
                    if hull_area > 0:
                        convexity = area / hull_area
                        if convexity < conv_thresh:
                            irregular_count += 1
                            irregular_indices.append(r.feature_index)
            except Exception as e:
                logger.debug("irregularity check failed for feature %s: %s", r.feature_index, e)
                continue

        if irregular_count > 0:
            results.append(_make_result(
                "irregular_polygon", status="fail",
                failed_count=irregular_count,
                total_checked=min(len(rows), 5000),
                message=f"发现 {irregular_count} 个不规则面 (边数<{min_sides} 或凸度<{conv_thresh})",
                feature_indices=irregular_indices[:500],
            ))
        else:
            results.append(_make_result(
                "irregular_polygon", status="pass",
                total_checked=min(len(rows), 5000),
                message=f"无不规则面 (最少边数: {min_sides}, 凸度阈值: {conv_thresh})",
            ))

    # ── T12: 环方向 ──────────────────────────────────────────────────
    if "ring_direction" in active_rules and "polygon" in check_models:
        # PostGIS: 外环应该是逆时针 (CCW)。用 ST_IsValidReason 检测
        # 实际上，PostGIS 通常不强制环方向，用 ST_ExteriorRing + ST_IsClosed 检查
        # 更简单的方式：检查 ST_IsValid + 面积符号（负面积 = 顺时针）
        q = text(f"""
            SELECT feature_index,
                   CASE WHEN ST_Area(geometry) < 0 THEN 'CW' ELSE 'CCW' END AS winding
            FROM polygon_features
            WHERE stored_file_id = :sid
              AND geometry IS NOT NULL
              AND NOT ST_IsEmpty(geometry){_sql_clause}
        """)
        _params = {"sid": stored_file_id, **_sql_params_extra}
        rows = (await db.execute(q, _params)).all()
        cw_count = sum(1 for r in rows if r.winding == "CW")
        if cw_count > 0:
            results.append(_make_result(
                "ring_direction", status="fail",
                failed_count=cw_count,
                total_checked=len(rows),
                message=f"发现 {cw_count} 个顺时针环（应为逆时针）",
            ))
        else:
            results.append(_make_result(
                "ring_direction", status="pass",
                total_checked=len(rows),
                message="所有环方向正确（逆时针）",
            ))

    # ── T13: 多部件几何 ──────────────────────────────────────────────
    if "multipart_geometry" in active_rules:
        for gtype in check_models:
            model = MODEL_MAP[gtype]
            q = select(func.count()).select_from(model).where(
                model.stored_file_id == stored_file_id,
                model.geometry.isnot(None),
                func.ST_NumGeometries(model.geometry) > 1,
            )
            if layer_filter:
                q = q.where(_lc(model))
            multi_count = (await db.execute(q)).scalar() or 0
            label = {"point": "点", "line": "线", "polygon": "面"}[gtype]
            if multi_count > 0:
                results.append(_make_result(
                    "multipart_geometry", status="fail",
                    failed_count=multi_count,
                    total_checked=counts.get(gtype, 0),
                    message=f"发现 {multi_count} 个{label}多部件几何",
                ))
            else:
                results.append(_make_result(
                    "multipart_geometry", status="pass",
                    total_checked=counts.get(gtype, 0),
                    message=f"无{label}多部件几何",
                ))

    # ── 汇总 ──────────────────────────────────────────────────────────
    summary = _summarize(results)
    return results, summary


def _make_result(
    rule_code: str,
    status: str = "pass",
    failed_count: int = 0,
    total_checked: int = 0,
    message: str = "",
    feature_indices: list[int] | None = None,
    pairs: list[tuple[int, int]] | None = None,
    **kwargs,
) -> TopologyCheckResult:
    """快捷构造 TopologyCheckResult。"""
    code, name, severity = ALL_RULES.get(rule_code, ("T??", rule_code, "warning"))
    return TopologyCheckResult(
        rule_code=code,
        rule_name=name,
        status=status,
        severity=severity if status == "fail" else "info",
        failed_count=failed_count,
        total_checked=total_checked,
        message=message,
        feature_indices=feature_indices or [],
        pairs=pairs or [],
        detail=kwargs,
    )


def _summarize(results: list[TopologyCheckResult]) -> dict:
    """汇总拓扑检查结果。"""
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    by_severity = {"error": 0, "warning": 0, "info": 0}
    for r in results:
        if r.status == "fail":
            by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_severity": by_severity,
    }
