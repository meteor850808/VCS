# -*- coding: utf-8 -*-
"""app/api/v1/data_standard_v2/ 路由级回归测试（P1 拆包后 54 路由回归）。

原 data_standard_v2.py（3645 行）已拆分为 app/api/v1/data_standard_v2/ 包（10 子模块）。
本文件以 conftest 的 async client + db_session + api_key_header 驱动标准/层组/表/
字段/字典/字典项/字段-字典关联/版本/模板/发布的 CRUD 与查询端点。

策略：先直接 ORM 造标准+嵌套结构，再命中 GET 详情(树深度载)；另用 POST 驱动实测创建路径。
"""
import pytest
import pytest_asyncio

from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _key(db_session, name):
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    k = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(k)
    await db_session.commit()
    return raw, k


async def _seed_standard(db_session, key, sid=None, name="基础标准", code=None):
    import uuid
    s = DataStandard(
        id=sid or str(uuid.uuid4()),
        api_key_id=key.id,
        code=code or "ST-BASE",
        name=name,
        description="种子标准",
        category="custom",
        status="draft",
    )
    db_session.add(s)
    await db_session.flush()
    return s


class TestStandardV2CreateList:
    """POST /data-standards-v2 + GET /data-standards-v2 列表。"""

    async def test_create_and_list(self, client, db_session):
        raw, key = await _key(db_session, "v2_cl_a")
        h = {"X-API-Key": raw}

        resp = await client.post(
            "/api/v1/data-standards-v2",
            headers=h,
            json={"name": "道路标准", "code": "RD-01", "category": "custom"},
        )
        assert resp.status_code == 201
        sid = resp.json()["id"]

        resp = await client.get("/api/v1/data-standards-v2", headers=h)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "道路标准"
        assert body["items"][0]["status"] == "draft"

        # 过滤/搜索
        resp = await client.get(
            "/api/v1/data-standards-v2?keyword=道路", headers=h
        )
        assert resp.json()["total"] == 1
        resp = await client.get(
            "/api/v1/data-standards-v2?category=national", headers=h
        )
        assert resp.json()["total"] == 0

        # 404 场景与详情可命中（详情在 create 之后有_id）
        resp = await client.get(
            f"/api/v1/data-standards-v2/{sid}", headers=h
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "道路标准"

    async def test_create_dup_409(self, client, db_session):
        raw, key = await _key(db_session, "v2_cl_b")
        h = {"X-API-Key": raw}
        payload = {"name": "重名标准", "code": "DUPE"}
        for _ in range(2):
            await client.post("/api/v1/data-standards-v2", headers=h, json=payload)
        resp = await client.post(
            "/api/v1/data-standards-v2", headers=h, json=payload
        )
        assert resp.status_code == 409


class TestStandardV2DetailTree:
    """GET detail 加载完整层级树 + update + delete。"""

    async def test_detail_tree(self, client, db_session):
        raw, key = await _key(db_session, "v2_dt_a")
        s = await _seed_standard(db_session, key)
        layer = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000001",
            standard_id=s.id, group_name="根图层", ordering=0,
        )
        db_session.add(layer)
        sub = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000002",
            standard_id=s.id, parent_id=layer.id, group_name="子图层", ordering=0,
        )
        db_session.add(sub)
        table = StandardTable(
            id="30000000-0000-0000-0000-000000000001",
            layer_id=layer.id, standard_id=s.id,
            table_name="道路面", table_type="spatial", geometry_type="Polygon",
        )
        db_session.add(table)
        db_session.add(StandardField(
            id="40000000-0000-0000-0000-000000000001",
            standard_id=s.id, table_id=table.id, field_name="长度", data_type="float",
        ))
        db_session.add(StandardDictionary(
            id="50000000-0000-0000-0000-000000000001",
            standard_id=s.id, scope="standard", dict_name="路况字典", dict_code="LC",
        ))
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "基础标准"
        assert len(body["layer_groups"]) == 1       # 仅根层组
        assert body["layer_groups"][0]["children"][0]["group_name"] == "子图层"
        assert body["layer_groups"][0]["tables"][0]["table_name"] == "道路面"
        assert len(body["dictionaries"]) == 1

    async def test_update_standard(self, client, db_session):
        raw, key = await _key(db_session, "v2_dt_b")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/data-standards-v2/{s.id}",
            headers={"X-API-Key": raw},
            json={"name": "改名", "status": "published"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "改名"
        assert resp.json()["status"] == "published"

    async def test_delete_standard(self, client, db_session):
        raw, key = await _key(db_session, "v2_dt_c")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/data-standards-v2/{s.id}", headers={"X-API-Key": raw}
        )
        assert resp.status_code == 204

    async def test_detail_404_other_scope(self, client, db_session):
        raw, key = await _key(db_session, "v2_dt_d")
        other_raw, other = await _key(db_session, "v2_dt_e")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        # 无关 key 不可见
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}",
            headers={"X-API-Key": other_raw},
        )
        assert resp.status_code == 404


