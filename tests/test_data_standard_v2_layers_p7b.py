# -*- coding: utf-8 -*-
"""P7b-1: data_standard_v2/layers.py 路由全覆盖（51% → 100%）。

补 404/403/深度守卫/防环/共享表守卫/递归复制 分支；顺带覆盖 _common.py
的 _layer_subtree_height / _find_shared_primary_tables / _copy_layer_recursive。
"""

import uuid

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
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


async def _seed_std(db_session, key, name="P7b层组标准", code=None):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=code or f"ST-P7B-{uuid.uuid4().hex[:6]}",
        name=name,
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _layer(db_session, std, group_name, parent_id=None, ordering=0):
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=std.id,
        parent_id=parent_id,
        group_name=group_name,
        ordering=ordering,
    )
    db_session.add(lg)
    await db_session.flush()
    return lg


async def _table(db_session, std, layer, table_name, table_type="attribute",
                 geometry_type=None, ordering=0):
    t = StandardTable(
        id=str(uuid.uuid4()),
        layer_id=layer.id,
        standard_id=std.id,
        table_name=table_name,
        table_type=table_type,
        geometry_type=geometry_type,
        ordering=ordering,
    )
    db_session.add(t)
    await db_session.flush()
    return t


async def _link(db_session, layer, table, is_primary=True, ordering=0):
    ln = StandardLayerGroupTableLink(
        id=str(uuid.uuid4()),
        layer_group_id=layer.id,
        table_id=table.id,
        is_primary=is_primary,
        ordering=ordering,
    )
    db_session.add(ln)
    await db_session.flush()
    return ln


async def _field(db_session, std, table, field_name, data_type="string"):
    f = StandardField(
        id=str(uuid.uuid4()),
        standard_id=std.id,
        table_id=table.id,
        field_name=field_name,
        data_type=data_type,
    )
    db_session.add(f)
    await db_session.flush()
    return f


