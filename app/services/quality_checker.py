"""数据质量检查编排器 v2 — 串联 Phase 0→1→2→3。

Phase 0: 标准加载 + 数据加载
Phase 1: 属性结构检查 (attribute_checker)
Phase 2: 拓扑检查 (topology_checker)
Phase 3: 五维评分 + 报告生成
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict

from app.services.quality.models import QualityResult, QualityIssue  # 保留兼容
from app.services.quality.reporter import generate_html_report, generate_json_report  # noqa: F401
from app.services.quality.attribute_checker import (
    CheckResult as AttrCheckResult,
    run_attribute_checks,
)
from app.services.topology_checker import (
    TopologyConfig,
    TopologyCheckResult,
    run_topology_checks,
)
from app.config import settings
from app.core.constants import CRITICAL_FIELDS

logger = logging.getLogger(__name__)


# ── 五维评分权重（唯一权威来源）──────────────────────────────────────────────
# v2.20 约定：权重只在本常量定义。质量.py/前端禁硬编码（综合分由后端按此计算
# 后返回 score_overall；HTML 报告经 api/v1/quality._overall_from_scores 引用）。

WEIGHTS = {
    "completeness": 0.25,  # 必填项完整率
    "accuracy": 0.25,      # 值域/类型正确率
    "consistency": 0.20,   # 属性结构一致性
    "uniqueness": 0.15,    # 主键唯一性
    "topology": 0.15,      # 拓扑合规率
}

# ── v3 口径：规则 → 维度归集（键集必须与 WEIGHTS 一致）──────────────────────
# 维度分 = 1 - Σfailed/Σtotal_checked（要素级），不是条目通过率。
DIMENSION_RULES: dict[str, frozenset[str]] = {
    "completeness": frozenset({"31010004"}),
    "accuracy": frozenset({"31010005"}),
    "consistency": frozenset({"31010001", "31010002", "31010003"}),
    "uniqueness": frozenset({"31010006"}),
    "topology": frozenset({"T01", "T02", "T03", "T04", "T05", "T11", "T12", "T13"}),
}

# 照常执行、只作诊断的规则：不进任何维度分，但会进 violation 清单
DIAGNOSTIC_RULES: frozenset[str] = frozenset({
    "31010007", "31010008", "31010009",   # A7 必要图层不为空 / A8 字段长度 / A9 精度
    "T06", "T07", "T08", "T09", "T10",    # 面重叠 / 点重叠 / 面裂隙 / 面合并 / 线连通性
})


# ── 判定（阈值唯一来源：app.config.settings）─────────────────────────────────

def derive_verdict(
    overall: float | None,
    evaluation_errors: list[dict] | None = None,
) -> str:
    """由综合分派生判定。阈值来自 settings，不在本函数内写字面量。

    Args:
        overall: 综合分（0..1）。None 表示本轮没有任何维度被评价。
        evaluation_errors: 规则执行异常清单；非空即视为结果不可信。

    Returns:
        "pass" | "warn" | "fail" | "not_evaluated"
    """
    if overall is None or evaluation_errors:
        return "not_evaluated"
    if overall >= settings.scoring_pass_threshold:
        return "pass"
    if overall >= settings.scoring_warn_threshold:
        return "warn"
    return "fail"


# ── 评分计算 ─────────────────────────────────────────────────────────────────

def compute_scores_v2(
    attr_results: list[AttrCheckResult],
    topo_results: list[TopologyCheckResult],
) -> dict[str, float]:
    """v2 口径：条目通过率（历史实现，原样保留供回滚与对照）。

    Returns:
        {completeness, accuracy, consistency, uniqueness, topology, overall}
    """
    def _score_by_codes(codes: list[str], results: list[AttrCheckResult]) -> float:
        """计算某组规则代码的通过率。"""
        relevant = [r for r in results if r.rule_code in codes]
        if not relevant:
            return 1.0
        passed = sum(1 for r in relevant if r.status == "pass")
        return round(passed / len(relevant), 4)

    # A4: 必填项
    completeness = _score_by_codes(["31010004"], attr_results)
    # A5: 值域
    accuracy = _score_by_codes(["31010005"], attr_results)
    # A3: 结构一致性
    consistency = _score_by_codes(["31010001", "31010002", "31010003"], attr_results)
    # A6: 唯一性
    uniqueness = _score_by_codes(["31010006"], attr_results)
    # 拓扑
    topo_failed = sum(1 for r in topo_results if r.status == "fail")
    topo_total = len(topo_results)
    topology = round(1.0 - (topo_failed / topo_total), 4) if topo_total > 0 else 1.0

    overall = round(
        completeness * WEIGHTS["completeness"]
        + accuracy * WEIGHTS["accuracy"]
        + consistency * WEIGHTS["consistency"]
        + uniqueness * WEIGHTS["uniqueness"]
        + topology * WEIGHTS["topology"],
        4,
    )

    return {
        "completeness": completeness,
        "accuracy": accuracy,
        "consistency": consistency,
        "uniqueness": uniqueness,
        "topology": topology,
        "overall": overall,
    }


def compute_scores_v3(
    attr_results: list[AttrCheckResult],
    topo_results: list[TopologyCheckResult],
) -> dict:
    """v3 口径：要素级派生比率（唯一源），条目通过率降为诊断字段。

    每维取该维规则集合内 `status in ("pass", "fail")` 且 `total_checked > 0`
    的结果，按 `1 - Σfailed/Σtotal_checked` 求值；无有效分母即「未评价」
    （None），不按 0 分计入。综合分只在已评价维度上重新归一化权重。

    Returns:
        {5 维: float | None, "overall": float | None, "evaluated": dict[str, bool],
         "rule_pass_rate": float, "evaluation_errors": list[dict], "profile": "v3"}
    """
    results = list(attr_results) + list(topo_results)

    # 规则执行异常（status="error"）→ 该报告整体不可信，判定降为 not_evaluated
    evaluation_errors = [
        {"rule_code": r.rule_code, "rule_name": r.rule_name, "message": r.message}
        for r in results if r.status == "error"
    ]

    scores: dict[str, float | None] = {}
    evaluated: dict[str, bool] = {}
    for dim, codes in DIMENSION_RULES.items():
        relevant = [
            r for r in results
            if r.rule_code in codes and r.status in ("pass", "fail") and r.total_checked > 0
        ]
        if not relevant:
            scores[dim] = None
            evaluated[dim] = False
            continue
        failed = sum(r.failed_count for r in relevant)
        total = sum(r.total_checked for r in relevant)
        scores[dim] = round(min(1.0, max(0.0, 1 - failed / total)), 4)
        evaluated[dim] = True

    active = [d for d in DIMENSION_RULES if evaluated[d]]
    if active:
        weight_sum = sum(WEIGHTS[d] for d in active)
        overall = round(
            sum(scores[d] * WEIGHTS[d] for d in active) / weight_sum, 4
        )
    else:
        overall = None

    # 条目通过率（诊断）：只看状态，与要素量无关
    entry_passed = sum(1 for r in results if r.status == "pass")
    entry_failed = sum(1 for r in results if r.status == "fail")
    denominator = entry_passed + entry_failed
    rule_pass_rate = round(entry_passed / denominator, 4) if denominator else 1.0

    return {
        **scores,
        "overall": overall,
        "evaluated": evaluated,
        "rule_pass_rate": rule_pass_rate,
        "evaluation_errors": evaluation_errors,
        "profile": "v3",
    }


def compute_scores(
    attr_results: list[AttrCheckResult],
    topo_results: list[TopologyCheckResult],
) -> dict:
    """按 `settings.scoring_profile` 分发（v2 回滚开关 / v3 默认）。"""
    if settings.scoring_profile == "v3":
        return compute_scores_v3(attr_results, topo_results)
    return compute_scores_v2(attr_results, topo_results)


# ── 主入口 ───────────────────────────────────────────────────────────────────

async def run_quality_check(
    db,
    standard_id: str | None,
    stored_file_id: str,
    api_key_id: str,
    geometry_type: str | None = None,
    topology_config: TopologyConfig | None = None,
    standard_table_id: str | None = None,
    layer_names: list[str] | None = None,
) -> dict:
    """
    执行完整的数据质量检查流程。

    Args:
        db: AsyncSession
        standard_id: 数据标准 ID（为空则跳过属性/结构检查，仅执行拓扑检查）
        stored_file_id: 已入库文件 ID
        api_key_id: API Key ID
        geometry_type: 标准表的几何类型 (Point/LineString/Polygon)
        topology_config: 拓扑检查配置
        standard_table_id: 【S4】指定后仅检查该标准表（文件关联图层），为空检查全部表
        layer_names: 【v2.10.5】质检图层圈定；None/空=全部图层。
                     仅影响拓扑检查范围与计数，圈定语义见 run_topology_checks。

    Returns:
        {
            "attr_results": [...],
            "attr_summary": {...},
            "topo_results": [...],
            "topo_summary": {...},
            "scores": {...},
            "total_violations": int,      # 失败要素数之和
            "violation_samples": [...],
            "feature_count": int,         # 文件要素数（stored_files.feature_count）
            "violation_count": int,       # 不通过条目数（status == "fail" 的条目）
        }
    """
    # ── Phase 0.5: 无图形要素守卫 ─────────────────────────────────
    # 已入库文件的 feature_count 在上传解析时写入。
    # 若显式为 0，直接返回明确失败结论（防止空结果被计为满分"通过"）。
    from sqlalchemy import select
    from app.models.stored_file import StoredFile as _SF
    _sf_row = (await db.execute(select(_SF).where(_SF.id == stored_file_id))).scalar_one_or_none()
    _fc = _sf_row.feature_count if _sf_row else None
    if _fc is not None and _fc <= 0:
        logger.warning("run_quality_check: 文件无图形要素 stored_file_id=%s", stored_file_id)
        _empty = {
            "total": 0, "passed": 0, "failed": 0,
            "by_severity": {"error": 0, "warning": 0},
            "message": "文件无图形要素（feature_count=0），质检无法执行",
        }
        return {
            "attr_results": [],
            "attr_summary": dict(_empty),
            "topo_results": [],
            "topo_summary": dict(_empty),
            "scores": {"completeness": 0.0, "accuracy": 0.0, "consistency": 0.0,
                       "uniqueness": 0.0, "topology": 0.0, "overall": 0.0,
                       # 口径标签如实记录，避免这行被当成 v2 历史数据统计
                       "profile": settings.scoring_profile},
            "total_violations": 0,
            "violation_samples": [],
            "feature_count": 0,
            "violation_count": 0,
        }

    # ── Phase 1: 属性结构检查 ────────────────────────────────────────
    logger.info("Phase 1: 属性结构检查 start")
    if standard_id:
        attr_results, attr_summary = await run_attribute_checks(
            db, standard_id, stored_file_id, api_key_id,
            standard_table_id=standard_table_id,
        )
    else:
        attr_results, attr_summary = [], {
            "total": 0, "passed": 0, "failed": 0,
            "by_severity": {"error": 0, "warning": 0},
        }
    logger.info("Phase 1: %d 项检查, %d 通过, %d 未通过",
                attr_summary["total"], attr_summary["passed"], attr_summary["failed"])

    # ── Phase 2: 拓扑检查 ───────────────────────────────────────────
    logger.info("Phase 2: 拓扑检查 start (layers=%s)",
                layer_names if layer_names else "全部")
    topo_results, topo_summary = await run_topology_checks(
        db, stored_file_id, api_key_id,
        geometry_type=geometry_type,
        config=topology_config or TopologyConfig(),
        layer_names=layer_names,
    )
    logger.info("Phase 2: %d 项检查, %d 通过, %d 未通过",
                topo_summary["total"], topo_summary["passed"], topo_summary["failed"])

    # ── Phase 3: 五维评分 ───────────────────────────────────────────
    scores = compute_scores(attr_results, topo_results)

    # 违规样本（前 100 条）
    violation_samples = []
    for r in attr_results:
        if r.status == "fail" and r.samples:
            for s in r.samples[:5]:
                violation_samples.append({
                    "rule_code": r.rule_code,
                    "rule_name": r.rule_name,
                    "layer": r.layer,
        "field": r.field_name,
                    "message": r.message,
                    "sample": s,
                })
    for r in topo_results:
        if r.status == "fail" and r.feature_indices:
            for idx in r.feature_indices[:5]:
                violation_samples.append({
                    "rule_code": r.rule_code,
                    "rule_name": r.rule_name,
                    "message": r.message,
                    "feature_index": idx,
                })
    violation_samples = violation_samples[:100]

    total_violations = sum(
        r.failed_count for r in attr_results if r.status == "fail"
    ) + sum(
        r.failed_count for r in topo_results if r.status == "fail"
    )

    # 不通过条目数（v3 口径：与 total_violations 的「失败要素数之和」不同）
    violation_count = sum(
        1 for r in list(attr_results) + list(topo_results) if r.status == "fail"
    )

    return {
        "attr_results": [_check_to_dict(r) for r in attr_results],
        "attr_summary": attr_summary,
        "topo_results": [_topo_to_dict(r) for r in topo_results],
        "topo_summary": topo_summary,
        "scores": scores,
        "total_violations": total_violations,
        "violation_samples": violation_samples,
        # 要素数取自 stored_files.feature_count（Phase 0.5 已查），不是检查项数
        "feature_count": int(_fc or 0),
        "violation_count": violation_count,
    }


def _check_to_dict(r: AttrCheckResult) -> dict:
    return {
        "rule_code": r.rule_code,
        "rule_name": r.rule_name,
        "status": r.status,
        "severity": r.severity,
        "field": r.field_name,
        "field_name": r.field_name,
        "layer": r.layer,
        "failed_count": r.failed_count,
        "total_checked": r.total_checked,
        "denominator_scope": r.denominator_scope,
        "expected": r.expected,
        "actual": r.actual,
        "message": r.message,
        "samples": r.samples,
    }


def _topo_to_dict(r: TopologyCheckResult) -> dict:
    return {
        "rule_code": r.rule_code,
        "rule_name": r.rule_name,
        "status": r.status,
        "severity": r.severity,
        "failed_count": r.failed_count,
        "total_checked": r.total_checked,
        "denominator_scope": r.denominator_scope,
        "message": r.message,
        "feature_indices": r.feature_indices[:50],
        "pairs": r.pairs[:50],
    }


# ── 兼容旧接口 ───────────────────────────────────────────────────────────────

def check_quality(
    file_path: str,
    detected_format: str | None = None,
    data_standard_id: str | None = None,
    standard_fields: dict[str, dict] | None = None,
    topology_config: dict | None = None,
) -> QualityResult:
    """旧接口兼容（同步版本，用于旧 API 调用）。"""
    import os
    file_name = os.path.basename(file_path)

    from app.services.format_detection import detect_format
    fmt_result = detect_format(file_path)
    fmt = detected_format or fmt_result.format_name

    result = QualityResult(
        file_name=file_name,
        detected_format=fmt,
        total_features=fmt_result.feature_count or 0,
        total_fields=len(fmt_result.attribute_schema),
        completeness_score=1.0,
        data_standard_id=data_standard_id,
        attribute_schema=[
            {"name": f.name, "field_type": f.field_type, "nullable": f.nullable}
            for f in fmt_result.attribute_schema
        ],
    )

    # 保留旧的 null 值分析 + 关键字段缺失检测
    try:
        from app.services.attribute_processing import process_attributes
        attr = process_attributes(file_path, preserve_critical_fields=True)
        result.total_features = max(result.total_features, attr.total_input)
        result.total_fields = max(result.total_fields, len(attr.fields))
        _analyze_critical_fields_legacy(result, attr.fields, attr.sample_data)
        if attr.sample_data:
            _analyze_null_values_legacy(result, attr.fields, attr.sample_data)
    except Exception as e:
        logger.warning("属性处理失败: %s", e)

    result.completeness_score = _calculate_score_legacy(result)
    return result


def _analyze_null_values_legacy(result: QualityResult, fields: list[str], samples: list[dict]):
    null_counts = {}
    for f in fields:
        null_counts[f] = sum(1 for row in samples if row.get(f) in (None, '', ' '))
    result.null_value_counts = null_counts
    missing = []
    total = len(samples)
    for f, count in null_counts.items():
        if count > 0:
            missing.append({"field": f, "null_count": count, "null_rate": round(count / total, 3)})
    result.missing_fields = sorted(missing, key=lambda x: x['null_count'], reverse=True)


def _analyze_critical_fields_legacy(result: QualityResult, fields: list[str], samples: list[dict]):
    """逐关键字段判定 present / null_count / null_rate。

    关键字段清单来自 app.core.constants.CRITICAL_FIELDS（含中英文别名，如
    YWBH/ywbh/业务编号 均指向同一业务字段）。
    - 字段在数据集中存在（大小写不敏感）→ present=True，并统计空值情况；
    - 字段完全缺失 → present=False，null_count=样本数、null_rate=1.0（视为整体缺失）。
    """
    fields_upper = {f.upper(): f for f in fields}
    total = len(samples)
    for key in CRITICAL_FIELDS:
        actual = fields_upper.get(key.upper())
        if actual is None:
            result.critical_fields_status[key] = {
                "present": False,
                "null_count": total,
                "null_rate": round(1.0, 3) if total else 0,
            }
            continue
        null_count = sum(1 for row in samples if row.get(actual) in (None, '', ' '))
        result.critical_fields_status[key] = {
            "present": True,
            "null_count": null_count,
            "null_rate": round(null_count / total, 3) if total else 0,
        }


def _calculate_score_legacy(result: QualityResult) -> float:
    total = result.total_features
    if total == 0:
        # 无图形要素不得判为通过（此前返回 1.0 满分）
        return 0.0
    generic_score = 1.0
    null_rates = [e.get('null_rate', 0) for e in result.missing_fields]
    if null_rates:
        generic_score = 1.0 - sum(null_rates) / len(null_rates)
    return round(generic_score, 3)
