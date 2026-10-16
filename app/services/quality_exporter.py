"""质检报告导出 — 将违规要素导出为 Excel 或 GeoJSON（含实际几何）。

v2.5 变更：
- GeoJSON 导出从 PostGIS 查询违规要素的实际几何坐标
- 支持 attribute / topology / issue 三类违规的几何导出
"""

import json
import logging
from typing import Any

from sqlalchemy import select, func, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spatial_feature import (
    PointFeature, LineFeature, PolygonFeature,
)

logger = logging.getLogger(__name__)


# ── 几何查询 ────────────────────────────────────────────────────────────────

async def _fetch_violation_geometry(
    db: AsyncSession,
    stored_file_id: str,
    feature_indices: list[int],
    api_key_id: str | None = None,
) -> dict[int, dict | None]:
    """从 PostGIS 查询指定索引的要素几何。

    Returns:
        {feature_index: GeoJSON geometry dict 或 None}
    """
    if not feature_indices:
        return {}

    result: dict[int, dict | None] = {}

    # 查询三张表
    for Model in [PointFeature, LineFeature, PolygonFeature]:
        # PostGIS ST_AsGeoJSON → 将 geometry 转为 GeoJSON 字符串
        geo_expr = func.ST_AsGeoJSON(Model.geometry)
        q = (
            select(Model.feature_index, geo_expr)
            .where(
                Model.stored_file_id == stored_file_id,
                Model.feature_index.in_(feature_indices),
            )
        )
        if api_key_id:
            q = q.where(Model.api_key_id == api_key_id)

        rows = await db.execute(q)
        for idx, geo_json_str in rows:
            if geo_json_str and idx not in result:
                try:
                    result[idx] = json.loads(geo_json_str)
                except (json.JSONDecodeError, TypeError):
                    result[idx] = None

    return result


# ── 主导出函数 ──────────────────────────────────────────────────────────────

async def export_violations_geojson(report, db: AsyncSession | None = None) -> str:
    """将质检报告的违规要素导出为 GeoJSON FeatureCollection。

    当 db 和 stored_file_id 可用时，会查询 PostGIS 获取违规要素的实际几何。

    Args:
        report: QualityReport ORM 对象
        db: 异步数据库会话（可选，用于查询几何）

    Returns:
        GeoJSON FeatureCollection 字符串
    """
    features: list[dict] = []

    # ── 收集所有违规要素索引，批量查询几何 ──
    all_indices: set[int] = set()
    topo_violations: list[dict] = []
    attr_violations: list[dict] = []

    # 拓扑违规 — 含 feature_indices
    for tv in (report.topology_violations or []):
        if isinstance(tv, dict):
            topo_violations.append(tv)
            for idx in (tv.get("feature_indices") or []):
                if isinstance(idx, int):
                    all_indices.add(idx)

    # 属性违规 — constraint_violations (v2 格式)
    for cv in (report.constraint_violations or []):
        if isinstance(cv, dict):
            attr_violations.append(cv)
            # 属性违规可能有 feature_indices（来自 samples）
            for s in (cv.get("samples") or []):
                if isinstance(s, dict) and "feature_index" in s:
                    idx = s["feature_index"]
                    if isinstance(idx, int):
                        all_indices.add(idx)

    # 旧版 issues
    for iss in (report.issues or []):
        if isinstance(iss, dict):
            for s in (iss.get("samples") or []):
                if isinstance(s, dict) and "feature_index" in s:
                    idx = s["feature_index"]
                    if isinstance(idx, int):
                        all_indices.add(idx)

    # ── 批量查询几何 ──
    geometry_map: dict[int, dict | None] = {}
    if db and report.stored_file_id and all_indices:
        try:
            geometry_map = await _fetch_violation_geometry(
                db, report.stored_file_id, sorted(all_indices), report.api_key_id,
            )
            logger.info("Fetched geometry for %d/%d violating features",
                        len(geometry_map), len(all_indices))
        except Exception as e:
            logger.warning("Failed to fetch geometry for export: %s", e)

    # ── 拓扑违规 Feature ──
    for tv in topo_violations:
        rule_name = tv.get("rule_name", tv.get("rule_code", "unknown"))
        severity = tv.get("severity", "warning")
        message = tv.get("message", tv.get("description", ""))
        indices = [i for i in (tv.get("feature_indices") or []) if isinstance(i, int)]

        # 每个违规索引生成一个 Feature
        for idx in indices:
            geom = geometry_map.get(idx)
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "violation_type": f"topology_{rule_name}",
                    "rule_code": tv.get("rule_code", ""),
                    "rule_name": rule_name,
                    "severity": severity,
                    "description": message,
                    "feature_index": idx,
                    "layer": tv.get("layer", ""),
                    "report_id": report.id,
                    "file_name": report.file_name,
                },
            })

        # 如果没有具体索引，生成一个汇总 Feature
        if not indices:
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "violation_type": f"topology_{rule_name}",
                    "rule_code": tv.get("rule_code", ""),
                    "rule_name": rule_name,
                    "severity": severity,
                    "description": message,
                    "failed_count": tv.get("failed_count", 0),
                    "report_id": report.id,
                    "file_name": report.file_name,
                },
            })

    # ── 属性违规 Feature ──
    for cv in attr_violations:
        rule_code = cv.get("rule_code", "")
        rule_name = cv.get("rule_name", cv.get("rule_code", "unknown"))
        severity = cv.get("severity", "warning")
        message = cv.get("message", "")
        field_name = cv.get("field", cv.get("field_name", ""))
        layer = cv.get("layer", "")
        failed_count = cv.get("failed_count", 0)

        # 如果有 samples 带 feature_index，逐个导出
        samples = cv.get("samples") or []
        exported_indices = set()
        for s in samples:
            if isinstance(s, dict) and "feature_index" in s:
                idx = s["feature_index"]
                if isinstance(idx, int) and idx not in exported_indices:
                    exported_indices.add(idx)
                    geom = geometry_map.get(idx)
                    features.append({
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {
                            "violation_type": f"attribute_{rule_code}",
                            "rule_code": rule_code,
                            "rule_name": rule_name,
                            "severity": severity,
                            "description": message,
                            "field": field_name,
                            "layer": layer,
                            "feature_index": idx,
                            "sample_value": s.get("value"),
                            "report_id": report.id,
                            "file_name": report.file_name,
                        },
                    })

        # 如果没有具体索引，生成汇总 Feature
        if not exported_indices:
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "violation_type": f"attribute_{rule_code}",
                    "rule_code": rule_code,
                    "rule_name": rule_name,
                    "severity": severity,
                    "description": message,
                    "field": field_name,
                    "layer": layer,
                    "failed_count": failed_count,
                    "report_id": report.id,
                    "file_name": report.file_name,
                },
            })

    # ── 旧版 issues ──
    for iss in (report.issues or []):
        if isinstance(iss, dict):
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "violation_type": iss.get("issue_type", "issue"),
                    "severity": iss.get("severity", "warning"),
                    "description": iss.get("message", ""),
                    "field": iss.get("field", ""),
                    "affected_count": iss.get("affected_count", 0),
                    "report_id": report.id,
                    "file_name": report.file_name,
                },
            })

    collection = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "report_id": report.id,
            "file_name": report.file_name,
            "detected_format": report.detected_format,
            "completeness_score": report.completeness_score,
            "total_features": len(features),
            "with_geometry": sum(1 for f in features if f.get("geometry")),
            "generated_at": str(report.created_at),
        },
    }
    return json.dumps(collection, ensure_ascii=False, indent=2)


