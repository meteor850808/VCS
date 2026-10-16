# -*- coding: utf-8 -*-
"""P7b-5: api/v1/quality_rules.py 全覆盖（52% → 100%）。

规则 CRUD（含过滤/scope 隔离/404）、自动生成（幂等删除旧规则）。
"""

import uuid

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.quality_rule import QualityRule
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


async def _key(db_session, name):
    raw, hashed = generate_api_key()
    k = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(k)
    await db_session.commit()
    return raw, k


async def _std(db_session, key, name="P7b规则标准"):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-QR-{uuid.uuid4().hex[:6]}",
        name=name, category="custom", status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _field(db_session, std, with_dict=True):
    """造一个会命中全部规则分支的字段。"""
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id,
        group_name="规则层", group_code="QR",
    )
    db_session.add(lg)
    await db_session.flush()
    t = StandardTable(
        id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
        table_name="规则表", table_code="QR_T", table_type="spatial",
    )
    db_session.add(t)
    await db_session.flush()
    f = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=t.id,
        field_name="道路等级", field_alias="等级", data_type="string",
        length=10, nullable=False, is_required=True, is_primary_key=True,
    )
    db_session.add(f)
    await db_session.flush()
    if with_dict:
        d = StandardDictionary(
            id=str(uuid.uuid4()), standard_id=std.id,
            scope="standard", dict_name="等级字典", dict_code="LV",
        )
        db_session.add(d)
        await db_session.flush()
        db_session.add_all([
            DictionaryItem(id=str(uuid.uuid4()), dict_id=d.id,
                           item_code="A", item_name="高速"),
            DictionaryItem(id=str(uuid.uuid4()), dict_id=d.id,
                           item_code="B", item_name="国道"),
        ])
        await db_session.flush()
        db_session.add(StandardFieldDictLink(
            id=str(uuid.uuid4()), field_id=f.id, dict_id=d.id,
            is_primary=True,
        ))
        await db_session.flush()
    return t, f


async def _rule(db_session, key, std_field_id=None, rule_type="LENGTH_LIMIT",
                name="长度限制"):
    r = QualityRule(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        standard_field_id=std_field_id,
        rule_type=rule_type,
        rule_name=name,
        field_pattern="等级",
        severity="warning",
        params={"max_length": 10},
        enabled=True,
        is_auto=False,
    )
    db_session.add(r)
    await db_session.commit()
    return r


