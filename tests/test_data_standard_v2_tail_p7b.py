# -*- coding: utf-8 -*-
"""P7b-6: data_standard_v2 fields/field_links/standards 尾巴全覆盖。

fields：create/update/delete 404 + reorder；field_links：create 404×2/update 404/
delete 404；standards：status 过滤、stats 全量、update 404/409、delete 404。
"""

import uuid

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.source_data import StandardVersion
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


async def _std(db_session, key, name="P7b收尾标准", category="custom",
               status="draft"):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-FN-{uuid.uuid4().hex[:6]}",
        name=name, category=category, status=status,
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _layer(db_session, std):
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id,
        group_name="收尾层", group_code="FN",
    )
    db_session.add(lg)
    await db_session.flush()
    return lg


async def _table(db_session, std, lg):
    t = StandardTable(
        id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
        table_name="收尾表", table_code="FN_T", table_type="spatial",
    )
    db_session.add(t)
    await db_session.flush()
    return t


async def _field(db_session, std, t, name="收尾字段"):
    f = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=t.id,
        field_name=name, data_type="string", ordering=0,
    )
    db_session.add(f)
    await db_session.flush()
    return f


async def _dict(db_session, std=None, key=None, name="收尾字典"):
    d = StandardDictionary(
        id=str(uuid.uuid4()),
        standard_id=std.id if std else None,
        api_key_id=std.api_key_id if std else key.id,
        dict_name=name, scope="standard" if std else "global",
    )
    db_session.add(d)
    await db_session.flush()
    db_session.add(DictionaryItem(
        id=str(uuid.uuid4()), dict_id=d.id, item_code="A", item_name="甲"))
    await db_session.flush()
    return d


class TestFieldsTail:

    async def test_404s_and_reorder(self, client, db_session):
        raw, key = await _key(db_session, "p7b_fn_a")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s)
        t = await _table(db_session, s, lg)
        f1 = await _field(db_session, s, t, name="字段一")
        f2 = await _field(db_session, s, t, name="字段二")
        await db_session.commit()
        h = {"X-API-Key": raw}
        # create 404（表不存在）
        assert (await client.post(
            "/api/v1/tables/no-such/fields", headers=h,
            json={"field_name": "x"})).status_code == 404
        # update 404 / delete 404
        assert (await client.put(
            "/api/v1/fields/no-such", headers=h,
            json={"field_name": "x"})).status_code == 404
        assert (await client.delete(
            "/api/v1/fields/no-such", headers=h)).status_code == 404
        # reorder：全部行（含跨表忽略）
        resp = await client.post(
            f"/api/v1/tables/{t.id}/fields/reorder", headers=h,
            json={"field_ids": [f2.id, f1.id, str(uuid.uuid4())]})
        assert resp.status_code == 204
        # 列表验证顺序翻转
        resp = await client.get(
            f"/api/v1/tables/{t.id}/fields", headers=h)
        assert [f["id"] for f in resp.json()] == [f2.id, f1.id]
        # reorder 后字段已落库（f2 在 f1 前）
        f1r = await db_session.get(StandardField, f1.id)
        f2r = await db_session.get(StandardField, f2.id)
        await db_session.refresh(f1r)
        await db_session.refresh(f2r)
        assert f2r.ordering < f1r.ordering


class TestFieldLinksTail:

    async def test_create_update_delete_links(self, client, db_session):
        raw, key = await _key(db_session, "p7b_fn_b")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s)
        t = await _table(db_session, s, lg)
        f = await _field(db_session, s, t)
        d = await _dict(db_session, s)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # create：字段 404、字典 404
        assert (await client.post(
            "/api/v1/fields/no-such/dict-links", headers=h,
            json={"dict_id": d.id})).status_code == 404
        assert (await client.post(
            f"/api/v1/fields/{f.id}/dict-links", headers=h,
            json={"dict_id": str(uuid.uuid4())})).status_code == 404
        # create happy
        resp = await client.post(
            f"/api/v1/fields/{f.id}/dict-links", headers=h,
            json={"dict_id": d.id, "is_primary": True,
                  "filter_condition": "A"})
        assert resp.status_code == 201
        link = resp.json()
        assert link["dict_name"] == "收尾字典"
        link_id = link["id"]
        # update happy + 404
        resp = await client.put(
            f"/api/v1/field-dict-links/{link_id}", headers=h,
            json={"is_primary": False, "filter_condition": "B"})
        assert resp.status_code == 200
        assert resp.json()["is_primary"] is False
        assert (await client.put(
            "/api/v1/field-dict-links/no-such", headers=h,
            json={"is_primary": False})).status_code == 404
        # list happy
        resp = await client.get(
            f"/api/v1/fields/{f.id}/dict-links", headers=h)
        assert len(resp.json()) == 1
        # delete happy + 404
        assert (await client.delete(
            f"/api/v1/field-dict-links/{link_id}", headers=h)).status_code == 204
        assert (await client.delete(
            "/api/v1/field-dict-links/no-such", headers=h)).status_code == 404


