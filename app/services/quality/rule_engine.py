"""质检规则引擎 — 加载规则 → 匹配字段 → 执行检查 → 生成结果。

每种 RuleType 对应一个检查函数，接收字段值和规则参数，返回检查结果。
"""

from __future__ import annotations

import re
from typing import Any

from app.models.quality_rule import QualityRule


class RuleCheckResult:
    """单条规则对单个字段的检查结果。"""
    def __init__(self, rule: QualityRule, passed: bool = True, failed_count: int = 0,
                 detail: dict | None = None):
        self.rule = rule
        self.passed = passed
        self.failed_count = failed_count
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule.id,
            "rule_type": self.rule.rule_type,
            "rule_name": self.rule.rule_name,
            "field_pattern": self.rule.field_pattern,
            "passed": self.passed,
            "severity": self.rule.severity,
            "total_checked": 0,
            "failed_count": self.failed_count,
            "detail": self.detail,
        }


def _match_field(field_name: str, pattern: str) -> bool:
    """判断字段名是否匹配规则的模式（支持 * 通配符）。"""
    if pattern == field_name:
        return True
    if "*" not in pattern and "?" not in pattern:
        return field_name.upper() == pattern.upper()
    # 简单通配符匹配：* → .*, ? → .
    regex = "^" + re.escape(pattern).replace("\\*", ".*").replace("\\?", ".") + "$"
    return bool(re.match(regex, field_name, re.IGNORECASE))


def _find_field_in_data(field_pattern: str, sample_data: list[dict]) -> str | None:
    """在数据中找到匹配的字段名。"""
    if not sample_data:
        return None
    first_row = sample_data[0]
    for field_name in first_row:
        if _match_field(field_name, field_pattern):
            return field_name
    return None


def check_rule_not_null(
    rule: QualityRule, field_name: str, sample_data: list[dict]
) -> RuleCheckResult:
    """NOT_NULL: 字段值不可为空。"""
    total = len(sample_data)
    failed = sum(1 for row in sample_data if row.get(field_name) in (None, "", " "))
    passed = failed == 0
    return RuleCheckResult(
        rule, passed=passed, failed_count=failed,
        detail={"null_count": failed, "null_rate": round(failed / total, 4) if total else 0},
    )


def check_rule_value_in_set(
    rule: QualityRule, field_name: str, sample_data: list[dict]
) -> RuleCheckResult:
    """VALUE_IN_SET: 字段值必须在允许列表中。"""
    allowed = (rule.params or {}).get("allowed_values", [])
    if not allowed:
        return RuleCheckResult(rule, passed=True)
    total = 0
    failed = 0
    errors: list[str] = []
    for row in sample_data:
        val = row.get(field_name)
        if val is None or str(val).strip() in ("", " "):
            continue
        total += 1
        if str(val) not in allowed:
            failed += 1
            if len(errors) < 10:
                errors.append(str(val))
    return RuleCheckResult(
        rule, passed=failed == 0, failed_count=failed,
        detail={"allowed_values": allowed, "sample_errors": errors, "total_checked": total},
    )


def check_rule_type_match(
    rule: QualityRule, field_name: str, sample_data: list[dict]
) -> RuleCheckResult:
    """TYPE_MATCH: 字段值类型与预期匹配。"""
    expected = (rule.params or {}).get("expected_type", "string")
    total = 0
    failed = 0
    for row in sample_data:
        val = row.get(field_name)
        if val is None or str(val).strip() in ("", " "):
            continue
        total += 1
        inferred = _infer_type(val)
        if inferred != expected:
            failed += 1
    return RuleCheckResult(
        rule, passed=failed == 0, failed_count=failed,
        detail={"expected_type": expected, "total_checked": total},
    )


def check_rule_length_limit(
    rule: QualityRule, field_name: str, sample_data: list[dict]
) -> RuleCheckResult:
    """LENGTH_LIMIT: 字符串长度不超过上限。"""
    max_len = (rule.params or {}).get("max_length", 0)
    if not max_len:
        return RuleCheckResult(rule, passed=True)
    total = 0
    failed = 0
    for row in sample_data:
        val = row.get(field_name)
        if val is None:
            continue
        total += 1
        if len(str(val)) > max_len:
            failed += 1
    return RuleCheckResult(
        rule, passed=failed == 0, failed_count=failed,
        detail={"max_length": max_len, "total_checked": total},
    )


_RULE_CHECKERS = {
    "NOT_NULL": check_rule_not_null,
    "VALUE_IN_SET": check_rule_value_in_set,
    "TYPE_MATCH": check_rule_type_match,
    "LENGTH_LIMIT": check_rule_length_limit,
}


def execute_rules(
    rules: list[QualityRule],
    sample_data: list[dict],
) -> list[RuleCheckResult]:
    """对采样数据执行一组质检规则。

    Args:
        rules: 待执行的规则列表。
        sample_data: 采样数据（attribute_processing 的输出）。

    Returns:
        每条规则的检查结果（仅包含能找到匹配字段的规则）。
    """
    results: list[RuleCheckResult] = []
    for rule in rules:
        checker = _RULE_CHECKERS.get(rule.rule_type)
        if not checker:
            continue
        field_name = _find_field_in_data(rule.field_pattern, sample_data)
        if not field_name:
            # 字段在数据中不存在 → 自动标记为未通过
            results.append(RuleCheckResult(
                rule, passed=False, failed_count=len(sample_data),
                detail={"error": f"字段 '{rule.field_pattern}' 在数据中未找到"},
            ))
            continue
        result = checker(rule, field_name, sample_data)
        results.append(result)
    return results


def _infer_type(value: Any) -> str:
    """推断采样值的 Python 类型（bool→string/int/float/string，字符串尝试 numeric 解析）。

    供 TYPE_MATCH 规则使用。与 `format_detection._infer_type` 为独立实现的相似副本，
    差异：本版支持字符串 numeric 解析（"42"→integer），二者无相互依赖，保持各自语义；
    无 quality_checker 同名函数（历史 docstring 曾假引用，已更正）。
    """
    if isinstance(value, bool):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    s = str(value)
    try:
        int(s)
        return "integer"
    except ValueError:
        pass
    try:
        float(s)
        return "float"
    except ValueError:
        pass
    return "string"