class TestListRules:

    async def test_filters_and_scope(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_qr_a")
        raw_b, key_b = await _key(db_session, "p7b_qr_b")
        r1 = await _rule(db_session, key_a, rule_type="NOT_NULL", name="非空")
        r2 = await _rule(db_session, key_a, rule_type="TYPE_MATCH", name="类型")
        db_session.add(QualityRule(
            id=str(uuid.uuid4()), api_key_id=key_a.id,
            standard_field_id=None, rule_type="TYPE_MATCH",
            rule_name="禁用规则", field_pattern="x",
            severity="warning", params=None, enabled=False, is_auto=False,
        ))
        await db_session.commit()
        h_a = {"X-API-Key": raw_a}
        # 全量（仅本人）
        resp = await client.get("/api/v1/quality-rules", headers=h_a)
        assert len(resp.json()) == 3
        # rule_type 过滤
        resp = await client.get(
            "/api/v1/quality-rules?rule_type=NOT_NULL", headers=h_a)
        assert [r["id"] for r in resp.json()] == [r1.id]
        # enabled_only
        resp = await client.get(
            "/api/v1/quality-rules?enabled_only=true", headers=h_a)
        assert all(r["enabled"] for r in resp.json()) and len(resp.json()) == 2
        # 异 key 隔离：看不到 a 的规则
        resp = await client.get(
            "/api/v1/quality-rules", headers={"X-API-Key": raw_b})
        assert resp.json() == []


class TestCreateUpdateDelete:

    async def test_create_and_update(self, client, db_session):
        raw, key = await _key(db_session, "p7b_qr_c")
        s = await _std(db_session, key)
        _, f = await _field(db_session, s)
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 创建（field_pattern 空 → 用 rule_name）
        resp = await client.post(
            "/api/v1/quality-rules", headers=h,
            json={"standard_field_id": f.id, "rule_type": "PATTERN_MATCH",
                  "rule_name": "编码正则", "severity": "error",
                  "params": {"pattern": "^[A-Z]+$"}, "enabled": True})
        assert resp.status_code == 201
        body = resp.json()
        assert body["field_pattern"] == "编码正则"
        assert body["is_auto"] is False
        rid = body["id"]
        # 更新（全部字段 + updated_at）
        resp = await client.put(
            f"/api/v1/quality-rules/{rid}", headers=h,
            json={"rule_name": "新名", "severity": "critical",
                  "params": {"pattern": "^[0-9]+$"}, "enabled": False})
        assert resp.status_code == 200
        assert resp.json()["rule_name"] == "新名"
        assert resp.json()["severity"] == "critical"
        assert resp.json()["params"] == {"pattern": "^[0-9]+$"}
        assert resp.json()["enabled"] is False
        # 部分更新（只改名）
        resp = await client.put(
            f"/api/v1/quality-rules/{rid}", headers=h, json={"rule_name": "半更"})
        assert resp.json()["rule_name"] == "半更"
        assert resp.json()["enabled"] is False  # 未传保持

    async def test_404_and_delete(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_qr_d")
        raw_b, key_b = await _key(db_session, "p7b_qr_e")
        r = await _rule(db_session, key_a)
        await db_session.commit()
        h_a = {"X-API-Key": raw_a}
        # 更新 404（不存在 + 非所属）
        assert (await client.put(
            "/api/v1/quality-rules/no-such", headers=h_a,
            json={"rule_name": "x"})).status_code == 404
        assert (await client.put(
            f"/api/v1/quality-rules/{r.id}",
            headers={"X-API-Key": raw_b},
            json={"rule_name": "x"})).status_code == 404
        # 删除 404 + 204
        assert (await client.delete(
            "/api/v1/quality-rules/no-such", headers=h_a)).status_code == 404
        assert (await client.delete(
            f"/api/v1/quality-rules/{r.id}", headers=h_a)).status_code == 204


class TestGenerateRules:

    async def test_generate_idempotent(self, client, db_session):
        raw, key = await _key(db_session, "p7b_qr_f")
        s = await _std(db_session, key)
        _, f = await _field(db_session, s)
        await db_session.commit()
        h = {"X-API-Key": raw}
        url = f"/api/v1/quality-rules/generate/{s.id}"

        resp = await client.post(url, headers=h)
        assert resp.status_code == 201
        rules = resp.json()
        # NOT_NULL + TYPE_MATCH + LENGTH_LIMIT + UNIQUE + VALUE_IN_SET
        types = {r["rule_type"] for r in rules}
        assert types == {"NOT_NULL", "TYPE_MATCH", "LENGTH_LIMIT",
                         "UNIQUE", "VALUE_IN_SET"}
        assert len(rules) == 5
        # 幂等：再次生成 → 旧规则删除重建（数量不变）
        resp = await client.post(url, headers=h)
        assert resp.status_code == 201
        assert len(resp.json()) == 5

    async def test_generate_empty_standard(self, client, db_session):
        raw, key = await _key(db_session, "p7b_qr_g")
        s = await _std(db_session, key)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/quality-rules/generate/{s.id}",
            headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert resp.json() == []

    async def test_generate_cross_key_contract(self, client, db_session):
        """行为契约：generate 端点不校验标准归属，任何 key 都可为任一标准生成（生成物归属调用者）。"""
        raw_a, key_a = await _key(db_session, "p7b_qr_h")
        raw_b, key_b = await _key(db_session, "p7b_qr_i")
        s = await _std(db_session, key_a)
        _, f = await _field(db_session, s)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/quality-rules/generate/{s.id}",
            headers={"X-API-Key": raw_b})
        assert resp.status_code == 201
        assert len(resp.json()) == 5
        # a 的列表仍看不到 b 生成的规则（scope 隔离）
        resp = await client.get(
            "/api/v1/quality-rules", headers={"X-API-Key": raw_a})
        assert resp.json() == []