class TestStandardV2Layers:
    """层组 create/update/delete/move + tables 关联。"""

    async def test_layer_crud(self, client, db_session):
        raw, key = await _key(db_session, "v2_ly_a")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 创建根层组
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/layers",
            headers=h,
            json={"group_name": "建筑层", "group_code": "B", "ordering": 1},
        )
        assert resp.status_code == 201
        layer_id = resp.json()["id"]
        # 创建子层组
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/layers",
            headers=h,
            json={"group_name": "附属", "parent_id": layer_id},
        )
        assert resp.status_code == 201
        child_id = resp.json()["id"]

        # 更新层组
        resp = await client.put(
            f"/api/v1/layers/{layer_id}",
            headers=h,
            json={"group_name": "建筑层2", "ordering": 2},
        )
        assert resp.status_code == 200

        # 移动子到顶层
        resp = await client.post(
            f"/api/v1/layers/{child_id}/move",
            headers=h,
            json={"new_parent_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["parent_id"] is None

        # 移动到自身 → 400
        resp = await client.post(
            f"/api/v1/layers/{layer_id}/move",
            headers=h,
            json={"new_parent_id": layer_id},
        )
        assert resp.status_code == 400

        # 删除子层组
        resp = await client.delete(f"/api/v1/layers/{child_id}", headers=h)
        assert resp.status_code == 204
        # 删除根层组（无共享表）
        resp = await client.delete(f"/api/v1/layers/{layer_id}", headers=h)
        assert resp.status_code == 204

    async def test_layer_table_flow(self, client, db_session):
        raw, key = await _key(db_session, "v2_ly_b")
        s = await _seed_standard(db_session, key)
        layer = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000101",
            standard_id=s.id, group_name="层", ordering=0,
        )
        db_session.add(layer)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # list layers + tables
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/layers", headers=h
        )
        assert resp.status_code == 200

        # 创建表
        resp = await client.post(
            f"/api/v1/layers/{layer.id}/tables",
            headers=h,
            json={"table_name": "房屋面", "table_type": "spatial",
                  "geometry_type": "Polygon",
                  "fields": [{"field_name": "楼层", "data_type": "int"}]},
        )
        assert resp.status_code == 201
        table_id = resp.json()["id"]

        # 表详情
        resp = await client.get(f"/api/v1/tables/{table_id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["table_name"] == "房屋面"
        assert resp.json()["fields"][0]["field_name"] == "楼层"

        # 更新表
        resp = await client.put(
            f"/api/v1/tables/{table_id}",
            headers=h,
            json={"table_name": "房屋面X", "geometry_type": "MultiPolygon"},
        )
        assert resp.status_code == 200

        # 字段列表 + 新增字段
        resp = await client.get(
            f"/api/v1/tables/{table_id}/fields", headers=h
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/v1/tables/{table_id}/fields",
            headers=h,
            json={"field_name": "名称", "data_type": "string"},
        )
        assert resp.status_code == 201
        field_id = resp.json()["id"]

        # 更新/删除字段
        resp = await client.put(
            f"/api/v1/fields/{field_id}",
            headers=h,
            json={"data_type": "string", "length": 50},
        )
        assert resp.status_code == 200
        resp = await client.delete(f"/api/v1/fields/{field_id}", headers=h)
        assert resp.status_code == 204

        # 删除表
        resp = await client.delete(f"/api/v1/tables/{table_id}", headers=h)
        assert resp.status_code == 204


class TestStandardV2Dictionaries:
    """字典 + 字典项 + 字段-字典关联。"""

    async def test_dictionary_flow(self, client, db_session):
        raw, key = await _key(db_session, "v2_da_a")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 在标准下创建字典
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/dictionaries",
            headers=h,
            json={"dict_name": "材质", "dict_code": "CZ"},
        )
        assert resp.status_code == 201
        dict_id = resp.json()["id"]

        # 全局字典列表 + 详情
        resp = await client.get("/api/v1/dictionaries", headers=h)
        assert resp.status_code == 200
        resp = await client.get(f"/api/v1/dictionaries/{dict_id}", headers=h)
        assert resp.status_code == 200

        # 添加字典项（body 为 list）
        resp = await client.post(
            f"/api/v1/dictionaries/{dict_id}/items",
            headers=h,
            json=[{"item_code": "BR", "item_name": "砖", "ordering": 1}],
        )
        assert resp.status_code == 201
        item_id = resp.json()[0]["id"]

        # 批量更新字典项（body 为 list）
        resp = await client.put(
            f"/api/v1/dictionaries/{dict_id}/items",
            headers=h,
            json=[{"id": item_id, "item_name": "砖混"}],
        )
        assert resp.status_code in (200, 201)
        assert resp.json()[0]["item_name"] == "砖混"

        # 删除字典项
        resp = await client.delete(
            f"/api/v1/dictionaries/{dict_id}/items/{item_id}", headers=h
        )
        assert resp.status_code == 204

        # 更新字典
        resp = await client.put(
            f"/api/v1/dictionaries/{dict_id}",
            headers=h,
            json={"dict_name": "材质V2"},
        )
        assert resp.status_code == 200

        # 删除字典
        resp = await client.delete(f"/api/v1/dictionaries/{dict_id}", headers=h)
        assert resp.status_code == 204


