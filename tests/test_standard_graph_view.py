# -*- coding: utf-8 -*-
"""知识图谱纯视图（A 档）— 端点契约。

v2.22.3 调研结论：不引入图数据库，`GET /{id}/graph` 直接从 ORM 出
nodes/edges（本仓模型本已是一张图）。真实参照物（一厅八室五标准）最大
SHGT_SURVEY 有 255 表 / 3288 字段 / 2031 字典条目 —— **摘要端点绝不能**
把字段与条目明细塞进响应（否则 ~1.5MB），展开走独立明细端点懒加载。

契约要点：
- 摘要：nodes（standard/group/table/dict）+ edges（contains/binds）；
  表节点带 field_count 徽章、字典节点带 item_count 徽章；**无明细数组**
- binds 聚合到「表↔字典」级：同表多字段绑同一字典 → 一条边 + field_count
- 全局字典按引用纳入：被本标准字段绑定的 global 字典进图，
  未被引用的 global 字典**不得**混进来（否则图被无关节点污染）
- 明细：表节点 → 字段列表（含 dict_codes）；字典节点 → 条目（含 parent_code）
- 归属：别人家的标准 / 别人家的节点 → 404
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


async def _std(db_session, key, name, code):
    s = DataStandard(
        id=str(uuid.uuid4()), api_key_id=key.id, code=code, name=name,
        category="custom", status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _seed_graph_fixture(db_session, std):
    """造一张有代表性的图：

    根层 BG ── 子层 CH（嵌套边）
      └ 表 ROAD（3 字段）
           ├ 材质 → 字典 CZ（主）+ 字典 XZ（次）   ← 一字段双绑定
           ├ 材质2 → 字典 CZ（次）                 ← 同表同字典第二字段 → 聚合
           └ 路况 → 全局字典 GLQ（跨标准共享）
    字典 CZ：2 条目，BR 的 parent_code=ST（层级）
    未引用的全局字典 GLQ2：不得出现在图里
    """
    root = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id, group_name="根层",
        group_code="BG", ordering=0)
    db_session.add(root)
    await db_session.flush()
    child = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id, parent_id=root.id,
        group_name="子层", group_code="CH", ordering=0)
    db_session.add(child)
    await db_session.flush()

    table = StandardTable(
        id=str(uuid.uuid4()), layer_id=root.id, standard_id=std.id,
        table_name="道路", table_code="ROAD", table_type="spatial",
        geometry_type="LineString", ordering=0)
    db_session.add(table)
    await db_session.flush()

    f_mat = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="材质", field_alias="MAT", data_type="string",
        is_required=True, ordering=1)
    f_mat2 = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="材质备注", data_type="string", ordering=2)
    f_cond = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="路况", data_type="string", ordering=3)
    f_extra = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=table.id,
        field_name="附加字段", data_type="string", ordering=4)
    db_session.add_all([f_mat, f_mat2, f_cond, f_extra])
    await db_session.flush()

    cz = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=std.id, scope="standard",
        dict_name="材质字典", dict_code="CZ")
    xz = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=std.id, scope="standard",
        dict_name="形状字典", dict_code="XZ")
    db_session.add_all([cz, xz])
    await db_session.flush()

    st_item = DictionaryItem(
        id=str(uuid.uuid4()), dict_id=cz.id, item_code="ST", item_name="石", ordering=1)
    db_session.add(st_item)
    await db_session.flush()
    db_session.add(DictionaryItem(
        id=str(uuid.uuid4()), dict_id=cz.id, item_code="BR", item_name="砖",
        parent_code="ST", ordering=2))
    db_session.add(DictionaryItem(
        id=str(uuid.uuid4()), dict_id=xz.id, item_code="L", item_name="线"))
    await db_session.flush()

    # 全局字典：GLQ 被引用（应进图）、GLQ2 未引用（不得进图）
    glq = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=None, api_key_id=None,
        scope="global", dict_name="全局路况", dict_code="GLQ")
    glq2 = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=None, api_key_id=None,
        scope="global", dict_name="没人用的全局", dict_code="GLQ2")
    db_session.add_all([glq, glq2])
    await db_session.flush()
    db_session.add(DictionaryItem(
        id=str(uuid.uuid4()), dict_id=glq.id, item_code="GOOD", item_name="良好"))

    # S9 次归属：ROAD 主归属根层，同时次归属子层 → contains 边带 primary=False
    db_session.add(StandardLayerGroupTableLink(
        id=str(uuid.uuid4()), layer_group_id=child.id, table_id=table.id,
        is_primary=False, ordering=0))

    # 悬挂边场景：别的标准的字典被本标准字段绑定（DB 层合法存在的脏状态）
    # —— 图里既不显示该字典节点，也不得出现指向它的边。
    std_other = DataStandard(
        id=str(uuid.uuid4()), api_key_id=std.api_key_id,
        code=f"ST-FD-{uuid.uuid4().hex[:6]}", name="外字典宿主",
        category="custom", status="draft")
    db_session.add(std_other)
    await db_session.flush()
    foreign_dict = StandardDictionary(
        id=str(uuid.uuid4()), standard_id=std_other.id, scope="standard",
        dict_name="别人家的字典", dict_code="FD")
    db_session.add(foreign_dict)
    await db_session.flush()

    db_session.add_all([
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_mat.id,
                              dict_id=cz.id, is_primary=True, ordering=0),
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_mat.id,
                              dict_id=xz.id, is_primary=False, ordering=1),
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_mat2.id,
                              dict_id=cz.id, is_primary=False, ordering=0),
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_cond.id,
                              dict_id=glq.id, is_primary=True, ordering=0),
        # 附加字段：绑 CZ（可见，拉开 linked≠binds 差距）+ 外字典（悬挂）
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_extra.id,
                              dict_id=cz.id, is_primary=False, ordering=1),
        StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f_extra.id,
                              dict_id=foreign_dict.id, is_primary=True, ordering=0),
    ])
    await db_session.flush()
    return {"root": root, "child": child, "table": table,
            "f_mat": f_mat, "f_mat2": f_mat2, "cz": cz, "xz": xz,
            "glq": glq, "glq2": glq2, "foreign": foreign_dict}


BASE = "/api/v1/data-standards-v2"


class TestGraphSummary:
    async def test_summary_shape_and_no_detail_leak(self, client, db_session):
        raw, key = await _key(db_session, "graph_sum_1")
        s = await _std(db_session, key, "图谱摘要", "ST-GRAPH-1")
        fx = await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw})
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["standard_id"] == s.id
        assert body["standard_name"] == "图谱摘要"
        assert set(body.keys()) >= {"standard_id", "standard_name", "nodes", "edges", "stats"}

        kinds = {n["kind"] for n in body["nodes"]}
        assert kinds == {"standard", "group", "table", "dict"}, kinds
        ids = {n["id"] for n in body["nodes"]}
        # standard 根 + BG + CH + ROAD + CZ + XZ + GLQ（被引用）= 7
        # 外字典 FD 不显示（别人家的），GLQ2 不显示（未引用）
        assert len(body["nodes"]) == 7, [n["name"] for n in body["nodes"]]
        assert s.id in ids
        assert {fx["root"].id, fx["child"].id, fx["table"].id,
                fx["cz"].id, fx["xz"].id, fx["glq"].id} <= ids
        # 未引用的全局字典不得混入；外字典也不得混入
        assert fx["glq2"].id not in ids
        assert fx["foreign"].id not in ids

        # 摘要契约：任何节点都不得携带明细数组（防 ~1.5MB 载荷回潮）
        for n in body["nodes"]:
            assert "fields" not in n, n
            assert "items" not in n, n
        # 字段名绝不该出现在摘要里（它们属于明细端点）
        import json as _json
        assert "材质备注" not in _json.dumps(body, ensure_ascii=False)

        # 徽章：表带 field_count、字典带 item_count
        tn = next(n for n in body["nodes"] if n["id"] == fx["table"].id)
        assert tn["field_count"] == 4
        czn = next(n for n in body["nodes"] if n["id"] == fx["cz"].id)
        assert czn["item_count"] == 2
        glqn = next(n for n in body["nodes"] if n["id"] == fx["glq"].id)
        assert glqn["scope"] == "global"
        assert glqn["item_count"] == 1

    async def test_contains_edges_follow_hierarchy(self, client, db_session):
        raw, key = await _key(db_session, "graph_edge_1")
        s = await _std(db_session, key, "图谱层级", "ST-GRAPH-2")
        fx = await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw})
        body = r.json()
        contains = [e for e in body["edges"] if e["kind"] == "contains"]
        pairs = {(e["source"], e["target"]) for e in contains}

        assert (s.id, fx["root"].id) in pairs          # standard → 根层组
        assert (fx["root"].id, fx["child"].id) in pairs  # 层组 → 子层组（嵌套）
        # 层组 → 表：主归属（layer_id）+ S9 次归属（table_links）各一条
        edge_r = next(e for e in contains
                      if e["source"] == fx["root"].id and e["target"] == fx["table"].id)
        assert edge_r["primary"] is True
        edge_c = next(e for e in contains
                      if e["source"] == fx["child"].id and e["target"] == fx["table"].id)
        assert edge_c["primary"] is False  # S9 次归属也要画出来

    async def test_binds_edges_aggregate_per_table_dict(self, client, db_session):
        raw, key = await _key(db_session, "graph_bind_1")
        s = await _std(db_session, key, "图谱绑定", "ST-GRAPH-3")
        fx = await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw})
        binds = [e for e in r.json()["edges"] if e["kind"] == "binds"]
        by_pair = {(e["source"], e["target"]): e for e in binds}

        # ROAD→CZ：材质(主) + 材质备注(次) + 附加字段(次) 聚合为一条
        e_cz = by_pair[(fx["table"].id, fx["cz"].id)]
        assert e_cz["field_count"] == 3
        assert e_cz["primary"] is True  # 任一绑定为主字典即标记
        # ROAD→XZ：仅材质(次)
        e_xz = by_pair[(fx["table"].id, fx["xz"].id)]
        assert e_xz["field_count"] == 1
        assert e_xz["primary"] is False
        # ROAD→GLQ：全局字典被引用 → 进图
        e_glq = by_pair[(fx["table"].id, fx["glq"].id)]
        assert e_glq["field_count"] == 1
        # 3 条边，不多不少：外字典的绑定**不得**产生悬挂边
        assert len(binds) == 3, binds
        assert fx["foreign"].id not in {e["target"] for e in binds}

    async def test_stats_block(self, client, db_session):
        raw, key = await _key(db_session, "graph_stat_1")
        s = await _std(db_session, key, "图谱统计", "ST-GRAPH-4")
        await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw})
        st = r.json()["stats"]
        assert st["group_count"] == 2
        assert st["table_count"] == 1
        assert st["field_count"] == 4
        assert st["dict_count"] == 3        # CZ + XZ + GLQ（不含 GLQ2/外字典）
        # 4 个绑定字段 vs 3 条聚合边 —— 两个口径必须分开（聚合场景）
        assert st["linked_field_count"] == 4
        assert st["binds_edge_count"] == 3

    async def test_404_standard_missing_or_foreign(self, client, db_session):
        raw_a, key_a = await _key(db_session, "graph_404_a")
        raw_b, key_b = await _key(db_session, "graph_404_b")
        s = await _std(db_session, key_b, "别人家的标准", "ST-GRAPH-X")
        await db_session.commit()

        r1 = await client.get(f"{BASE}/no-such-std/graph", headers={"X-API-Key": raw_a})
        assert r1.status_code == 404
        r2 = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw_a})
        assert r2.status_code == 404  # 归属校验，不能泄露存在性


class TestGraphNodeDetail:
    async def test_table_detail_returns_fields_with_dict_codes(self, client, db_session):
        raw, key = await _key(db_session, "graph_det_1")
        s = await _std(db_session, key, "表明细", "ST-GRAPH-D1")
        fx = await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph/nodes/{fx['table'].id}",
                             headers={"X-API-Key": raw})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "table"
        assert body["name"] == "道路"
        fields = body["fields"]
        assert [f["field_name"] for f in fields] == ["材质", "材质备注", "路况", "附加字段"]

        mat = fields[0]
        assert mat["field_alias"] == "MAT"
        assert mat["is_required"] is True
        assert mat["dict_codes"] == ["CZ", "XZ"]  # 双绑定保序：主在前
        assert fields[2]["dict_codes"] == ["GLQ"]
        # 附加字段绑了 CZ + 外字典 FD（DB 真实状态，主绑定在前）
        assert fields[3]["dict_codes"] == ["FD", "CZ"]

    async def test_dict_detail_returns_items_with_hierarchy(self, client, db_session):
        raw, key = await _key(db_session, "graph_det_2")
        s = await _std(db_session, key, "字典明细", "ST-GRAPH-D2")
        fx = await _seed_graph_fixture(db_session, s)
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph/nodes/{fx['cz'].id}",
                             headers={"X-API-Key": raw})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "dict"
        assert body["name"] == "材质字典"
        items = body["items"]
        assert [(i["item_code"], i["parent_code"]) for i in items] == [
            ("ST", None), ("BR", "ST")]  # ordering 保序 + 层级

        # 被引用的全局字典也能展开
        r2 = await client.get(f"{BASE}/{s.id}/graph/nodes/{fx['glq'].id}",
                              headers={"X-API-Key": raw})
        assert r2.status_code == 200
        assert r2.json()["items"][0]["item_code"] == "GOOD"

    async def test_detail_404s(self, client, db_session):
        raw, key = await _key(db_session, "graph_det_3")
        other_raw, other_key = await _key(db_session, "graph_det_4")
        s = await _std(db_session, key, "明细404", "ST-GRAPH-D3")
        fx = await _seed_graph_fixture(db_session, s)

        # 另一个标准 + 它自己的表 —— 不属于 s 的图
        s2 = await _std(db_session, key, "隔壁标准", "ST-GRAPH-D4")
        t2 = StandardTable(
            id=str(uuid.uuid4()), layer_id=fx["root"].id, standard_id=s2.id,
            table_name="隔壁表", table_code="OTHER")
        db_session.add(t2)
        await db_session.commit()

        h = {"X-API-Key": raw}
        # 未知 id
        r1 = await client.get(f"{BASE}/{s.id}/graph/nodes/{uuid.uuid4()}", headers=h)
        assert r1.status_code == 404
        # 别人的表（挂在同一层组下也拦得住 —— 按 standard_id 校验）
        r2 = await client.get(f"{BASE}/{s.id}/graph/nodes/{t2.id}", headers=h)
        assert r2.status_code == 404
        # 未引用的全局字典不属于本图
        r3 = await client.get(f"{BASE}/{s.id}/graph/nodes/{fx['glq2'].id}", headers=h)
        assert r3.status_code == 404
        # 层组节点不支持展开（摘要已完整表达层级）
        r4 = await client.get(f"{BASE}/{s.id}/graph/nodes/{fx['root'].id}", headers=h)
        assert r4.status_code == 404
        # 别人的 key 访问整张图
        r5 = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": other_raw})
        assert r5.status_code == 404

    async def test_empty_standard_yields_minimal_graph(self, client, db_session):
        """空标准也要给合法图（standard 根 + 空集合），不是 500。"""
        raw, key = await _key(db_session, "graph_empty_1")
        s = await _std(db_session, key, "空标准", "ST-GRAPH-E")
        await db_session.commit()

        r = await client.get(f"{BASE}/{s.id}/graph", headers={"X-API-Key": raw})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [n["kind"] for n in body["nodes"]] == ["standard"]
        assert body["edges"] == []
        assert body["stats"]["table_count"] == 0
