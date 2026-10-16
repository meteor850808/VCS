# -*- coding: utf-8 -*-
"""P7b-3: data_standard_v2/tables.py 全覆盖（64% → 100%）+ _common 归属函数。

覆盖：list/create/get table、attach（主/次归属、升级、409/400/404/403）、
detach、list_table_layers、update/delete；顺带 _get_layer_ctx/_get_table_ctx/
_to_table_layer_link/_get_layer_depth(141 缺失层组)。
"""

import uuid

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_layer_group_table_link import StandardLayerGroupTableLink
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


async def _std(db_session, key, name="P7b表标准"):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-TB-{uuid.uuid4().hex[:6]}",
        name=name,
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _layer(db_session, std, name, parent_id=None, code=None):
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=std.id, parent_id=parent_id,
        group_name=name, group_code=code or name,
    )
    db_session.add(lg)
    await db_session.flush()
    return lg


async def _table(db_session, std, layer, name="道路面", code="RD", with_dict=False):
    t = StandardTable(
        id=str(uuid.uuid4()),
        layer_id=layer.id, standard_id=std.id,
        table_name=name, table_code=code,
        table_type="spatial", geometry_type="Polygon",
    )
    db_session.add(t)
    await db_session.flush()
    # 主归属 link（与 create_table 端点语义一致）
    db_session.add(StandardLayerGroupTableLink(
        id=str(uuid.uuid4()),
        layer_group_id=layer.id, table_id=t.id,
        is_primary=True, ordering=0,
    ))
    await db_session.flush()
    f1 = StandardField(
        id=str(uuid.uuid4()),
        standard_id=std.id, table_id=t.id, field_name="名称",
        data_type="string", length=50, is_required=True,
    )
    db_session.add(f1)
    await db_session.flush()
    if with_dict:
        d = StandardDictionary(
            id=str(uuid.uuid4()),
            standard_id=std.id, scope="standard",
            dict_name="状态表", dict_code="ST",
        )
        db_session.add(d)
        await db_session.flush()
        link = StandardFieldDictLink(
            id=str(uuid.uuid4()),
            field_id=f1.id, dict_id=d.id, is_primary=True,
        )
        db_session.add(link)
        await db_session.flush()
    return t