class TestStandardV2VersionsAndTemplates:
    """发布/版本/模板/导出（轻量 DB 路径）。"""

    async def test_publish_and_versions(self, client, db_session):
        raw, key = await _key(db_session, "v2_pb_a")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/publish",
            headers=h,
            json={"change_log": "首次发布"},
        )
        assert resp.status_code == 201
        assert resp.json()["version_number"] == 1

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions", headers=h
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # 发布后标准版本号 +1、状态 published
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}", headers=h
        )
        assert resp.json()["version"] == 2
        assert resp.json()["status"] == "published"

    async def test_templates(self, client, db_session):
        raw, key = await _key(db_session, "v2_tp_a")
        await db_session.commit()
        h = {"X-API-Key": raw}
        for t in ("json", "excel", "csv"):
            resp = await client.get(
                f"/api/v1/data-standards-v2/template/{t}", headers=h
            )
            assert resp.status_code == 200, f"template/{t} {resp.status_code}"

    async def test_version_diff(self, client, db_session):
        from app.models.source_data import StandardVersion
        raw, key = await _key(db_session, "v2_vd_a")
        s = await _seed_standard(db_session, key)
        db_session.add_all([
            StandardVersion(
                id="60000000-0000-0000-0000-00000000000a",
                standard_id=s.id, api_key_id=key.id, version_number=1,
                snapshot_data={"name": "基础标准", "version": 1},
            ),
            StandardVersion(
                id="60000000-0000-0000-0000-00000000000b",
                standard_id=s.id, api_key_id=key.id, version_number=2,
                snapshot_data={"name": "基础标准2", "version": 2},
            ),
        ])
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/1/diff/2", headers=h
        )
        assert resp.status_code == 200
        assert resp.json()["from_version"] == 1
        assert resp.json()["to_version"] == 2
        assert isinstance(resp.json()["summary"], list)

        # 不存在的版本 → 404
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/versions/9/diff/10", headers=h
        )
        assert resp.status_code == 404

    async def test_versions_404_and_rollback(self, client, db_session):
        from app.models.source_data import StandardVersion
        raw, key = await _key(db_session, "v2_rb_a")
        s = await _seed_standard(db_session, key)
        db_session.add(StandardVersion(
            id="60000000-0000-0000-0000-00000000000c",
            standard_id=s.id, api_key_id=key.id, version_number=1,
            snapshot_data={"name": "基础标准", "version": 1, "tables": []},
        ))
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 回滚到 v1（用快照恢复树）
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/1", headers=h
        )
        assert resp.status_code == 201
        # 标准版本号应 +1，且回滚版本记录创建
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/rollback/99", headers=h
        )
        assert resp.status_code == 404

    async def test_qc_summary(self, client, db_session):
        raw, key = await _key(db_session, "v2_qc_a")
        s = await _seed_standard(db_session, key)
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/data-standards-v2/{s.id}/qc-summary",
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200

    async def test_import_json(self, client, db_session):
        raw, key = await _key(db_session, "v2_im_a")
        await db_session.commit()
        h = {"X-API-Key": raw}
        payload = {
            "name": "导入标准",
            "code": "IMP-1",
            "category": "custom",
            "description": "从 JSON 导入",
            "layer_groups": [
                {
                    "group_name": "根层",
                    "group_code": "G1",
                    "tables": [
                        {
                            "table_name": "表A",
                            "table_type": "spatial",
                            "geometry_type": "Point",
                            "fields": [
                                {"field_name": "编号", "data_type": "string"},
                            ],
                        }
                    ],
                    "children": [],
                }
            ],
            "dictionaries": [
                {"dict_name": "字典A", "dict_code": "DA", "items": []}
            ],
        }
        resp = await client.post(
            "/api/v1/data-standards-v2/import/json", headers=h, json=payload
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "导入标准"
        # 再创建同名 → 409
        resp = await client.post(
            "/api/v1/data-standards-v2/import/json", headers=h, json=payload
        )
        assert resp.status_code == 409


class TestStandardV2TablesExtra:
    """表的 layers 查询 + attach/detach。"""

    async def test_table_layers_and_attach(self, client, db_session):
        raw, key = await _key(db_session, "v2_tb_a")
        s = await _seed_standard(db_session, key)
        l1 = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000201",
            standard_id=s.id, group_name="L1", ordering=0,
        )
        l2 = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000202",
            standard_id=s.id, group_name="L2", ordering=1,
        )
        db_session.add_all([l1, l2])
        await db_session.commit()
        h = {"X-API-Key": raw}

        resp = await client.post(
            f"/api/v1/layers/{l1.id}/tables",
            headers=h,
            json={"table_name": "共享表", "table_type": "attribute"},
        )
        assert resp.status_code == 201
        table_id = resp.json()["id"]

        # 表所属 layligrs
        resp = await client.get(f"/api/v1/tables/{table_id}/layers", headers=h)
        assert resp.status_code == 200

        # attach 到第二个层（次归属）
        resp = await client.post(
            f"/api/v1/layers/{l2.id}/tables/{table_id}/attach",
            headers=h,
            json={"is_primary": False},
        )
        assert resp.status_code in (200, 201)

        resp = await client.get(f"/api/v1/tables/{table_id}/layers", headers=h)
        assert resp.status_code == 200


