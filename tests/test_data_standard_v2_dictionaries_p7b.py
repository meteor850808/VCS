# -*- coding: utf-8 -*-
"""P7b-4: data_standard_v2/dictionaries.py 全覆盖（71% → 100%）。

标准级/全局字典 CRUD、条目批量增改删、共享、409 冲突（预查重 + IntegrityError
兜底并发分支）、400 scope/系统字典保护。
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.standard_dictionary import StandardDictionary


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


async def _key(db_session, name):
    raw, hashed = generate_api_key()
    k = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(k)
    await db_session.commit()
    return raw, k


async def _std(db_session, key, name="P7b字典标准"):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-DC-{uuid.uuid4().hex[:6]}",
        name=name,
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _dict(db_session, std=None, name="材质", key=None, items=None):
    d = StandardDictionary(
        id=str(uuid.uuid4()),
        standard_id=std.id if std else None,
        api_key_id=std.api_key_id if std else (key.id if key else "test"),
        dict_name=name, dict_code=name[:2].upper() if len(name) >= 2 else "X",
        scope="standard" if std else "global",
    )
    db_session.add(d)
    await db_session.flush()
    for it in items or [("BR", "砖"), ("ST", "石")]:
        db_session.add(DictionaryItem(
            id=str(uuid.uuid4()),
            dict_id=d.id, item_code=it[0], item_name=it[1],
        ))
    await db_session.flush()
    return d


class TestStandardDictionaries:

    async def test_happy_flow(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_a")
        s = await _std(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 创建（带条目，parent_code 层级）
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries",
            headers=h,
            json={"dict_name": "道路等级", "dict_code": "RD_LVL",
                  "description": "等级", "scope": "standard",
                  "items": [
                      {"item_code": "A", "item_name": "高速",
                       "parent_code": None, "description": "x", "ordering": 1},
                      {"item_code": "B", "item_name": "国道",
                       "parent_code": "A", "ordering": 2},
                  ]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["dict_name"] == "道路等级"
        assert len(body["items"]) == 2
        did = body["id"]
        # 列表
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries", headers=h)
        assert resp.status_code == 200
        assert resp.json()[0]["item_count"] == 2
        # 详情
        resp = await client.get(f"/api/v1/dictionaries/{did}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["dict_code"] == "RD_LVL"

    async def test_errors(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_b")
        raw_x, key_x = await _key(db_session, "p7b_dc_c")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质")
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 标准不存在（不属于 + 不存在）
        assert (await client.post(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries",
            headers={"X-API-Key": raw_x}, json={"dict_name": "x"})).status_code == 404
        assert (await client.post(
            "/api/v1/data-standards-v2/no-such/dictionaries",
            headers=h, json={"dict_name": "x"})).status_code == 404
        # scope 非法 400
        assert (await client.post(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries",
            headers=h, json={"dict_name": "y", "scope": "global"})).status_code == 400
        # 预查重 409
        assert (await client.post(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries",
            headers=h, json={"dict_name": "材质"})).status_code == 409

    async def test_duplicate_race_integrity(self, client, db_session):
        """并发同名创建：一个成功，另一个走 IntegrityError 兜底 409。"""
        raw, key = await _key(db_session, "p7b_dc_d")
        s = await _std(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        url = f"/api/v1/data-standards-v2/{s.id}/dictionaries"

        async def _create():
            return await client.post(
                url, headers=h, json={"dict_name": "并发字典"})

        r1, r2 = await asyncio.gather(_create(), _create())
        codes = sorted([r1.status_code, r2.status_code])
        assert codes[0] == 201
        assert codes[1] == 409

    async def test_update_scope_rules(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_e")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质")
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 改名
        resp = await client.put(
            f"/api/v1/dictionaries/{d.id}", headers=h,
            json={"dict_name": "材质V2", "description": "更新"})
        assert resp.status_code == 200
        assert resp.json()["dict_name"] == "材质V2"
        # scope → global：standard_id 置 NULL
        resp = await client.put(
            f"/api/v1/dictionaries/{d.id}", headers=h, json={"scope": "global"})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "global"
        # global → standard：standard_id 为 NULL → 400
        resp = await client.put(
            f"/api/v1/dictionaries/{d.id}", headers=h, json={"scope": "standard"})
        assert resp.status_code == 400

    async def test_delete_rules(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_f")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质")
        sys_d = StandardDictionary(
            id=str(uuid.uuid4()),
            standard_id=None, api_key_id=key.id,
            dict_name="系统字典", scope="global", is_system=True,
        )
        db_session.add(sys_d)
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 系统字典不可删 400
        assert (await client.delete(
            f"/api/v1/dictionaries/{sys_d.id}", headers=h)).status_code == 400
        # 正常删除 204
        assert (await client.delete(
            f"/api/v1/dictionaries/{d.id}", headers=h)).status_code == 204
        # 再删 404
        assert (await client.delete(
            f"/api/v1/dictionaries/{d.id}", headers=h)).status_code == 404


class TestGlobalDictionaries:

    async def test_create_list_detail(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_g")
        s = await _std(db_session, key)
        g = await _dict(db_session, name="全局路况", key=key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        # 创建全局字典
        resp = await client.post(
            "/api/v1/dictionaries", headers=h,
            json={"dict_name": "全局材质", "dict_code": "G_CZ",
                  "scope": "global", "items": [{"item_code": "BL", "item_name": "蓝"}]})
        assert resp.status_code == 201
        assert resp.json()["standard_id"] is None
        # 列表（无 keyword）+ keyword 过滤
        resp = await client.get("/api/v1/dictionaries", headers=h)
        assert resp.status_code == 200
        assert any(d["dict_name"] == "全局材质" for d in resp.json())
        resp = await client.get("/api/v1/dictionaries?keyword=材质", headers=h)
        names = [d["dict_name"] for d in resp.json()]
        assert "全局材质" in names and "全局路况" not in names
        # 详情
        resp = await client.get(f"/api/v1/dictionaries/{g.id}", headers=h)
        assert resp.status_code == 200
        # 详情 404 + 更新 404
        assert (await client.get(
            "/api/v1/dictionaries/no-such", headers=h)).status_code == 404
        assert (await client.put(
            "/api/v1/dictionaries/no-such", headers=h,
            json={"dict_name": "x"})).status_code == 404

    async def test_errors(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_h")
        await _dict(db_session, name="独名全局", key=key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        assert (await client.post(
            "/api/v1/dictionaries", headers=h,
            json={"dict_name": "x", "scope": "standard"})).status_code == 400
        # scope 默认 "standard"；显式 global 才走预查重
        assert (await client.post(
            "/api/v1/dictionaries", headers=h,
            json={"dict_name": "独名全局", "scope": "global"})).status_code == 409


class TestDictItems:

    async def test_items_crud(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_i")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质", items=[("BR", "砖")])
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 批量添加
        resp = await client.post(
            f"/api/v1/dictionaries/{d.id}/items", headers=h,
            json=[
                {"item_code": "ST", "item_name": "石", "parent_code": "BR",
                 "description": "d", "ordering": 2},
                {"item_code": "WO", "item_name": "木", "ordering": 3},
            ])
        assert resp.status_code == 201
        added = resp.json()
        assert len(added) == 2
        st_id = added[0]["id"]

        # 批量更新（id 命中更新 / 未知 id 忽略 / 跨字典 id 忽略）
        resp = await client.put(
            f"/api/v1/dictionaries/{d.id}/items", headers=h,
            json=[
                {"id": st_id, "item_name": "石V2", "ordering": 9},
                {"id": str(uuid.uuid4()), "item_name": "幽灵"},
            ])
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["item_name"] == "石V2"

        # 删除条目
        assert (await client.delete(
            f"/api/v1/dictionaries/{d.id}/items/{added[1]['id']}",
            headers=h)).status_code == 204
        # 不存在的条目/跨字典 404
        assert (await client.delete(
            f"/api/v1/dictionaries/{d.id}/items/no-such",
            headers=h)).status_code == 404

    async def test_items_404s(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_j")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质")
        await db_session.commit()
        h = {"X-API-Key": raw}
        assert (await client.post(
            f"/api/v1/dictionaries/{d.id}/items", headers=h,
            json=[{"item_code": "A", "item_name": "甲"}])).status_code == 201
        assert (await client.post(
            "/api/v1/dictionaries/no-such/items", headers=h,
            json=[{"item_code": "A", "item_name": "甲"}])).status_code == 404
        assert (await client.put(
            "/api/v1/dictionaries/no-such/items", headers=h,
            json=[])).status_code == 404
        assert (await client.delete(
            "/api/v1/dictionaries/no-such/items/xyz", headers=h)).status_code == 404


class TestShareDictionary:

    async def test_share_and_404(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dc_k")
        s = await _std(db_session, key)
        d = await _dict(db_session, s, name="材质")
        await db_session.commit()
        h = {"X-API-Key": raw}
        resp = await client.post(
            f"/api/v1/dictionaries/{d.id}/share", headers=h)
        assert resp.status_code == 200
        assert resp.json()["scope"] == "global"
        assert resp.json()["standard_id"] is None
        assert (await client.post(
            "/api/v1/dictionaries/no-such/share", headers=h)).status_code == 404