class TestListStandardTables:

    async def test_flat_list_across_groups(self, client, db_session):
        raw, key = await _key(db_session, "p7b_lst_a")
        s = await _seed_std(db_session, key)
        l1 = await _layer(db_session, s, "组1")
        l2 = await _layer(db_session, s, "组2")
        sub = await _layer(db_session, s, "组1子", parent_id=l1.id)
        await _table(db_session, s, l1, "表A", table_type="spatial", geometry_type="Polygon")
        await _table(db_session, s, sub, "表B")
        await _table(db_session, s, l2, "表C")
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/tables", headers=h)
        assert resp.status_code == 200
        names = {t["table_name"] for t in resp.json()}
        assert names == {"表A", "表B", "表C"}

    async def test_404_for_missing_or_foreign(self, client, db_session):
        raw, key = await _key(db_session, "p7b_lst_b")
        s = await _seed_std(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        resp = await client.get(
            "/api/v1/data-standards-v2/no-such-id/tables", headers=h)
        assert resp.status_code == 404


class TestListLayersGuard:

    async def test_404_foreign_standard(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_ll_a")
        raw_b, key_b = await _key(db_session, "p7b_ll_b")
        s = await _seed_std(db_session, key_a)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/layers",
            headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestCreateLayerDepthGuard:

    async def test_depth_exceeds_max(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cr_a")
        s = await _seed_std(db_session, key)
        # 造 6 层链 root→L1→…→L5（L5 深度 5）
        parent = None
        for i in range(6):
            parent = await _layer(db_session, s, f"L{i}", parent_id=parent.id if parent else None)
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/layers",
            headers=h,
            json={"group_name": "超深", "parent_id": parent.id},
        )
        assert resp.status_code == 400
        assert "嵌套深度" in resp.json()["detail"]

    async def test_create_missing_standard_404(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cr_b")
        resp = await client.post(
            "/api/v1/data-standards-v2/no-such/layers",
            headers={"X-API-Key": raw},
            json={"group_name": "x"},
        )
        assert resp.status_code == 404


class TestUpdateLayerGuard:

    async def test_404_missing_layer(self, client, db_session):
        raw, key = await _key(db_session, "p7b_up_a")
        resp = await client.put(
            "/api/v1/layers/no-such-layer",
            headers={"X-API-Key": raw},
            json={"group_name": "x"},
        )
        assert resp.status_code == 404

    async def test_403_foreign_key(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_up_b")
        raw_b, key_b = await _key(db_session, "p7b_up_c")
        s = await _seed_std(db_session, key_a)
        lg = await _layer(db_session, s, "私有层")
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/layers/{lg.id}",
            headers={"X-API-Key": raw_b},
            json={"group_name": "改名"},
        )
        assert resp.status_code == 403


class TestDeleteLayerGuard:

    async def test_404_missing(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dl_a")
        resp = await client.delete(
            "/api/v1/layers/no-such", headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_403_foreign(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_dl_b")
        raw_b, key_b = await _key(db_session, "p7b_dl_c")
        s = await _seed_std(db_session, key_a)
        lg = await _layer(db_session, s, "私有层")
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/layers/{lg.id}", headers={"X-API-Key": raw_b})
        assert resp.status_code == 403

    async def test_400_shared_primary_table_guard(self, client, db_session):
        raw, key = await _key(db_session, "p7b_dl_d")
        s = await _seed_std(db_session, key)
        l1 = await _layer(db_session, s, "主层")
        l2 = await _layer(db_session, s, "次层")
        t = await _table(db_session, s, l1, "共享表")
        await _link(db_session, l1, t, is_primary=True)
        await _link(db_session, l2, t, is_primary=False)
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.delete(f"/api/v1/layers/{l1.id}", headers=h)
        assert resp.status_code == 400
        assert "共享表" in resp.json()["detail"]


class TestMoveLayerGuard:

    async def test_404_missing(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_a")
        resp = await client.post(
            "/api/v1/layers/no-such/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": None},
        )
        assert resp.status_code == 404

    async def test_403_foreign(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_mv_b")
        raw_b, key_b = await _key(db_session, "p7b_mv_c")
        s = await _seed_std(db_session, key_a)
        lg = await _layer(db_session, s, "私有层")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{lg.id}/move",
            headers={"X-API-Key": raw_b},
            json={"new_parent_id": None},
        )
        assert resp.status_code == 403

    async def test_404_new_parent_missing(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_d")
        s = await _seed_std(db_session, key)
        lg = await _layer(db_session, s, "A")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{lg.id}/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": "no-such-parent"},
        )
        assert resp.status_code == 404

    async def test_400_cross_standard(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_e")
        s1 = await _seed_std(db_session, key, name="标准1")
        s2 = await _seed_std(db_session, key, name="标准2")
        a = await _layer(db_session, s1, "A")
        p = await _layer(db_session, s2, "P")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": p.id},
        )
        assert resp.status_code == 400
        assert "跨标准" in resp.json()["detail"]

    async def test_400_move_into_own_child_cycle(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_f")
        s = await _seed_std(db_session, key)
        a = await _layer(db_session, s, "A")
        b = await _layer(db_session, s, "B", parent_id=a.id)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": b.id},
        )
        assert resp.status_code == 400
        assert "子孙" in resp.json()["detail"]

    async def test_400_target_depth_exceeds(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_g")
        s = await _seed_std(db_session, key)
        # 链 0..4 → parent 深度 4；被移动 A 带子 B（子树高 2）
        parent = None
        for i in range(5):
            parent = await _layer(db_session, s, f"C{i}",
                                  parent_id=parent.id if parent else None)
        a = await _layer(db_session, s, "A")
        await _layer(db_session, s, "B", parent_id=a.id)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": parent.id},
        )
        assert resp.status_code == 400

    async def test_400_root_move_subtree_too_deep(self, client, db_session):
        raw, key = await _key(db_session, "p7b_mv_h")
        s = await _seed_std(db_session, key)
        # A 下链 5 个子 → subtree_h=6 > 5
        a = await _layer(db_session, s, "A")
        parent = a
        for i in range(5):
            parent = await _layer(db_session, s, f"D{i}", parent_id=parent.id)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/move",
            headers={"X-API-Key": raw},
            json={"new_parent_id": None},
        )
        assert resp.status_code == 400


class TestLayersHappyPath:
    """补 layers.py 成功路径：GET 树 / POST / PUT / move happy / delete 无共享。"""

    async def test_full_flow(self, client, db_session):
        raw, key = await _key(db_session, "p7b_hp_a")
        s = await _seed_std(db_session, key)
        p1 = await _layer(db_session, s, "父层")
        c1 = await _layer(db_session, s, "子层", parent_id=p1.id)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # GET 层组树（含嵌套 children + 空表）
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/layers", headers=h)
        assert resp.status_code == 200
        tree = resp.json()
        assert len(tree) == 1
        assert tree[0]["children"][0]["group_name"] == "子层"

        # POST 新层组（带 group_code/description/ordering）
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/layers",
            headers=h,
            json={"group_name": "新层", "group_code": "N",
                  "description": "描述", "ordering": 9},
        )
        assert resp.status_code == 201
        new_id = resp.json()["id"]
        assert resp.json()["group_code"] == "N"

        # PUT 更新（exclude_unset → 只改 group_name + ordering）
        resp = await client.put(
            f"/api/v1/layers/{new_id}",
            headers=h,
            json={"group_name": "新层V2", "ordering": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["group_name"] == "新层V2"
        assert resp.json()["ordering"] == 5

        # move happy：把新层移到 父层 下
        resp = await client.post(
            f"/api/v1/layers/{new_id}/move",
            headers=h,
            json={"new_parent_id": p1.id},
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == p1.id

        # move 到自身 → 400（自环防护）
        resp = await client.post(
            f"/api/v1/layers/{new_id}/move",
            headers=h,
            json={"new_parent_id": new_id},
        )
        assert resp.status_code == 400

        # DELETE 无共享表 → 204（主干删除路径）
        resp = await client.delete(f"/api/v1/layers/{c1.id}", headers=h)
        assert resp.status_code == 204


class TestCopyLayer:

    async def test_copy_same_standard_with_subtree(self, client, db_session):
        """同标准复制：子层组/表/字段/次归属递归复制。"""
        raw, key = await _key(db_session, "p7b_cp_a")
        s = await _seed_std(db_session, key)
        a = await _layer(db_session, s, "源层", ordering=1)
        a1 = await _layer(db_session, s, "源之子", parent_id=a.id)
        ext = await _layer(db_session, s, "外部层")
        t = await _table(db_session, s, a, "源表")
        f = await _field(db_session, s, t, "编号", data_type="int")
        f.length = 10
        f.is_primary_key = True
        await _link(db_session, a, t, is_primary=True)
        # 次归属：映射到子树内 a1（mapped 分支） + 子树外 ext（同标准保留分支）
        await _link(db_session, a1, t, is_primary=False, ordering=1)
        await _link(db_session, ext, t, is_primary=False, ordering=2)
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers=h,
            json={"new_name": "复制层"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["group_name"] == "复制层"
        assert body["parent_id"] is None

        # 校验新结构落库
        from sqlalchemy import select
        new_tables = (await db_session.execute(
            select(StandardTable).where(StandardTable.layer_id == body["id"])
        )).scalars().all()
        assert len(new_tables) == 1
        new_fields = (await db_session.execute(
            select(StandardField).where(StandardField.table_id == new_tables[0].id)
        )).scalars().all()
        assert new_fields[0].field_name == "编号"
        assert new_fields[0].is_primary_key is True
        # 次归属应保留 2 条（a1 映射 + ext 同标准保留）
        links = (await db_session.execute(
            select(StandardLayerGroupTableLink).where(
                StandardLayerGroupTableLink.table_id == new_tables[0].id,
                StandardLayerGroupTableLink.is_primary.is_(False),
            )
        )).scalars().all()
        assert len(links) == 2

    async def test_copy_cross_standard_into_parent(self, client, db_session):
        """跨标准复制到目标父层组；带 default 名与跨标准次归属丢弃。"""
        raw, key = await _key(db_session, "p7b_cp_b")
        s1 = await _seed_std(db_session, key, name="源标准")
        s2 = await _seed_std(db_session, key, name="目标标准")
        a = await _layer(db_session, s1, "源层")
        inner = await _layer(db_session, s1, "内部子", parent_id=a.id)
        ext = await _layer(db_session, s1, "外部层")
        t = await _table(db_session, s1, a, "跨源表")
        await _link(db_session, a, t, is_primary=True)
        await _link(db_session, inner, t, is_primary=False)
        await _link(db_session, ext, t, is_primary=False)
        p = await _layer(db_session, s2, "目标父")
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers=h,
            json={"new_standard_id": s2.id, "new_parent_id": p.id},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["standard_id"] == s2.id
        assert body["parent_id"] == p.id
        assert body["group_name"] == "源层 (副本)"  # 默认副本名

        from sqlalchemy import select
        new_tables = (await db_session.execute(
            select(StandardTable).where(StandardTable.layer_id == body["id"])
        )).scalars().all()
        assert len(new_tables) == 1
        links = (await db_session.execute(
            select(StandardLayerGroupTableLink).where(
                StandardLayerGroupTableLink.table_id == new_tables[0].id,
                StandardLayerGroupTableLink.is_primary.is_(False),
            )
        )).scalars().all()
        # 跨标准：仅子树内次归属（inner→新内部子）保留，ext 丢弃 → 1 条
        assert len(links) == 1

    async def test_copy_404_layer_missing(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cp_c")
        resp = await client.post(
            "/api/v1/layers/no-such/copy",
            headers={"X-API-Key": raw},
            json={},
        )
        assert resp.status_code == 404

    async def test_copy_403_foreign(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_cp_d")
        raw_b, key_b = await _key(db_session, "p7b_cp_e")
        s = await _seed_std(db_session, key_a)
        a = await _layer(db_session, s, "私有层")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers={"X-API-Key": raw_b},
            json={},
        )
        assert resp.status_code == 403

    async def test_copy_404_target_standard(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cp_f")
        s = await _seed_std(db_session, key)
        a = await _layer(db_session, s, "A")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers={"X-API-Key": raw},
            json={"new_standard_id": "no-such-std"},
        )
        assert resp.status_code == 404

    async def test_copy_404_target_parent(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cp_g")
        s = await _seed_std(db_session, key)
        a = await _layer(db_session, s, "A")
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers={"X-API-Key": raw},
            json={"new_parent_id": "no-such-parent"},
        )
        assert resp.status_code == 404

    async def test_copy_400_parent_wrong_standard(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cp_h")
        s1 = await _seed_std(db_session, key, name="源")
        s2 = await _seed_std(db_session, key, name="目标")
        a = await _layer(db_session, s1, "A")
        p1 = await _layer(db_session, s1, "P1")   # 属于源标准
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers={"X-API-Key": raw},
            json={"new_standard_id": s2.id, "new_parent_id": p1.id},
        )
        assert resp.status_code == 400
        assert "不属于目标标准" in resp.json()["detail"]

    async def test_copy_400_depth_exceeds(self, client, db_session):
        raw, key = await _key(db_session, "p7b_cp_i")
        s = await _seed_std(db_session, key)
        parent = None
        for i in range(5):
            parent = await _layer(db_session, s, f"C{i}",
                                  parent_id=parent.id if parent else None)
        a = await _layer(db_session, s, "A")
        await _layer(db_session, s, "B", parent_id=a.id)
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/layers/{a.id}/copy",
            headers={"X-API-Key": raw},
            json={"new_parent_id": parent.id},
        )
        assert resp.status_code == 400