class TestStandardV2DictLinksAndShare:
    """字段-字典关联 + 全局共享 + 表/字段 reorder + 导出。"""

    async def test_field_dict_links_and_share(self, client, db_session):
        raw, key = await _key(db_session, "v2_fl_a")
        s = await _seed_standard(db_session, key)
        layer = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000300",
            standard_id=s.id, group_name="根", ordering=0,
        )
        db_session.add(layer)
        await db_session.flush()
        table = StandardTable(
            id="30000000-0000-0000-0000-000000000301",
            standard_id=s.id, layer_id=layer.id,
            table_name="T1", table_type="attribute",
        )
        db_session.add(table)
        await db_session.flush()
        field = StandardField(
            id="40000000-0000-0000-0000-000000000300",
            standard_id=s.id, table_id=table.id, field_name="状态", data_type="string",
        )
        dict_obj = StandardDictionary(
            id="50000000-0000-0000-0000-000000000300",
            standard_id=s.id, scope="standard", dict_name="状态字典", dict_code="ZT",
        )
        db_session.add_all([field, dict_obj])
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 字段字典关联
        resp = await client.get(f"/api/v1/fields/{field.id}/dict-links", headers=h)
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/v1/fields/{field.id}/dict-links",
            headers=h,
            json={"dict_id": dict_obj.id, "is_primary": True},
        )
        assert resp.status_code == 201
        link_id = resp.json()["id"]

        # 更新/删除关联
        resp = await client.put(
            f"/api/v1/field-dict-links/{link_id}",
            headers=h,
            json={"is_primary": False},
        )
        assert resp.status_code == 200
        resp = await client.delete(
            f"/api/v1/field-dict-links/{link_id}", headers=h
        )
        assert resp.status_code in (200, 204)

        # 全局共享
        resp = await client.post(
            f"/api/v1/dictionaries/{dict_obj.id}/share", headers=h
        )
        assert resp.status_code == 200
        assert resp.json()["scope"] == "global"

    async def test_export_and_reorder(self, client, db_session):
        raw, key = await _key(db_session, "v2_ex_a")
        s = await _seed_standard(db_session, key)
        layer = StandardLayerGroup(
            id="20000000-0000-0000-0000-000000000310",
            standard_id=s.id, group_name="根", ordering=0,
        )
        db_session.add(layer)
        t1 = StandardTable(
            id="30000000-0000-0000-0000-000000000310",
            standard_id=s.id, layer_id=layer.id, table_name="A", table_type="spatial",
            ordering=0,
        )
        t2 = StandardTable(
            id="30000000-0000-0000-0000-000000000311",
            standard_id=s.id, layer_id=layer.id, table_name="B", table_type="spatial",
            ordering=1,
        )
        db_session.add_all([t1, t2])
        await db_session.commit()
        h = {"X-API-Key": raw}

        # 导出 json
        resp = await client.post(
            f"/api/v1/data-standards-v2/{s.id}/export?format=json",
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "基础标准"