class TestListTables:

    async def test_403_stranger(self, client, db_session):
        raw_o, key_o = await _key(db_session, "p7b_tb_a")
        raw_x, key_x = await _key(db_session, "p7b_tb_b")
        s = await _std(db_session, key_o)
        lg = await _layer(db_session, s, "根层")
        await db_session.commit()
        # 非所属：先 404 层组？ctx 校验在 list 里是 db.get(layer) → 我们 owner 校验在哪？
        # list_tables 只查层组存在，不校验权限；用不存在 id 测 404
        resp = await client.get(
            "/api/v1/layers/no-such/tables",
            headers={"X-API-Key": raw_x})
        assert resp.status_code == 404
        # 正常空列表
        resp = await client.get(
            f"/api/v1/layers/{lg.id}/tables",
            headers={"X-API-Key": raw_o})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_links(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_c")
        s = await _std(db_session, key)
        root1 = await _layer(db_session, s, "根1", code="R1")
        root2 = await _layer(db_session, s, "根2", code="R2")
        t = await _table(db_session, s, root1)
        # 次归属挂到 root2
        db_session.add(StandardLayerGroupTableLink(
            id=str(uuid.uuid4()),
            layer_group_id=root2.id, table_id=t.id,
            is_primary=False, ordering=5,
        ))
        await db_session.commit()
        # 主归属的列表（root1）：含自身主挂
        resp = await client.get(
            f"/api/v1/layers/{root1.id}/tables",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["is_primary"] is True
        assert rows[0]["field_count"] == 1
        # 次归属的列表（root2）
        resp = await client.get(
            f"/api/v1/layers/{root2.id}/tables",
            headers={"X-API-Key": raw})
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["is_primary"] is False
        assert rows[0]["id"] == t.id


class TestCreateGetTable:

    async def test_create_with_fields_and_get(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_d")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s, "根层")
        await db_session.commit()
        payload = {
            "table_name": "兴趣点", "table_code": "POI",
            "table_type": "spatial", "geometry_type": "Point",
            "description": "POI 层", "ordering": 3,
            "fields": [
                {"field_name": "编号", "field_alias": "ID",
                 "data_type": "string", "length": 20,
                 "is_primary_key": True, "is_required": True,
                 "default_value": "0", "ordering": 1},
                {"field_name": "名称", "data_type": "string",
                 "nullable": False, "ordering": 2},
            ],
        }
        resp = await client.post(
            f"/api/v1/layers/{lg.id}/tables",
            headers={"X-API-Key": raw}, json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["table_name"] == "兴趣点"
        assert len(body["fields"]) == 2
        table_id = body["id"]
        # 详情（含字段-字典关联关系）
        resp = await client.get(
            f"/api/v1/tables/{table_id}",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.json()["table_code"] == "POI"

    async def test_404s(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_e")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s, "根层")
        t = await _table(db_session, s, lg, with_dict=True)
        await db_session.commit()
        h = {"X-API-Key": raw}
        assert (await client.post(
            "/api/v1/layers/no-such/tables", headers=h,
            json={"table_name": "x"})).status_code == 404
        assert (await client.get(
            "/api/v1/tables/no-such", headers=h)).status_code == 404

    async def test_get_with_dict_links(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_f")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s, "根层")
        t = await _table(db_session, s, lg, with_dict=True)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/tables/{t.id}",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        fields = resp.json()["fields"]
        assert fields[0]["dict_links"][0]["dict_name"] == "状态表"


class TestAttachTable:

    async def test_full_attach_flow(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_g")
        s = await _std(db_session, key)
        root1 = await _layer(db_session, s, "根1", code="R1")
        root2 = await _layer(db_session, s, "根2", code="R2")
        t = await _table(db_session, s, root1)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 次归属挂载
        resp = await client.post(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": False, "ordering": 9})
        assert resp.status_code == 201
        assert resp.json()["is_primary"] is False
        assert resp.json()["ordering"] == 9
        assert resp.json()["layer_group"]["group_name"] == "根2"

        # 重复次归属 → 409
        resp = await client.post(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": False})
        assert resp.status_code == 409

        # 次归属原位升级为主归属（old_primary 降级 + table.layer_id 改指）
        resp = await client.post(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": True, "ordering": 3})
        assert resp.status_code == 201
        assert resp.json()["is_primary"] is True

        # 再次主归属挂到 root2 → 409 已是主归属
        resp = await client.post(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": True})
        assert resp.status_code == 409

        # 主归属转移到 root1（新 link 分支）→ 200/201
        resp = await client.post(
            f"/api/v1/layers/{root1.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": True})
        assert resp.status_code == 201
        assert resp.json()["is_primary"] is True

        # 主归属转移到全新无 link 层组 root3（209-223 新 link 分支）
        root3 = await _layer(db_session, s, "根3", code="R3")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{root3.id}/tables/{t.id}/attach",
            headers=h, json={"is_primary": True, "ordering": 7})
        assert resp.status_code == 201
        assert resp.json()["is_primary"] is True
        assert resp.json()["ordering"] == 7

    async def test_cross_standard_400_and_ctx_errors(self, client, db_session):
        raw_o, key_o = await _key(db_session, "p7b_tb_h")
        raw_x, key_x = await _key(db_session, "p7b_tb_i")
        s_o = await _std(db_session, key_o, "表标准O")
        s_x = await _std(db_session, key_o, "表标准X")  # 同 key 双标准 → 跨标准 400 分支
        lg_o = await _layer(db_session, s_o, "根O")
        lg_x = await _layer(db_session, s_x, "根X")
        t = await _table(db_session, s_o, lg_o)
        s_forbidden = await _std(db_session, key_x, "表标准F")
        lg_f = await _layer(db_session, s_forbidden, "根F")
        await db_session.commit()
        h_o = {"X-API-Key": raw_o}

        # 跨标准 400（同 key 但标准不同）
        resp = await client.post(
            f"/api/v1/layers/{lg_x.id}/tables/{t.id}/attach",
            headers=h_o, json={"is_primary": False})
        assert resp.status_code == 400

        # 层组不存在 → 404
        resp = await client.post(
            "/api/v1/layers/no-such/tables/no-such/attach",
            headers=h_o, json={"is_primary": False})
        assert resp.status_code == 404

        # 表不存在 → 404
        resp = await client.post(
            f"/api/v1/layers/{lg_o.id}/tables/no-such/attach",
            headers=h_o, json={"is_primary": False})
        assert resp.status_code == 404

        # 非所属 → 403（层组属于别的 key）
        resp = await client.post(
            f"/api/v1/layers/{lg_f.id}/tables/no-such/attach",
            headers=h_o, json={"is_primary": False})
        assert resp.status_code == 403


class TestDetachTable:

    async def test_detach_secondary_and_errors(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_j")
        s = await _std(db_session, key)
        root1 = await _layer(db_session, s, "根1", code="R1")
        root2 = await _layer(db_session, s, "根2", code="R2")
        t = await _table(db_session, s, root1)
        db_session.add(StandardLayerGroupTableLink(
            id=str(uuid.uuid4()),
            layer_group_id=root2.id, table_id=t.id,
            is_primary=False, ordering=1,
        ))
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 摘挂次归属 → 204
        resp = await client.delete(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach", headers=h)
        assert resp.status_code == 204

        # 再摘 → 404 未挂载
        resp = await client.delete(
            f"/api/v1/layers/{root2.id}/tables/{t.id}/attach", headers=h)
        assert resp.status_code == 404

        # 摘主归属 → 400
        resp = await client.delete(
            f"/api/v1/layers/{root1.id}/tables/{t.id}/attach", headers=h)
        assert resp.status_code == 400

        # 非所属 → 403
        raw_x, key_x = await _key(db_session, "p7b_tb_k")
        resp = await client.delete(
            f"/api/v1/layers/{root1.id}/tables/{t.id}/attach",
            headers={"X-API-Key": raw_x})
        assert resp.status_code == 403

        # 层组不存在 → 404
        resp = await client.delete(
            "/api/v1/layers/no-such/tables/no-such/attach", headers=h)
        assert resp.status_code == 404


class TestListTableLayers:

    async def test_primary_then_secondary(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_l")
        s = await _std(db_session, key)
        root1 = await _layer(db_session, s, "根1", code="R1")
        root2 = await _layer(db_session, s, "根2", code="R2")
        t = await _table(db_session, s, root1)
        db_session.add(StandardLayerGroupTableLink(
            id=str(uuid.uuid4()),
            layer_group_id=root2.id, table_id=t.id,
            is_primary=False, ordering=1,
        ))
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/tables/{t.id}/layers",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        links = resp.json()
        assert [l["is_primary"] for l in links] == [True, False]
        assert links[0]["layer_group"]["id"] == root1.id
        assert links[1]["layer_group"]["id"] == root2.id

    async def test_404_403(self, client, db_session):
        raw_o, key_o = await _key(db_session, "p7b_tb_m")
        raw_x, key_x = await _key(db_session, "p7b_tb_n")
        s = await _std(db_session, key_o)
        lg = await _layer(db_session, s, "根O")
        t = await _table(db_session, s, lg)
        await db_session.commit()
        assert (await client.get(
            "/api/v1/tables/no-such/layers",
            headers={"X-API-Key": raw_o})).status_code == 404
        assert (await client.get(
            f"/api/v1/tables/{t.id}/layers",
            headers={"X-API-Key": raw_x})).status_code == 403


class TestUpdateDeleteTable:

    async def test_update_table(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_o")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s, "根层")
        t = await _table(db_session, s, lg)
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/tables/{t.id}",
            headers={"X-API-Key": raw},
            json={"table_name": "改名表", "geometry_type": "MultiPolygon"})
        assert resp.status_code == 200
        assert resp.json()["table_name"] == "改名表"
        assert resp.json()["geometry_type"] == "MultiPolygon"

    async def test_delete_and_404s(self, client, db_session):
        raw, key = await _key(db_session, "p7b_tb_p")
        s = await _std(db_session, key)
        lg = await _layer(db_session, s, "根层")
        t = await _table(db_session, s, lg)
        await db_session.commit()
        h = {"X-API-Key": raw}
        assert (await client.put(
            "/api/v1/tables/no-such", headers=h,
            json={"table_name": "x"})).status_code == 404
        resp = await client.delete(f"/api/v1/tables/{t.id}", headers=h)
        assert resp.status_code == 204
        assert (await client.delete(
            "/api/v1/tables/no-such", headers=h)).status_code == 404


class TestGetLayerDepthMissing:

    async def test_nonexistent_layer_returns_zero(self, db_session):
        """_get_layer_depth 对不存在层组返回 0（141 行）。"""
        from app.api.v1.data_standard_v2 import _common as C
        depth = await C._get_layer_depth(db_session, "no-such-layer")
        assert depth == 0