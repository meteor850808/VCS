# -*- coding: utf-8 -*-
"""P5f: source_data.py 路由级测试 — 源数据表/字典/字段-字典关联 CRUD。

背景（TDD 红用例先行）：
- 探针实测修复前 `GET|POST /source-dictionaries/*` 直接 500：
  `type object 'SourceDictionary' has no attribute 'items'`（selectinload 失效）
- list 的 item_count 恒 0（DictionaryItem.dict_id 指向 standard_dictionaries，
  旧表 source_dictionaries 查询永远关联不上）
- 根因：v2.1「迁移后将被 StandardDictionary 替代」的半迁移遗留，
  source_data.py 字典端点仍引用旧模型 SourceDictionary。
  已按迁移意图修复：字典端点改操作 StandardDictionary（本文件按修复后语义断言）。
- P5f 补充修复（本轮在红用例驱动下发现）：
  1) FieldDictLink.dict_id FK 仍指向 source_dictionaries → 关联必然外键违约，
     已改指向 standard_dictionaries.id；
  2) add_source_field 返回未加载 dict_links 的对象 → 响应序列化 MissingGreenlet，
     已改为 commit 后重查 + selectinload（与 get/update 一致）；
  3) DELETE /field-dict-links/{link_id} 被 data_standard_v2 同名路由遮蔽
     （v2 先注册）→ HTTP 层永远到不了旧 unlink_field_dict。
     测试固化该 API 事实（test_unlink_mask_by_v2），旧实现以函数级
     test_unlink_legacy_direct/_not_found 覆盖其行。
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings  # noqa: F401  (确保配置加载)
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.dictionary_item import DictionaryItem
from app.models.source_data import SourceTable, SourceField, FieldDictLink
from app.models.standard_dictionary import StandardDictionary


async def _autouse_test_db(test_db):
    yield


async def _create_key(db_session, name):
    raw, hashed = generate_api_key()
    key = ApiKey(id=str(uuid.uuid4()), key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.commit()
    return raw, key


def _make_table(db, key, name="要素表", code="ST-001", **kw):
    t = SourceTable(
        id=str(uuid.uuid4()), api_key_id=key.id,
        table_name=name, table_code=code,
        **kw,
    )
    db.add(t)
    return t


def _make_field(db, table, name="fd", type_="属性", dtype="string", **kw):
    f = SourceField(
        id=str(uuid.uuid4()), table_id=table.id,
        field_name=name, field_type=type_, data_type=dtype,
        **kw,
    )
    db.add(f)
    return f


def _make_dict(db, key, name="地类代码", code="DICT-1", is_system=False):
    d = StandardDictionary(
        id=str(uuid.uuid4()), api_key_id=key.id,
        dict_name=name, dict_code=code, is_system=is_system,
    )
    db.add(d)
    return d


def _make_item(db, d, code="01", name="耕地"):
    i = DictionaryItem(
        id=str(uuid.uuid4()), dict_id=d.id,
        item_code=code, item_name=name,
    )
    db.add(i)
    return i


def _link_setup(db, key):
    """构造 field + dict（同 key），供关联端点使用。"""
    t = SourceTable(id=str(uuid.uuid4()), api_key_id=key.id, table_name="T")
    db.add(t)
    f = SourceField(id=str(uuid.uuid4()), table_id=t.id, field_name="code")
    d = StandardDictionary(id=str(uuid.uuid4()), api_key_id=key.id, dict_name="D")
    db.add(f); db.add(d)
    return f, d


# ═══════════════════════════════════════════════════════════════
# 源数据表
# ═══════════════════════════════════════════════════════════════

class TestListSourceTables:
    async def test_list_with_field_count(self, client, db_session):
        raw, key = await _create_key(db_session, "src_list")
        t1 = _make_table(db_session, key, "道路", "RD")
        _make_field(db_session, t1, "code"); _make_field(db_session, t1, "name")
        _make_table(db_session, key, "地块", "PL", ordering=0)
        await db_session.commit()

        resp = await client.get("/api/v1/source-tables", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 2  # session 级共享库可能含他类残留行 → 只断言本测试数据
        by_code = {i["table_code"]: i for i in items}
        assert by_code["RD"]["field_count"] == 2
        assert by_code["PL"]["field_count"] == 0
        assert by_code["PL"]["created_at"]  # brief 含 created_at

    async def test_list_search_name(self, client, db_session):
        raw, key = await _create_key(db_session, "src_search_n")
        _make_table(db_session, key, "道路要素", "RD")
        _make_table(db_session, key, "地块要素", "PL")
        await db_session.commit()

        resp = await client.get("/api/v1/source-tables?search=道路",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert [i["table_name"] for i in resp.json()] == ["道路要素"]

    async def test_list_search_code_ilike(self, client, db_session):
        raw, key = await _create_key(db_session, "src_search_c")
        _make_table(db_session, key, "A", "abc-01")
        await db_session.commit()

        resp = await client.get("/api/v1/source-tables?search=ABC",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert len(resp.json()) == 1  # ilike 大小写不敏感

    async def test_list_search_no_match(self, client, db_session):
        raw, key = await _create_key(db_session, "src_search_z")
        _make_table(db_session, key, "A", "AA")
        await db_session.commit()
        resp = await client.get("/api/v1/source-tables?search=zzz",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200 and resp.json() == []

    async def test_list_scope_isolation(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "src_scope_a")
        raw_b, _ = await _create_key(db_session, "src_scope_b")
        _make_table(db_session, key_a, "A表")
        await db_session.commit()

        resp = await client.get("/api/v1/source-tables", headers={"X-API-Key": raw_b})
        assert resp.status_code == 200
        assert not any(i["table_name"] == "A表" for i in resp.json())  # 越权不可见


class TestGetSourceTable:
    async def test_detail_with_fields(self, client, db_session):
        raw, key = await _create_key(db_session, "src_get")
        t = _make_table(db_session, key, "道路", "RD")
        _make_field(db_session, t, "code", is_primary_key=True, ordering=0)
        _make_field(db_session, t, "name", is_required=True, ordering=1)
        await db_session.commit()

        resp = await client.get(f"/api/v1/source-tables/{t.id}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["table_name"] == "道路"
        assert [f["field_name"] for f in body["fields"]] == ["code", "name"]
        assert body["fields"][0]["is_primary_key"] is True
        assert "dict_links" in body["fields"][0]

    async def test_detail_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_get_404")
        resp = await client.get(f"/api/v1/source-tables/{uuid.uuid4()}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_detail_other_key_forbidden(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "src_get_oa")
        raw_b, _ = await _create_key(db_session, "src_get_ob")
        t = _make_table(db_session, key_a, "A表")
        await db_session.commit()
        resp = await client.get(f"/api/v1/source-tables/{t.id}",
                                headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestCreateSourceTable:
    async def test_create_with_fields(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_post")
        body = {
            "table_name": "境界",
            "table_code": "BND",
            "geometry_type": "Polygon",
            "source": "国标",
            "version": "GB/T 13923-2022",
            "fields": [
                {"field_name": "code", "data_type": "string", "is_primary_key": True},
                {"field_name": "name", "data_type": "string", "is_required": True},
            ],
        }
        resp = await client.post("/api/v1/source-tables", json=body,
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 201
        out = resp.json()
        assert out["table_name"] == "境界"
        # ordering 未传时按序号降级
        assert [f["ordering"] for f in out["fields"]] == [0, 1]
        cnt = (await db_session.execute(
            select(func.count()).select_from(SourceField).where(
                SourceField.table_id == out["id"]))).scalar()
        assert cnt == 2

    async def test_create_without_fields(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_post_nf")
        resp = await client.post("/api/v1/source-tables", json={"table_name": "裸表"},
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert resp.json()["fields"] == []


class TestUpdateSourceTable:
    async def test_update_partial(self, client, db_session):
        raw, key = await _create_key(db_session, "src_put")
        t = _make_table(db_session, key, "旧名", "OLD")
        await db_session.commit()

        resp = await client.put(
            f"/api/v1/source-tables/{t.id}",
            json={"table_name": "新名", "is_active": False, "ordering": 5},
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        out = resp.json()
        assert out["table_name"] == "新名"
        assert out["is_active"] is False and out["ordering"] == 5
        # 未传字段保持原值
        assert out["table_code"] == "OLD"

    async def test_update_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_put_404")
        resp = await client.put(f"/api/v1/source-tables/{uuid.uuid4()}",
                                json={"table_name": "x"},
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_update_other_key_forbidden(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "src_put_oa")
        raw_b, _ = await _create_key(db_session, "src_put_ob")
        t = _make_table(db_session, key_a, "A表")
        await db_session.commit()
        resp = await client.put(f"/api/v1/source-tables/{t.id}",
                                json={"table_name": "改"},
                                headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestDeleteSourceTable:
    async def test_delete_cascade_fields(self, client, db_session):
        raw, key = await _create_key(db_session, "src_del")
        t = _make_table(db_session, key, "删")
        _make_field(db_session, t, "f1")
        await db_session.commit()

        resp = await client.delete(f"/api/v1/source-tables/{t.id}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 204
        cnt = (await db_session.execute(
            select(func.count()).select_from(SourceTable).where(
                SourceTable.id == t.id))).scalar()
        fc = (await db_session.execute(
            select(func.count()).select_from(SourceField).where(
                SourceField.table_id == t.id))).scalar()
        assert cnt == 0 and fc == 0  # 级联删除字段

    async def test_delete_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_del_404")
        resp = await client.delete(f"/api/v1/source-tables/{uuid.uuid4()}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_delete_other_key_forbidden(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "src_del_oa")
        raw_b, _ = await _create_key(db_session, "src_del_ob")
        t = _make_table(db_session, key_a, "A表")
        await db_session.commit()
        resp = await client.delete(f"/api/v1/source-tables/{t.id}",
                                   headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestAddField:
    async def test_add_field(self, client, db_session):
        raw, key = await _create_key(db_session, "src_field")
        t = _make_table(db_session, key, "T")
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/source-tables/{t.id}/fields",
            json={"field_name": "geom", "data_type": "geometry",
                  "field_type": "几何", "is_required": True},
            headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert resp.json()["field_name"] == "geom"
        cnt = (await db_session.execute(
            select(func.count()).select_from(SourceField).where(
                SourceField.table_id == t.id))).scalar()
        assert cnt == 1

    async def test_add_field_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "src_field_404")
        resp = await client.post(f"/api/v1/source-tables/{uuid.uuid4()}/fields",
                                 json={"field_name": "x"},
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_add_field_other_key_forbidden(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "src_field_oa")
        raw_b, _ = await _create_key(db_session, "src_field_ob")
        t = _make_table(db_session, key_a, "A表")
        await db_session.commit()
        resp = await client.post(f"/api/v1/source-tables/{t.id}/fields",
                                 json={"field_name": "x"},
                                 headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# 源数据字典（P5f 修复：操作 StandardDictionary）
# ═══════════════════════════════════════════════════════════════

class TestListDictionaries:
    async def test_list_with_item_count(self, client, db_session):
        raw, key = await _create_key(db_session, "dict_list")
        d1 = _make_dict(db_session, key, "地类代码")
        _make_item(db_session, d1, "01"); _make_item(db_session, d1, "02")
        _make_dict(db_session, key, "权属代码")
        await db_session.commit()

        resp = await client.get("/api/v1/source-dictionaries", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        # 系统字典（is_system=True）对所有 key 可见，过滤后再断言私有字典集
        items = {i["dict_name"]: i for i in resp.json() if not i["is_system"]}
        assert len(items) == 2
        assert items["地类代码"]["item_count"] == 2  # 修复后真值（迁移前恒 0）
        assert items["权属代码"]["item_count"] == 0

    async def test_list_search(self, client, db_session):
        raw, key = await _create_key(db_session, "dict_search")
        _make_dict(db_session, key, "地类代码")
        _make_dict(db_session, key, "权属代码")
        await db_session.commit()
        resp = await client.get("/api/v1/source-dictionaries?search=权属",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert [i["dict_name"] for i in resp.json()] == ["权属代码"]

    async def test_list_system_visible_to_others(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "dict_sys_a")
        raw_b, _ = await _create_key(db_session, "dict_sys_b")
        _make_dict(db_session, key_a, "系统预置", is_system=True)
        _make_dict(db_session, key_a, "私有字典")
        await db_session.commit()

        resp = await client.get("/api/v1/source-dictionaries", headers={"X-API-Key": raw_b})
        assert resp.status_code == 200
        names = [i["dict_name"] for i in resp.json()]
        assert "系统预置" in names and "私有字典" not in names


class TestGetDictionary:
    async def test_detail_with_items(self, client, db_session):
        raw, key = await _create_key(db_session, "dict_get")
        d = _make_dict(db_session, key, "地类代码")
        _make_item(db_session, d, "01", "耕地"); _make_item(db_session, d, "02", "园地")
        await db_session.commit()

        resp = await client.get(f"/api/v1/source-dictionaries/{d.id}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        out = resp.json()
        assert out["is_system"] is False
        assert [(i["item_code"], i["item_name"]) for i in out["items"]] == [
            ("01", "耕地"), ("02", "园地")]

    async def test_detail_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "dict_get_404")
        resp = await client.get(f"/api/v1/source-dictionaries/{uuid.uuid4()}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_detail_private_forbidden_other_key(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "dict_get_oa")
        raw_b, _ = await _create_key(db_session, "dict_get_ob")
        d = _make_dict(db_session, key_a, "私有")
        await db_session.commit()
        resp = await client.get(f"/api/v1/source-dictionaries/{d.id}",
                                headers={"X-API-Key": raw_b})
        assert resp.status_code == 403

    async def test_detail_system_readable_other_key(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "dict_get_sa")
        raw_b, _ = await _create_key(db_session, "dict_get_sb")
        d = _make_dict(db_session, key_a, "系统", is_system=True)
        await db_session.commit()
        resp = await client.get(f"/api/v1/source-dictionaries/{d.id}",
                                headers={"X-API-Key": raw_b})
        assert resp.status_code == 200


class TestCreateDictionary:
    async def test_create_with_items(self, client, db_session):
        raw, _ = await _create_key(db_session, "dict_post")
        resp = await client.post(
            "/api/v1/source-dictionaries",
            json={"dict_name": "地类代码", "dict_code": "D1",
                  "items": [{"item_code": "01", "item_name": "耕地"}]},
            headers={"X-API-Key": raw})
        assert resp.status_code == 201
        out = resp.json()
        assert out["dict_name"] == "地类代码"
        assert len(out["items"]) == 1 and out["items"][0]["item_name"] == "耕地"
        cnt = (await db_session.execute(
            select(func.count()).select_from(DictionaryItem).where(
                DictionaryItem.dict_id == out["id"]))).scalar()
        assert cnt == 1

    async def test_create_without_items(self, client, db_session):
        raw, _ = await _create_key(db_session, "dict_post_ni")
        resp = await client.post("/api/v1/source-dictionaries",
                                 json={"dict_name": "空字典"},
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert resp.json()["items"] == []


class TestAddDictItems:
    async def test_add_items_batch(self, client, db_session):
        raw, key = await _create_key(db_session, "dict_items")
        d = _make_dict(db_session, key, "地类")
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/source-dictionaries/{d.id}/items",
            json=[{"item_code": "01", "item_name": "耕地"},
                  {"item_code": "02", "item_name": "园地"}],
            headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert len(resp.json()) == 2
        cnt = (await db_session.execute(
            select(func.count()).select_from(DictionaryItem).where(
                DictionaryItem.dict_id == d.id))).scalar()
        assert cnt == 2

    async def test_add_items_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "dict_items_404")
        resp = await client.post(f"/api/v1/source-dictionaries/{uuid.uuid4()}/items",
                                 json=[{"item_code": "01", "item_name": "x"}],
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_add_items_private_forbidden(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "dict_items_oa")
        raw_b, _ = await _create_key(db_session, "dict_items_ob")
        d = _make_dict(db_session, key_a, "私有")
        await db_session.commit()
        resp = await client.post(f"/api/v1/source-dictionaries/{d.id}/items",
                                 json=[{"item_code": "01", "item_name": "x"}],
                                 headers={"X-API-Key": raw_b})
        assert resp.status_code == 403


class TestDeleteDictionary:
    async def test_delete_cascade_items(self, client, db_session):
        raw, key = await _create_key(db_session, "dict_del")
        d = _make_dict(db_session, key, "删")
        _make_item(db_session, d, "01")
        await db_session.commit()

        resp = await client.delete(f"/api/v1/source-dictionaries/{d.id}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 204
        dc = (await db_session.execute(
            select(func.count()).select_from(StandardDictionary).where(
                StandardDictionary.id == d.id))).scalar()
        ic = (await db_session.execute(
            select(func.count()).select_from(DictionaryItem).where(
                DictionaryItem.dict_id == d.id))).scalar()
        assert dc == 0 and ic == 0  # 级联删除条目

    async def test_delete_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "dict_del_404")
        resp = await client.delete(f"/api/v1/source-dictionaries/{uuid.uuid4()}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_delete_other_key_404(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "dict_del_oa")
        raw_b, _ = await _create_key(db_session, "dict_del_ob")
        d = _make_dict(db_session, key_a, "私有")
        await db_session.commit()
        resp = await client.delete(f"/api/v1/source-dictionaries/{d.id}",
                                   headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# 字段-字典关联
# ═══════════════════════════════════════════════════════════════

class TestFieldDictLinks:
    async def test_link(self, client, db_session):
        raw, key = await _create_key(db_session, "link_ok")
        f, d = _link_setup(db_session, key)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/field-dict-links",
            params={"field_id": f.id, "dict_id": d.id, "filter_condition": "a=1"},
            headers={"X-API-Key": raw})
        assert resp.status_code == 201
        assert resp.json() == {"message": "关联成功"}
        link = (await db_session.execute(
            select(FieldDictLink).where(FieldDictLink.field_id == f.id))).scalar_one()
        assert link.dict_id == d.id and link.filter_condition == "a=1"

    async def test_link_field_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "link_404")
        resp = await client.post("/api/v1/field-dict-links",
                                 params={"field_id": str(uuid.uuid4()),
                                         "dict_id": str(uuid.uuid4())},
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_unlink_mask_by_v2(self, client, db_session):
        """路由冲突固化:data_standard_v2 在 source_data 之前注册同名 DELETE,
        HTTP 实际到达 v2 delete_field_dict_link(查询 standard_field_dict_links),
        旧表 field_dict_links 的行不会被删除。自 router.py 注释『v2 路由必须在
        source_data 之前注册』起即如此,此测试记录当前 API 事实(见 ADR-0001 P5f)。"""
        raw, key = await _create_key(db_session, "unlink_masked")
        f, d = _link_setup(db_session, key)
        link = FieldDictLink(id=str(uuid.uuid4()), field_id=f.id, dict_id=d.id)
        db_session.add(link)
        await db_session.commit()

        resp = await client.delete(f"/api/v1/field-dict-links/{link.id}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404  # v2 视角:standard_field_dict_links 无此行
        cnt = (await db_session.execute(
            select(func.count()).select_from(FieldDictLink).where(
                FieldDictLink.id == link.id))).scalar()
        assert cnt == 1  # 旧表行未被动过

    async def test_unlink_legacy_direct(self, db_session):
        """函数级调用被遮蔽的旧实现 unlink_field_dict,覆盖其 DELETE 行。"""
        from app.api.v1.source_data import unlink_field_dict
        raw, key = await _create_key(db_session, "unlink_direct")
        f, d = _link_setup(db_session, key)
        link = FieldDictLink(id=str(uuid.uuid4()), field_id=f.id, dict_id=d.id)
        db_session.add(link)
        await db_session.commit()

        await unlink_field_dict(link_id=link.id, db=db_session, api_key=key)
        cnt = (await db_session.execute(
            select(func.count()).select_from(FieldDictLink).where(
                FieldDictLink.id == link.id))).scalar()
        assert cnt == 0

    async def test_unlink_legacy_not_found(self, db_session):
        from app.api.v1.source_data import unlink_field_dict
        raw, key = await _create_key(db_session, "unlink_direct404")
        with pytest.raises(HTTPException) as ei:
            await unlink_field_dict(link_id=str(uuid.uuid4()),
                                    db=db_session, api_key=key)
        assert ei.value.status_code == 404

    async def test_unlink_not_found(self, client, db_session):
        """HTTP 层后续 404 由 v2 delete_field_dict_link 兜底,语义一致。"""
        raw, _ = await _create_key(db_session, "unlink_404")
        resp = await client.delete(f"/api/v1/field-dict-links/{uuid.uuid4()}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404