def export_violations_excel(report, output_path: str) -> str:
    """将违规要素导出为 Excel 文件。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "违规清单"

    # 报告头部
    ws.insert_rows(1, 3)
    ws['A1'] = f"质检报告: {report.file_name}"
    ws['A2'] = (
        f"综合评分: {report.score_completeness:.0%}  "
        f"违规总数: {len(report.topology_violations or []) + len(report.constraint_violations or []) + len(report.issues or [])}"
    )
    ws['A1'].font = Font(bold=True, size=14)

    # 表头
    ws.append(["违规类型", "规则码", "规则名称", "严重级别", "描述", "字段", "图层", "失败数", "要素索引"])
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 10
    ws.column_dimensions['E'].width = 50
    ws.column_dimensions['F'].width = 16
    ws.column_dimensions['G'].width = 16
    ws.column_dimensions['H'].width = 10
    ws.column_dimensions['I'].width = 20

    row = 5

    # 拓扑违规
    for tv in (report.topology_violations or []):
        if isinstance(tv, dict):
            indices = tv.get("feature_indices", [])
            ws.append([
                f"topology_{tv.get('rule_name', '')}",
                tv.get("rule_code", ""),
                tv.get("rule_name", ""),
                tv.get("severity", ""),
                tv.get("message", tv.get("description", "")),
                "",
                tv.get("layer", ""),
                tv.get("failed_count", 0),
                ",".join(str(i) for i in indices[:20]),
            ])
            row += 1

    # 属性违规
    for cv in (report.constraint_violations or []):
        if isinstance(cv, dict):
            ws.append([
                f"attribute_{cv.get('rule_code', '')}",
                cv.get("rule_code", ""),
                cv.get("rule_name", ""),
                cv.get("severity", ""),
                cv.get("message", ""),
                cv.get("field", cv.get("field_name", "")),
                cv.get("layer", ""),
                cv.get("failed_count", 0),
                "",
            ])
            row += 1

    # 旧版 issues
    for iss in (report.issues or []):
        if isinstance(iss, dict):
            ws.append([
                iss.get("issue_type", "issue"),
                "",
                "",
                iss.get("severity", ""),
                iss.get("message", ""),
                iss.get("field", ""),
                "",
                iss.get("affected_count", 0),
                "",
            ])
            row += 1

    wb.save(output_path)
    return output_path
