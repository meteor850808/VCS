"""P0: rule_engine 纯逻辑路由级单测 — 零 DB/Geo 依赖。

覆盖 app/services/quality/rule_engine.py：
- 4 个规则 checker（NOT_NULL / VALUE_IN_SET / TYPE_MATCH / LENGTH_LIMIT）
- 通配符匹配 _match_field / 字段查找 _find_field_in_data
- 分发 execute_rules（含未知类型跳过、字段缺失标记失败 等分支）
- 结果序列化 RuleCheckResult.to_dict
- 类型推断 _infer_type
"""

import pytest

from app.models.quality_rule import QualityRule
from app.services.quality.rule_engine import (
    RuleCheckResult,
    _find_field_in_data,
    _infer_type,
    _match_field,
    check_rule_length_limit,
    check_rule_not_null,
    check_rule_type_match,
    check_rule_value_in_set,
    execute_rules,
)


def _rule(rule_type: str, field_pattern: str, params=None, **kw) -> QualityRule:
    """构造一个无需 DB session 的 QualityRule 桩。"""
    return QualityRule(
        id=kw.get("id", "r-1"),
        api_key_id="k-1",
        rule_type=rule_type,
        rule_name=kw.get("rule_name", f"规则-{rule_type}"),
        field_pattern=field_pattern,
        severity=kw.get("severity", "warning"),
        params=params or {},
    )


# ── _match_field / _find_field_in_data ─────────────────────────────

class TestMatchField:
    def test_exact_match(self):
        assert _match_field("NAME", "NAME") is True
        assert _match_field("name", "NAME") is True  # 无通配符时大小写不敏感

    def test_star_wildcard(self):
        assert _match_field("province_code", "province_*") is True
        assert _match_field("province_name", "province_*") is True
        assert _match_field("city_code", "province_*") is False

    def test_question_wildcard(self):
        assert _match_field("code", "cod?") is True
        assert _match_field("codes", "cod?") is False

    def test_wildcard_case_insensitive(self):
        assert _match_field("PROVINCE_CODE", "province_*") is True


class TestFindFieldInData:
    def test_find_by_exact(self):
        data = [{"name": "北京", "code": "110000"}]
        assert _find_field_in_data("code", data) == "code"

    def test_find_by_wildcard(self):
        data = [{"province_code": "11", "name": "北京"}]
        assert _find_field_in_data("province_*", data) == "province_code"

    def test_no_match_returns_none(self):
        data = [{"name": "北京"}]
        assert _find_field_in_data("code", data) is None

    def test_empty_data_returns_none(self):
        assert _find_field_in_data("code", []) is None


# ── _infer_type ──────────────────────────────────────────────────

class TestInferType:
    def test_bool_is_string(self):
        assert _infer_type(True) == "string"

    def test_int(self):
        assert _infer_type(5) == "integer"
        assert _infer_type(-3) == "integer"

    def test_float(self):
        assert _infer_type(3.14) == "float"

    def test_numeric_string(self):
        assert _infer_type("42") == "integer"
        assert _infer_type("3.14") == "float"

    def test_non_numeric_string(self):
        assert _infer_type("abc") == "string"


# ── check_rule_not_null ─────────────────────────────────────────

class TestCheckNotNull:
    def test_all_present_passes(self):
        rule = _rule("NOT_NULL", "name")
        data = [{"name": "a"}, {"name": "b"}]
        res = check_rule_not_null(rule, "name", data)
        assert res.passed is True
        assert res.failed_count == 0

    def test_nulls_fail(self):
        rule = _rule("NOT_NULL", "name")
        data = [{"name": None}, {"name": ""}, {"name": " "}, {"name": "ok"}]
        res = check_rule_not_null(rule, "name", data)
        assert res.passed is False
        assert res.failed_count == 3
        assert res.detail["null_count"] == 3
        assert res.detail["null_rate"] == 0.75

    def test_empty_data_no_division_error(self):
        rule = _rule("NOT_NULL", "name")
        res = check_rule_not_null(rule, "name", [])
        assert res.passed is True
        assert res.detail["null_rate"] == 0


# ── check_rule_value_in_set ─────────────────────────────────────

class TestCheckValueInSet:
    def test_no_allowed_everything_passes(self):
        rule = _rule("VALUE_IN_SET", "status", params={})
        res = check_rule_value_in_set(rule, "status", [{"status": "x"}])
        assert res.passed is True

    def test_valid_values_pass(self):
        rule = _rule("VALUE_IN_SET", "status", params={"allowed_values": ["open", "closed"]})
        data = [{"status": "open"}, {"status": "closed"}]
        res = check_rule_value_in_set(rule, "status", data)
        assert res.passed is True
        assert res.detail["total_checked"] == 2

    def test_invalid_values_fail_with_errors(self):
        rule = _rule("VALUE_IN_SET", "status", params={"allowed_values": ["open"]})
        data = [{"status": "open"}, {"status": "bogus"}, {"status": "other"}]
        res = check_rule_value_in_set(rule, "status", data)
        assert res.passed is False
        assert res.failed_count == 2
        assert set(res.detail["sample_errors"]) == {"bogus", "other"}

    def test_null_and_blank_skipped(self):
        rule = _rule("VALUE_IN_SET", "status", params={"allowed_values": ["open"]})
        data = [{"status": None}, {"status": ""}, {"status": " "}]
        res = check_rule_value_in_set(rule, "status", data)
        assert res.passed is True
        assert res.detail["total_checked"] == 0