class TestStandardsTail:

    async def test_stats_full(self, client, db_session):
        raw, key = await _key(db_session, "p7b_fn_c")
        s1 = await _std(db_session, key, name="统计甲", category="规划",
                        status="published")
        s2 = await _std(db_session, key, name="统计乙", category="规划",
                        status="draft")
        lg = await _layer(db_session, s1)
        t = await _table(db_session, s1, lg)
        f = await _field(db_session, s1, t)
        d = await _dict(db_session, s2)
        g = await _dict(db_session, key=key, name="全局统计")
        db_session.add(StandardVersion(
            id=str(uuid.uuid4()), standard_id=s1.id, api_key_id=key.id,
            version_number=1,
            snapshot_data={"standard": {"version": 1}}))
        await db_session.commit()
        resp = await client.get(
            "/api/v1/data-standards-v2/stats",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        # 统计为全局汇总（共享测试库），故用 ≥ 验证管道与 scope 隔离
        assert body["standards_total"] >= 2
        assert body["by_category"].get("规划", 0) >= 2
        assert body["by_status"].get("published", 0) >= 1
        assert body["by_status"].get("draft", 0) >= 1
        assert body["layers_total"] >= 1
        assert body["tables_total"] >= 1
        assert body["fields_total"] >= 1
        assert body["global_dicts"] >= 1
        assert body["standard_dicts"] >= 1
        assert body["dict_items_total"] >= 2
        assert body["versions_total"] >= 1
        assert set(body.keys()) == {
            "standards_total", "by_category", "by_status",
            "layers_total", "tables_total", "fields_total",
            "global_dicts", "standard_dicts", "dict_items_total",
            "versions_total",
        }

    async def test_filters_pagination(self, client, db_session):
        raw, key = await _key(db_session, "p7b_fn_d")
        await _std(db_session, key, name="筛选草稿", status="draft")
        await _std(db_session, key, name="筛选发布", status="published")
        await db_session.commit()
        h = {"X-API-Key": raw}
        resp = await client.get(
            "/api/v1/data-standards-v2?status=published", headers=h)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["name"] == "筛选发布"
        resp = await client.get(
            "/api/v1/data-standards-v2?category=none&keyword=不存在的", headers=h)
        assert resp.json()["total"] == 0
        resp = await client.get(
            "/api/v1/data-standards-v2?page=1&page_size=1", headers=h)
        assert resp.json()["total"] == 2
        assert len(resp.json()["items"]) == 1

    async def test_update_404_409_delete_404(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_fn_e")
        raw_b, key_b = await _key(db_session, "p7b_fn_f")
        s = await _std(db_session, key_a, name="冲突源")
        await db_session.commit()
        h_a = {"X-API-Key": raw_a}
        # 不存在 + 非所属 404
        assert (await client.put(
            "/api/v1/data-standards-v2/no-such", headers=h_a,
            json={"name": "x"})).status_code == 404
        assert (await client.put(
            f"/api/v1/data-standards-v2/{s.id}",
            headers={"X-API-Key": raw_b},
            json={"name": "x"})).status_code == 404
        # 更新冲突 409：改名撞已有 code 唯一约束
        s2 = await _std(db_session, key_a, name="另一标准")
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/data-standards-v2/{s.id}", headers=h_a,
            json={"code": s2.code})
        assert resp.status_code == 409
        # 删除不存在 404 + 自己 204
        assert (await client.delete(
            "/api/v1/data-standards-v2/no-such", headers=h_a)).status_code == 404
        assert (await client.delete(
            f"/api/v1/data-standards-v2/{s2.id}", headers=h_a)).status_code == 204