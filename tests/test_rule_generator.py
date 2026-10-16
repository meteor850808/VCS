# -*- coding: utf-8 -*-
"""P5i — app/services/quality/rule_generator.py 服务级 100%。

覆盖：
- generate_rules_from_field 五类规则（NOT_NULL/TYPE_MATCH/LENGTH_LIMIT/UNIQUE/VALUE_IN_SET）
  及各分支（nullable 语义、label 取 alias、mark_auto=False、字典项 code 过滤）。
- __get__ descriptor 抛异常 → 字典加载失败静默不影响其余规则。
- generate_rules_for_standard 全量生成（真实库 selectinload dict_links）。
"""
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.dictionary_item import DictionaryItem
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.data_standard import DataStandard
from app.services.quality.rule_generator import (
    generate_rules_from_field,
    generate_rules_for_standard,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


def _field(**kw) -> StandardField:
    defaults = dict(
        id=str(uuid.uuid4()), standard_id=None, table_id=None,
        field_name="f", field_alias=None, data_type=None, length=None,
        precision=None, nullable=True, is_required=False, is_primary_key=False,
    )
    defaults.update(kw)
    return StandardField(**defaults)


def _attach_dict(field: StandardField, codes: list[str | None]) -> StandardFieldDictLink:
    d = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=field.standard_id,
        dict_name="允许值", dict_code="ALLOW", scope="standard",
    )
    d.items = [DictionaryItem(id=str(uuid.uuid4()), dict_id=d.id, item_code=c) for c in codes]
    link = StandardFieldDictLink(
        id=str(uuid.uuid4()), field_id=field.id, dict_id=d.id,
    )
    link.dictionary = d
    field.dict_links = [link]
    return link


class TestGenerateRulesFromField:

    def test_all_five_rule_types(self):
        field = _field(field_name="code", field_alias="编码", data_type="string",
                       length=20, nullable=False, is_required=True, is_primary_key=True)
        _attach_dict(field, ["X", "Y", None])  # None code 被过滤
        rules = generate_rules_from_field(field, "key-id")
        assert len(rules) == 5
        types = {r.rule_type for r in rules}
        assert types == {"NOT_NULL", "TYPE_MATCH", "LENGTH_LIMIT", "UNIQUE", "VALUE_IN_SET"}
        # label 取 field_alias
        nn = next(r for r in rules if r.rule_type == "NOT_NULL")
        assert "编码" in nn.rule_name
        assert nn.rule_name == "字段 编码 不可为空"
        assert nn.severity == "critical"
        tm = next(r for r in rules if r.rule_type == "TYPE_MATCH")
        assert tm.params == {"expected_type": "string"}
        ll = next(r for r in rules if r.rule_type == "LENGTH_LIMIT")
        assert ll.params == {"max_length": 20}
        uniq = next(r for r in rules if r.rule_type == "UNIQUE")
        assert uniq.severity == "error"
        vs = next(r for r in rules if r.rule_type == "VALUE_IN_SET")
        assert vs.params == {"allowed_values": ["X", "Y"]}
        assert "允许值" in vs.rule_name
        for r in rules:
            assert r.api_key_id == "key-id"
            assert r.standard_field_id == field.id
            assert r.field_pattern == "code"
            assert r.is_auto is True
            assert r.enabled is True

    def test_nullable_field_no_not_null(self):
        field = _field(field_name="g", data_type="integer", length=1)
        rules = generate_rules_from_field(field, "k")
        assert {r.rule_type for r in rules} == {"TYPE_MATCH", "LENGTH_LIMIT"}

    def test_empty_field_zero_rules(self):
        field = _field()
        assert generate_rules_from_field(field, "k") == []

    def test_mark_auto_false(self):
        field = _field(field_name="f", data_type="string")
        rules = generate_rules_from_field(field, "k", mark_auto=False)
        assert len(rules) == 1
        assert rules[0].is_auto is False
        assert rules[0].rule_type == "TYPE_MATCH"

    def test_dict_link_with_empty_items(self):
        field = _field(field_name="f", data_type="string")
        _attach_dict(field, [])
        rules = generate_rules_from_field(field, "k")
        assert {r.rule_type for r in rules} == {"TYPE_MATCH"}

    def test_dict_links_empty_list(self):
        field = _field(field_name="f", data_type="string")
        field.dict_links = []
        rules = generate_rules_from_field(field, "k")
        assert {r.rule_type for r in rules} == {"TYPE_MATCH"}

    def test_dict_loading_exception_is_swallowed(self):
        class _NotIterable:
            """非可迭代对象：for loop 抛 TypeError → 被 except 吞噬。"""

        field = SimpleNamespace(
            id="fx", field_alias=None, field_name="f", nullable=True,
            is_required=False, data_type="string", length=1, is_primary_key=False,
            dict_links=_NotIterable(),
        )
        rules = generate_rules_from_field(field, "k")
        assert {r.rule_type for r in rules} == {"TYPE_MATCH", "LENGTH_LIMIT"}


class TestGenerateRulesForStandard:

    async def test_generates_for_all_fields(self, db_session):
        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="rg_std_a", is_active=True)
        db_session.add(key)
        await db_session.flush()
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id,
                           name="规则标准", code="RG-1")
        db_session.add(std)
        await db_session.flush()
        layer = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                                   group_name="L", ordering=0)
        db_session.add(layer)
        await db_session.flush()
        table = StandardTable(id=str(uuid.uuid4()), layer_id=layer.id,
                              standard_id=std.id, table_name="T", table_type="attribute")
        db_session.add(table)
        await db_session.flush()
        f1 = _field(id=str(uuid.uuid4()), standard_id=std.id,
                    table_id=table.id, field_name="name", data_type="string")
        f2 = _field(id=str(uuid.uuid4()), standard_id=std.id,
                    table_id=table.id, field_name="note", data_type="")
        db_session.add_all([f1, f2])
        await db_session.commit()

        rules = await generate_rules_for_standard(std.id, key.id, db_session)
        assert len(rules) == 1  # 仅 f1 有 data_type
        assert rules[0].rule_type == "TYPE_MATCH"

        # 空标准 → 0 条
        empty_rules = await generate_rules_for_standard(str(uuid.uuid4()), key.id, db_session)
        assert empty_rules == []