# ── check_rule_type_match ───────────────────────────────────────

class TestCheckTypeMatch:
    def test_matching_int(self):
        rule = _rule("TYPE_MATCH", "count", params={"expected_type": "integer"})
        data = [{"count": 1}, {"count": "2"}]
        res = check_rule_type_match(rule, "count", data)
        assert res.passed is True
        assert res.detail["expected_type"] == "integer"
        assert res.detail["total_checked"] == 2

    def test_mismatch_fails(self):
        rule = _rule("TYPE_MATCH", "count", params={"expected_type": "integer"})
        data = [{"count": 1}, {"count": "abc"}]
        res = check_rule_type_match(rule, "count", data)
        assert res.passed is False
        assert res.failed_count == 1

    def test_default_expected_string(self):
        rule = _rule("TYPE_MATCH", "name", params={})
        data = [{"name": "北京"}]
        res = check_rule_type_match(rule, "name", data)
        assert res.passed is True

    def test_blank_skipped(self):
        rule = _rule("TYPE_MATCH", "count", params={"expected_type": "integer"})
        data = [{"count": None}, {"count": " "}]
        res = check_rule_type_match(rule, "count", data)
        assert res.passed is True
        assert res.detail["total_checked"] == 0


# ── check_rule_length_limit ─────────────────────────────────────

class TestCheckLengthLimit:
    def test_no_limit_passes(self):
        rule = _rule("LENGTH_LIMIT", "name", params={})
        res = check_rule_length_limit(rule, "name", [{"name": "x" * 1000}])
        assert res.passed is True

    def test_within_limit(self):
        rule = _rule("LENGTH_LIMIT", "name", params={"max_length": 5})
        data = [{"name": "abc"}, {"name": "defg"}]
        res = check_rule_length_limit(rule, "name", data)
        assert res.passed is True
        assert res.detail["max_length"] == 5
        assert res.detail["total_checked"] == 2

    def test_over_limit_fails(self):
        rule = _rule("LENGTH_LIMIT", "name", params={"max_length": 3})
        data = [{"name": "ab"}, {"name": "abcd"}]
        res = check_rule_length_limit(rule, "name", data)
        assert res.passed is False
        assert res.failed_count == 1

    def test_null_skipped(self):
        rule = _rule("LENGTH_LIMIT", "name", params={"max_length": 3})
        res = check_rule_length_limit(rule, "name", [{"name": None}])
        assert res.passed is True
        assert res.detail["total_checked"] == 0


# ── execute_rules（分发 + 字段缺失分支）────────────────────────

class TestExecuteRules:
    def test_unknown_rule_type_skipped(self):
        rules = [_rule("UNKNOWN_TYPE", "name")]
        results = execute_rules(rules, [{"name": "x"}])
        assert results == []

    def test_missing_field_marks_failed(self):
        rules = [_rule("NOT_NULL", "ghost_code")]
        data = [{"name": "x"}, {"name": "y"}]
        results = execute_rules(rules, data)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].failed_count == 2
        assert "未找到" in results[0].detail["error"]

    def test_empty_rules(self):
        assert execute_rules([], [{"a": 1}]) == []

    def test_mixed_rules(self):
        rules = [
            _rule("NOT_NULL", "name"),
            _rule("VALUE_IN_SET", "status", params={"allowed_values": ["ok"]}),
        ]
        data = [{"name": "a", "status": "ok"}, {"name": None, "status": "bad"}]
        results = execute_rules(rules, data)
        assert len(results) == 2
        assert results[0].passed is False  # name 有 null
        assert results[1].passed is False  # status bad


# ── RuleCheckResult.to_dict ────────────────────────────────────

class TestToDict:
    def test_fields_mapped(self):
        rule = _rule("LENGTH_LIMIT", "code", params={"max_length": 4}, severity="error")
        res = RuleCheckResult(rule, passed=False, failed_count=2, detail={"max_length": 4})
        d = res.to_dict()
        assert d["rule_id"] == "r-1"
        assert d["rule_type"] == "LENGTH_LIMIT"
        assert d["rule_name"] == "规则-LENGTH_LIMIT"
        assert d["field_pattern"] == "code"
        assert d["passed"] is False
        assert d["severity"] == "error"
        assert d["total_checked"] == 0
        assert d["failed_count"] == 2
        assert d["detail"] == {"max_length": 4}

    def test_default_detail(self):
        rule = _rule("NOT_NULL", "name")
        res = RuleCheckResult(rule)
        assert res.detail == {}