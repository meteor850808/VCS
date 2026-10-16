# -*- coding: utf-8 -*-
"""P7b-2: data_standard_v2/versions.py 全覆盖（60% → 100%）。

补 rich 结构下的 qc-summary / publish×2(diff 全链) / diff 端点 / rollback
快照恢复 / 404/500 分支；顺带覆盖 _common._delete_standard_children。
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


async def _std(db_session, key, name="P7b版本标准"):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-VP-{uuid.uuid4().hex[:6]}",
        name=name,
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _seed_rich(db_session, std, table_suffix=""):
    """造完整结构：root→child 层组、带表+字段+字典关联、标准字典、全局字典。"""
    root = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=std.id, group_name="根层", group_code="BG", ordering=0,
    )
    db_session.add(root)
    await db_session.flush()
    child = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=std.id, parent_id=root.id, group_name="子层",
        group_code="CH", ordering=0,
    )
    db_session.add(child)
    await db_session.flush()
    table = StandardTable(
        id=str(uuid.uuid4()),
        layer_id=root.id, standard_id=std.id,
        table_name=f"道路面{table_suffix}", table_code="RD_FACE",
        table_type="spatial", geometry_type="Polygon",
    )
    db_session.add(table)
    await db_session.flush()
    field_a = StandardField(
        id=str(uuid.uuid4()),
        standard_id=std.id, table_id=table.id, field_name="名称",
        field_alias="NM", data_type="string", length=50,
        is_required=True, ordering=1,
    )
    field_b = StandardField(
        id=str(uuid.uuid4()),
        standard_id=std.id, table_id=table.id, field_name="状态",
        data_type="string", ordering=2,
    )
    db_session.add_all([field_a, field_b])
    await db_session.flush()

    std_dict = StandardDictionary(
        id=str(uuid.uuid4()),
        standard_id=std.id, scope="standard", dict_name=f"材质{table_suffix}",
        dict_code="CZ",
    )
    db_session.add(std_dict)
    await db_session.flush()
    db_session.add_all([
        DictionaryItem(id=str(uuid.uuid4()),
                       dict_id=std_dict.id, item_code="BR", item_name="砖"),
        DictionaryItem(id=str(uuid.uuid4()),
                       dict_id=std_dict.id, item_code="ST", item_name="石"),
    ])
    await db_session.flush()
    db_session.add(StandardFieldDictLink(
        id=str(uuid.uuid4()),
        field_id=field_b.id, dict_id=std_dict.id, is_primary=True,
    ))
    await db_session.flush()
    return root, child, table, (field_a, field_b), std_dict


class TestQcSummaryRich:

    async def test_rich_summary(self, client, db_session):
        raw, key = await _key(db_session, "p7b_qs_a")
        s = await _std(db_session, key)
        await _seed_rich(db_session, s)
        # 全局字典（跨标准）
        gdict = StandardDictionary(
            id=str(uuid.uuid4()),
            standard_id=None, scope="global", dict_name="全局路况", dict_code="GL",
        )
        db_session.add(gdict)
        await db_session.flush()
        db_session.add(DictionaryItem(
            id=str(uuid.uuid4()),
            dict_id=gdict.id, item_code="GOOD", item_name="良好",
        ))
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/qc-summary",
            headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["standard_name"] == "P7b版本标准"
        assert body["stats"]["table_count"] == 1
        assert body["stats"]["field_count"] == 2
        assert body["stats"]["linked_field_count"] == 1
        # 层组树（含子层）与字典
        assert len(body["tables"]) == 1
        assert body["tables"][0]["fields"][1]["dict_links"][0]["dict_name"] == "材质"
        assert body["standard_dictionaries"][0]["item_count"] == 2
        assert any(d["dict_name"] == "全局路况"
                   for d in body["global_dictionaries"])

    async def test_404(self, client, db_session):
        raw, key = await _key(db_session, "p7b_qs_b")
        resp = await client.get(
            "/api/v1/data-standards-v2/no-such/qc-summary",
            headers={"X-API-Key": raw})
        assert resp.status_code == 404


class TestPublishTwiceDiffChain:

    async def test_second_publish_diffs_naturally(self, client, db_session):
        raw, key = await _key(db_session, "p7b_pb_a")
        s = await _std(db_session, key)
        root, _, _, (fa, fb), sd = await _seed_rich(db_session, s)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 第一次发布（prev 空 → added 全量）
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers=h, json={"change_log": "v1"})
        assert resp.status_code == 201
        # 修改标准：改名 + 删除 child 层组 + 加一个字段 + 改字段长度 + 字典加项
        s.name = "改名标准"
        child_id = None
        from sqlalchemy import select
        child = (await db_session.execute(
            select(StandardLayerGroup).where(
                StandardLayerGroup.parent_id == root.id))).scalars().first()
        child_id = child.id
        await db_session.delete(child)
        await db_session.flush()
        fa.length = 80
        new_field = StandardField(
            id=str(uuid.uuid4()),
            standard_id=s.id, table_id=fa.table_id, field_name="新字段",
            data_type="int", ordering=3,
        )
        db_session.add(new_field)
        db_session.add(DictionaryItem(
            id=str(uuid.uuid4()),
            dict_id=sd.id, item_code="WO", item_name="木",
        ))
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers=h, json={"change_log": "v2"})
        assert resp.status_code == 201
        v2 = resp.json()
        assert v2["version_number"] == 2

        # 版本历史
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions", headers=h)
        assert len(resp.json()) == 2

        std = await db_session.get(DataStandard, s.id)
        await db_session.refresh(std)
        assert std.version == 3
        assert std.status == "published"

    async def test_publish_404(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_pb_b")
        raw_b, key_b = await _key(db_session, "p7b_pb_c")
        s = await _std(db_session, key_a)
        await db_session.commit()
        # 不存在
        resp = await client.post(
            "/api/v1/data-standards-v2/no-such/publish",
            headers={"X-API-Key": raw_a}, json={"change_log": "x"})
        assert resp.status_code == 404
        # 非所属
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers={"X-API-Key": raw_b}, json={"change_log": "x"})
        assert resp.status_code == 404


class TestDiffVersionsRich:

    async def test_rich_diff_endpoint(self, client, db_session):
        raw, key = await _key(db_session, "p7b_vd_a")
        s = await _std(db_session, key)
        snapshot_v1 = {
            "standard": {"name": "V1名", "code": s.code, "category": "custom",
                         "version": 1},
            "layer_groups": [
                {"id": str(uuid.uuid4()), "group_name": "根", "group_code": "BG",
                 "parent_id": None, "tables": [{
                     "id": str(uuid.uuid4()), "table_name": "旧表",
                     "table_code": "OLD", "table_type": "spatial",
                     "geometry_type": "Point", "table_type_ext": None,
                     "fields": [
                         {"id": str(uuid.uuid4()), "field_name": "旧字段",
                          "field_alias": "OF", "data_type": "string",
                          "length": 10, "nullable": True},
                     ],
                 }], "children": []},
            ],
            "dictionaries": [
                {"id": str(uuid.uuid4()), "dict_name": "材质字典",
                 "scope": "standard", "items": [
                     {"item_code": "A", "item_name": "甲"},
                 ]},
            ],
        }
        snapshot_v2 = {
            "standard": {"name": "V2名", "code": s.code, "category": "custom",
                         "version": 2},
            "layer_groups": [
                {"id": str(uuid.uuid4()), "group_name": "新根", "group_code": "BG",
                 "parent_id": None, "tables": [{
                     "id": str(uuid.uuid4()), "table_name": "新表",
                     "table_code": "OLD", "table_type": "spatial",
                     "geometry_type": "MultiPolygon", "table_type_ext": None,
                     "fields": [
                         {"id": str(uuid.uuid4()), "field_name": "旧字段",
                          "field_alias": "OF2", "data_type": "int",
                          "length": 6, "nullable": False},
                     ],
                 }], "children": []},
            ],
            "dictionaries": [
                {"id": str(uuid.uuid4()), "dict_name": "材质字典",
                 "scope": "standard", "items": [
                     {"item_code": "A", "item_name": "甲改"},
                     {"item_code": "B", "item_name": "乙"},
                 ]},
            ],
        }
        db_session.add_all([
            StandardVersion(
                id=str(uuid.uuid4()),
                standard_id=s.id, api_key_id=key.id, version_number=1,
                snapshot_data=snapshot_v1),
            StandardVersion(
                id=str(uuid.uuid4()),
                standard_id=s.id, api_key_id=key.id, version_number=2,
                snapshot_data=snapshot_v2),
        ])
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/1/diff/2", headers=h)
        assert resp.status_code == 200
        body = resp.json()
        assert body["from_version"] == 1 and body["to_version"] == 2
        # 自然键对齐：层组/表/字段 modified；字典新增项 added
        assert any("layer_groups/BG" in k for k in body["modified"])
        assert any("tables/OLD" in k for k in body["modified"])
        assert any("fields" in k for k in body["modified"])
        assert any("items/B" in k for k in body["added"])
        # 摘要与统计
        assert isinstance(body["summary"], list) and body["summary"]
        assert body["stats"]["layer_groups"]["modified"] >= 1
        assert body["stats"]["tables"]["modified"] >= 1
        assert body["stats"]["fields"]["modified"] >= 1
        assert body["stats"]["items"]["added"] == 1
        # 标签映射
        assert body["labels"]["layer_groups"].get("BG") in ("根", "新根")
        assert body["labels"]["dictionaries"]["材质字典"] == "材质字典"
        # items/A modified / items/B added（自然键 item_code）
        assert any("items/A" in k for k in body["modified"])
        assert body["stats"]["items"]["modified"] == 1

    async def test_v1_exists_v2_missing_404(self, client, db_session):
        raw, key = await _key(db_session, "p7b_vd_b")
        s = await _std(db_session, key)
        db_session.add(StandardVersion(
            id=str(uuid.uuid4()),
            standard_id=s.id, api_key_id=key.id, version_number=1,
            snapshot_data={"name": "x"}))
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/1/diff/99",
            headers={"X-API-Key": raw})
        assert resp.status_code == 404
        # v1 缺失、v2 存在 → 同 404（ver1 分支）
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/99/diff/1",
            headers={"X-API-Key": raw})
        assert resp.status_code == 404


class TestDeepDiffUnit:
    """直测 versions 模块 diff 辅助函数（补 436-483/493-495 死路分支）。"""

    def test_none_leaf_values(self):
        from app.api.v1.data_standard_v2 import versions as V
        # both None → 空
        assert V._deep_diff(None, None)["added"] == {}
        # old None → added
        r = V._deep_diff(None, {"a": 1}, "x/y")
        assert r["added"]["x/y"] == {"a": 1}
        # new None → removed
        r = V._deep_diff({"a": 1}, None, "x")
        assert r["removed"]["x"] == {"a": 1}

    def test_generic_list_indices(self):
        """路径段不在自然键链上 → 按下标分支 471-483。"""
        from app.api.v1.data_standard_v2 import versions as V
        r = V._deep_diff([1, 2], [1, 3, 4], "misc")
        assert "misc[1]" in r["modified"] and "misc[2]" in r["added"]
        r = V._deep_diff([1, 2, 5], [1, 2], "misc")
        assert "misc[2]" in r["removed"]

    def test_key_lookup_fallbacks(self):
        """自然键缺失回退链：dict 无 key(448) / 非 dict 元素(453)。"""
        from app.api.v1.data_standard_v2 import versions as V
        r = V._deep_diff(
            [{"foo": 1}, 9],
            [{"foo": 2}, 8],
            "children",  # children 在链上，但元素缺 group_code/id
        )
        # 无自然键可对齐 → 空对比（元素落空）
        assert r["added"] == {} and r["removed"] == {}

    def test_dict_added_removed_keys(self):
        from app.api.v1.data_standard_v2 import versions as V
        r = V._deep_diff({"a": 1}, {"a": 1, "b": 2}, "")
        assert "b" in r["added"]
        r = V._deep_diff({"a": 1, "c": 3}, {"a": 1}, "")
        assert "c" in r["removed"]

    def test_collect_labels_robustness(self):
        """_collect_diff_labels 脏数据防线。"""
        from app.api.v1.data_standard_v2 import versions as V
        labels = V._collect_diff_labels(
            {"layer_groups": ["not-dict",
                              {"group_code": "G1", "group_name": "根",
                               "tables": ["junk",
                                          {"table_code": "T1", "table_name": "表一",
                                           "fields": ["bad",
                                                      {"field_name": "F1",
                                                       "field_alias": "FA"}]}],
                               "children": ["x"]}],
            "dictionaries": ["junk",
                             {"dict_name": "材", "items": ["x",
                                                           {"item_code": "I1",
                                                            "item_name": "项"}]}]},
            None,  # 非 dict 快照
        )
        assert labels["layer_groups"].get("G1") == "根"
        assert labels["tables"].get("T1") == "表一"
        assert labels["fields"].get("F1") == "FA"
        assert labels["dictionaries"].get("材") == "材"
        assert labels["items"].get("I1") == "项"

    async def test_common_children_delegation(self):
        """C_delete_standard_children 兼容别名需真实执行（覆盖 _common 委托）。"""
        from app.api.v1.data_standard_v2 import versions as V
        from app.api.v1.data_standard_v2 import _common as C
        called = {}
        orig = C._delete_standard_children

        async def fake(db, standard_id):
            called["x"] = standard_id

        C._delete_standard_children = fake
        try:
            await V.C_delete_standard_children(None, "S1")
        finally:
            C._delete_standard_children = orig
        assert called.get("x") == "S1"

    async def test_diff_404_standard(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_vd_c")
        raw_b, key_b = await _key(db_session, "p7b_vd_d")
        s = await _std(db_session, key_a)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/1/diff/2",
            headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestRollbackRestore:

    async def test_rollback_rich_snapshot(self, client, db_session):
        raw, key = await _key(db_session, "p7b_rb_a")
        s = await _std(db_session, key)
        root, child, table, (fa, fb), sd = await _seed_rich(db_session, s)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 发布 v1 拿全量快照
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers=h, json={"change_log": "v1"})
        assert resp.status_code == 201

        # 破坏结构：删除 chld 层组 + 一个字段
        await db_session.delete(child)
        await db_session.delete(fa)
        s.name = "破坏后"
        await db_session.commit()

        # 回滚到 v1 → 快照恢复全部子记录
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers=h)
        assert resp.status_code == 201
        rec = resp.json()
        assert rec["is_rollback"] is True
        assert "回滚" in rec["change_log"]

        from sqlalchemy import select
        layers = (await db_session.execute(
            select(StandardLayerGroup).where(
                StandardLayerGroup.standard_id == s.id))).scalars().all()
        assert len(layers) == 2  # root + child 恢复
        tables = (await db_session.execute(
            select(StandardTable).where(
                StandardTable.standard_id == s.id))).scalars().all()
        assert len(tables) == 1
        fields = (await db_session.execute(
            select(StandardField).where(
                StandardField.standard_id == s.id))).scalars().all()
        assert len(fields) == 2  # 字段恢复
        std_dicts = (await db_session.execute(
            select(StandardDictionary).where(
                StandardDictionary.standard_id == s.id))).scalars().all()
        assert len(std_dicts) == 1
        items = (await db_session.execute(
            select(DictionaryItem).where(
                DictionaryItem.dict_id == std_dicts[0].id))).scalars().all()
        assert len(items) == 2

    async def test_rollback_404_standard_and_version(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7b_rb_b")
        raw_b, key_b = await _key(db_session, "p7b_rb_c")
        s = await _std(db_session, key_a)
        await db_session.commit()
        # 标准不存在
        resp = await client.post(
            "/api/v1/data-standards-v2/no-such/rollback/1",
            headers={"X-API-Key": raw_a})
        assert resp.status_code == 404
        # 非所属
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1",
            headers={"X-API-Key": raw_b})
        assert resp.status_code == 404
        # 版本不存在
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/99",
            headers={"X-API-Key": raw_a})
        assert resp.status_code == 404

    async def test_rollback_snapshot_restore_failure_500(self, client, db_session):
        """快照缺少 group_name → KeyError → 500 回滚失败。"""
        raw, key = await _key(db_session, "p7b_rb_d")
        s = await _std(db_session, key)
        await db_session.commit()
        bad_snapshot = {
            "layer_groups": [{"id": str(uuid.uuid4())}],  # 缺 group_name
            "dictionaries": [],
        }
        db_session.add(StandardVersion(
            id=str(uuid.uuid4()),
            standard_id=s.id, api_key_id=key.id, version_number=1,
            snapshot_data=bad_snapshot))
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1",
            headers={"X-API-Key": raw})
        assert resp.status_code == 500
        assert "快照恢复失败" in resp.json()["detail"]