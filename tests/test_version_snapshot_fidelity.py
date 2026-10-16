# -*- coding: utf-8 -*-
"""版本回滚的快照保真度：回滚后必须与发布时**完全等价**，不能少任何边或列。

缺陷背景（v2.22.3 取证发现）：
`_delete_standard_children` 会删 `StandardFieldDictLink`，但
`_build_standard_snapshot` 压根不加载它、`_restore_from_snapshot` 也不重建
—— 于是**回滚后字段-字典绑定全部消失**。而 v2.22.3 刚把绑定做成模板往返，
并确认字典绑定是质检真实数据源（`rule_generator.py` → `VALUE_IN_SET`、
`attribute_checker.py` A5 值域 31010005），绑定一丢，值域质检整链失效。

除绑定外，同一链路还丢：
- `StandardDictionary.dict_code`（v2.22.3 绑定主键）、`description`、`is_system`
- `DictionaryItem.parent_code`（字典层级）、`description`、`ordering`
- 层组 / 表 的 `description`、`ordering`
- S9 次归属（`StandardLayerGroupTableLink` 只恢复主归属）

以及两个**幽灵字段**：快照写入 `field_code` / `table_type_ext`，但模型里
根本没有这两列 —— 写进去又不会被恢复，纯属噪声。

这些测试全部按「发布 → 破坏 → 回滚 → 查库核对」的行为路径写，
不依赖快照 dict 的内部结构。
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
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


async def _std(db_session, key, name):
    s = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        code=f"ST-SNAP-{uuid.uuid4().hex[:6]}",
        name=name,
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _seed_full_fidelity(db_session, std):
    """造一份「每个可快照列都有值」的结构，用于回滚后逐列核对。

    返回 id 字典（而非 ORM 对象）—— 破坏阶段要删对象，断言阶段要按 id 查。
    """
    root = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id, group_name="根层",
        group_code="BG", description="根层描述", ordering=7,
    )
    db_session.add(root)
    await db_session.flush()
    other = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id, group_name="另一层",
        group_code="OT", description="另一层描述", ordering=3,
    )
    db_session.add(other)
    await db_session.flush()

    table = StandardTable(
        id=str(uuid.uuid4()), layer_id=root.id, standard_id=std.id,
        table_name="路面", table_code="RD", table_type="spatial",
        geometry_type="Polygon", description="路面表描述", ordering=4,
    )
    db_session.add(table)
    await db_session.flush()

    field_a = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="名称", field_alias="NM", data_type="string", length=50,
        is_required=True, description="名称字段", ordering=1,
    )
    field_b = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="材质", data_type="string", default_value="砖",
        description="材质字段", ordering=2,
    )
    db_session.add_all([field_a, field_b])
    await db_session.flush()

    # 主归属 + S9 次归属（两条边，只恢复一条就说明次归属丢了）
    db_session.add(StandardLayerGroupTableLink(
        id=str(uuid.uuid4()), layer_group_id=root.id, table_id=table.id,
        is_primary=True, ordering=0,
    ))
    secondary = StandardLayerGroupTableLink(
        id=str(uuid.uuid4()), layer_group_id=other.id, table_id=table.id,
        is_primary=False, ordering=9,
    )
    db_session.add(secondary)
    await db_session.flush()

    sd = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=std.id, scope="standard",
        dict_name="材质字典", dict_code="CZ", description="材质字典描述",
    )
    db_session.add(sd)
    await db_session.flush()

    # 层级字典项：BR 是 ST 的子项
    parent_item = DictionaryItem(
        id=str(uuid.uuid4()), dict_id=sd.id, item_code="ST", item_name="石",
        description="石说明", ordering=1,
    )
    db_session.add(parent_item)
    await db_session.flush()
    db_session.add(DictionaryItem(
        id=str(uuid.uuid4()), dict_id=sd.id, item_code="BR", item_name="砖",
        parent_code="ST", description="砖说明", ordering=2,
    ))
    await db_session.flush()

    # 两条绑定：主 + 次，且带 filter_condition / ordering
    link_a = StandardFieldDictLink(
        id=str(uuid.uuid4()), field_id=field_b.id, dict_id=sd.id,
        is_primary=True, ordering=0, filter_condition="BR",
    )
    link_b = StandardFieldDictLink(
        id=str(uuid.uuid4()), field_id=field_a.id, dict_id=sd.id,
        is_primary=False, ordering=5,
    )
    db_session.add_all([link_a, link_b])
    await db_session.flush()

    return {
        "root": root, "other": other, "table": table,
        "field_a": field_a, "field_b": field_b, "secondary": secondary,
        "dict": sd, "link_ids": [link_a.id, link_b.id],
    }


async def _publish_then_break(client, db_session, raw, std, seeded):
    """发布 v1 拿全量快照，然后把所有子记录删干净（模拟最坏破坏）。

    破坏直接复用 `_delete_standard_children` —— 回滚自己也是先删后建，
    且这个函数已处理过正确的删除顺序（手写顺序会触发 FK 级联告警）。
    """
    from app.api.v1.data_standard_v2 import _common as C

    h = {"X-API-Key": raw}
    resp = await client.post(
        f"/api/v1/data-standards-v2/{std.id}/publish",
        headers=h, json={"change_log": "v1"})
    assert resp.status_code == 201, resp.text

    await C._delete_standard_children(db_session, std.id)
    await db_session.commit()

    # 确认破坏干净了 —— 否则后面的断言可能"因为没删掉"而假通过
    assert (await db_session.execute(
        select(StandardField).where(StandardField.standard_id == std.id)
    )).scalars().all() == []
    assert (await db_session.execute(
        select(StandardDictionary).where(StandardDictionary.standard_id == std.id)
    )).scalars().all() == []


async def _fetch(db_session, model, standard_id):
    """查库前必须 expire_all。

    回滚会用快照里的**相同 id** 重建实体/边，而本测试全程共用一个
    `db_session` —— identity map 里还挂着破坏前那批对象。不清缓存的话
    查询返回的是内存里的旧属性值，断言的是「没变过」而非「真被还原了」，
    于是任何"值写错/没写"的变异都杀不掉（已实测：S9 ordering 变异漏过）。
    """
    db_session.expire_all()
    return (await db_session.execute(
        select(model).where(model.standard_id == standard_id)
    )).scalars().all()


async def _fetch_where(db_session, stmt):
    """同 _fetch：先 expire_all 再执行任意查询。"""
    db_session.expire_all()
    return (await db_session.execute(stmt)).scalars().all()


class TestSnapshotFidelityEdges:
    """回滚后「边」必须一条不少。"""

    async def test_field_dict_links_survive_rollback(self, client, db_session):
        """字段-字典绑定回滚后必须还在（含 is_primary/filter_condition/ordering）。"""
        raw, key = await _key(db_session, "snap_link_1")
        s = await _std(db_session, key, "绑定保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        links = await _fetch_where(db_session, select(StandardFieldDictLink).where(
            StandardFieldDictLink.field_id.in_(
                select(StandardField.id).where(StandardField.standard_id == s.id))))
        assert len(links) == 2, f"绑定丢成 {len(links)} 条（应为 2）：{links}"
        by_field = {lk.field_id: lk for lk in links}
        fb, fa = seeded["field_b"].id, seeded["field_a"].id
        assert fa in by_field and fb in by_field, f"绑定的 field_id 错位：{by_field}"
        assert by_field[fb].is_primary is True
        assert by_field[fb].filter_condition == "BR"
        assert by_field[fb].ordering == 0
        assert by_field[fa].is_primary is False
        assert by_field[fa].ordering == 5

    async def test_dict_code_survives_rollback(self, client, db_session):
        """dict_code 是 v2.22.3 绑定主键，回滚后不能变 NULL。"""
        raw, key = await _key(db_session, "snap_code_1")
        s = await _std(db_session, key, "字典编码保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        dicts = await _fetch(db_session, StandardDictionary, s.id)
        assert len(dicts) == 1
        assert dicts[0].dict_code == "CZ", f"dict_code 丢失：{dicts[0].dict_code!r}"
        assert dicts[0].description == "材质字典描述"

    async def test_dictionary_item_hierarchy_survives_rollback(self, client, db_session):
        """字典项的 parent_code（层级）+ description + ordering 不能丢。"""
        raw, key = await _key(db_session, "snap_item_1")
        s = await _std(db_session, key, "字典层级保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        items = await _fetch_where(db_session, select(DictionaryItem).where(
            DictionaryItem.dict_id.in_(
                select(StandardDictionary.id).where(
                    StandardDictionary.standard_id == s.id))))
        assert len(items) == 2
        by_code = {i.item_code: i for i in items}
        assert by_code["BR"].parent_code == "ST", "字典层级 parent_code 丢失"
        assert by_code["ST"].parent_code is None
        assert by_code["BR"].description == "砖说明"
        assert by_code["ST"].description == "石说明"
        assert {i.ordering for i in items} == {1, 2}

    async def test_secondary_table_attribution_survives_rollback(self, client, db_session):
        """S9 次归属必须恢复（此前只恢复主归属）。"""
        raw, key = await _key(db_session, "snap_s9_1")
        s = await _std(db_session, key, "次归属保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        links = await _fetch_where(db_session, select(StandardLayerGroupTableLink))
        mine = [l for l in links if l.table_id == seeded["table"].id]
        assert len(mine) == 2, f"层组-表边丢成 {len(mine)} 条（应为 2）"
        assert {l.is_primary for l in mine} == {True, False}
        secondary = next(l for l in mine if not l.is_primary)
        assert secondary.layer_group_id == seeded["other"].id, "次归属指向错层组"
        assert secondary.ordering == 9, "次归属 ordering 丢失"
        primary = next(l for l in mine if l.is_primary)
        assert primary.layer_group_id == seeded["root"].id
        assert primary.ordering == 0, "主归属 ordering 丢失"


class TestSnapshotFidelityColumns:
    """回滚后「列」必须原样回来。"""

    async def test_layer_and_table_metadata_survive_rollback(self, client, db_session):
        raw, key = await _key(db_session, "snap_meta_1")
        s = await _std(db_session, key, "元信息保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        layers = await _fetch(db_session, StandardLayerGroup, s.id)
        by_id = {l.id: l for l in layers}
        assert by_id[seeded["root"].id].description == "根层描述"
        assert by_id[seeded["root"].id].ordering == 7
        assert by_id[seeded["other"].id].description == "另一层描述"
        assert by_id[seeded["other"].id].ordering == 3

        tables = await _fetch(db_session, StandardTable, s.id)
        assert len(tables) == 1
        assert tables[0].description == "路面表描述"
        assert tables[0].ordering == 4

    async def test_field_metadata_survives_rollback(self, client, db_session):
        raw, key = await _key(db_session, "snap_fmeta_1")
        s = await _std(db_session, key, "字段元信息保真")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()
        await _publish_then_break(client, db_session, raw, s, seeded)

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        fields = await _fetch(db_session, StandardField, s.id)
        by_id = {f.id: f for f in fields}
        assert by_id[seeded["field_a"].id].field_alias == "NM"
        assert by_id[seeded["field_a"].id].description == "名称字段"
        assert by_id[seeded["field_a"].id].is_required is True
        assert by_id[seeded["field_b"].id].default_value == "砖"
        assert by_id[seeded["field_b"].id].description == "材质字段"
        assert {f.ordering for f in fields} == {1, 2}


class TestSnapshotNoGhostFields:
    """快照不得写入模型里不存在的列。"""

    async def test_snapshot_has_no_ghost_columns(self, client, db_session):
        """`field_code` / `table_type_ext` 在 ORM 里不存在，属幽灵字段。

        写进快照只会让人误以为它们被备份了，实际恢复时又不会落库 ——
        比不写更危险（虚假安全感）。
        """
        from app.api.v1.data_standard_v2.versions import _build_standard_snapshot
        raw, key = await _key(db_session, "snap_ghost_1")
        s = await _std(db_session, key, "幽灵字段")
        await _seed_full_fidelity(db_session, s)
        await db_session.commit()

        snap = await _build_standard_snapshot(db_session, s.id)
        # 有两个根层组，只有一个挂了表 —— 按"有表"定位，别假设下标 0
        layer = next(lg for lg in snap["layer_groups"] if lg["tables"])
        table = layer["tables"][0]
        assert "table_type_ext" not in table, "table_type_ext 是幽灵字段"
        assert "field_code" not in table["fields"][0], "field_code 是幽灵字段"


class TestLegacySnapshotCompatibility:
    """历史快照（缺边表 / 缺列）必须仍能回滚，不能因为新字段而炸掉。"""

    async def test_rollback_legacy_snapshot_without_links(self, client, db_session):
        """v2.22.3 之前存的快照没有 field_dict_links / layer_group_table_links。

        这种快照回滚时：绑定无处可恢复（本来也没记），但**主归属必须补齐** ——
        `layer_group_tables` 已被全删，不补就违反「每表恰有一主归属」。
        """
        raw, key = await _key(db_session, "snap_legacy_1")
        s = await _std(db_session, key, "老快照兼容")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()

        # 手工造一份"老格式"快照：只有主干，没有两张边表
        legacy = {
            "standard": {"name": s.name, "code": s.code,
                         "category": s.category, "version": s.version},
            "layer_groups": [{
                "id": seeded["root"].id,
                "group_name": "根层", "group_code": "BG", "parent_id": None,
                "tables": [{
                    "id": seeded["table"].id, "table_name": "路面",
                    "table_code": "RD", "table_type": "spatial",
                    "geometry_type": "Polygon",
                    "fields": [{
                        "id": seeded["field_b"].id, "field_name": "材质",
                        "field_alias": None, "data_type": "string",
                        "length": None, "precision": None, "nullable": True,
                        "is_primary_key": False, "is_required": False,
                        "default_value": None, "description": None,
                        "ordering": 2,
                    }],
                }],
                "children": [],
            }],
            "dictionaries": [{
                "id": seeded["dict"].id, "dict_name": "材质字典", "scope": "standard",
                "items": [{"item_code": "BR", "item_name": "砖"}],
            }],
        }
        assert "field_dict_links" not in legacy
        assert "layer_group_table_links" not in legacy

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers={"X-API-Key": raw})
        # 没有 v1 版本 → 先发布一个，把老快照塞进去
        assert resp.status_code == 404

        pub = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers={"X-API-Key": raw}, json={"change_log": "legacy"})
        assert pub.status_code == 201

        from app.api.v1.data_standard_v2 import _common as C
        from app.models.source_data import StandardVersion
        await C._delete_standard_children(db_session, s.id)
        ver = (await db_session.execute(
            select(StandardVersion).where(StandardVersion.standard_id == s.id)
        )).scalars().first()
        ver.snapshot_data = legacy
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/{ver.version_number}",
            headers={"X-API-Key": raw})
        assert resp.status_code == 201, resp.text

        # 主归属被补齐（这条不变量不能破）
        links = await _fetch_where(db_session, select(StandardLayerGroupTableLink).where(
            StandardLayerGroupTableLink.table_id == seeded["table"].id))
        assert len(links) == 1, f"老快照回滚后主归属应有 1 条，实际 {len(links)}"
        assert links[0].is_primary is True
        assert links[0].layer_group_id == seeded["root"].id

        # 字段与字典仍恢复；缺列走默认值而不是 KeyError
        fields = await _fetch(db_session, StandardField, s.id)
        assert [f.field_name for f in fields] == ["材质"]
        assert fields[0].field_alias is None
        dicts = await _fetch(db_session, StandardDictionary, s.id)
        assert len(dicts) == 1
        assert dicts[0].dict_code is None          # 老快照没这列 → None
        assert dicts[0].description is None

    async def test_rollback_legacy_snapshot_missing_ordering_key(self, client, db_session):
        """老快照的 field dict 里连 ordering 键都没有 → 不得 KeyError。"""
        from app.api.v1.data_standard_v2.versions import _restore_from_snapshot
        from app.api.v1.data_standard_v2 import _common as C

        raw, key = await _key(db_session, "snap_legacy_2")
        s = await _std(db_session, key, "老快照无ordering")
        seeded = await _seed_full_fidelity(db_session, s)
        await db_session.commit()

        await C._delete_standard_children(db_session, s.id)
        await db_session.flush()

        minimal = {
            "standard": {"name": s.name},
            "layer_groups": [{
                "id": seeded["root"].id, "group_name": "根层", "parent_id": None,
                "tables": [{
                    "id": seeded["table"].id, "table_name": "路面",
                    "fields": [{"id": seeded["field_b"].id, "field_name": "材质"}],
                }],
                "children": [],
            }],
            "dictionaries": [{
                "id": seeded["dict"].id, "dict_name": "材质字典",
                "items": [{"item_code": "BR", "item_name": "砖"}],
            }],
            # 边有，但每条都缺 ordering / is_primary 等可选键
            "field_dict_links": [
                {"id": str(uuid.uuid4()), "field_id": seeded["field_b"].id,
                 "dict_id": seeded["dict"].id},
            ],
        }
        await _restore_from_snapshot(db_session, s.id, minimal)
        await db_session.commit()

        links = await _fetch_where(db_session, select(StandardFieldDictLink))
        mine = [l for l in links if l.field_id == seeded["field_b"].id]
        assert len(mine) == 1
        assert mine[0].is_primary is False
        assert mine[0].ordering == 0
        assert mine[0].filter_condition is None
