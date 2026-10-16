"""约束冲突的错误信息必须说清是哪条约束、撞了什么。

背景：v2.22.3 之前 6 处 `except IntegrityError:` 把**任何**约束冲突都报成
「标准名称已存在」。实证缺陷：新建标准时标准**编码**已被占用（名称全新），
返回的却是「标准名称 'X' 已存在」—— 用户按提示改名字永远改不通。

修复：按 PG 唯一约束名（`uq_*`）把冲突翻译成对应的实体与字段，未知约束
保留通用文案 + 原始约束名，不再谎报「名称已存在」。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"C:\Users\Administrator\Desktop\AI项目\空间质检")
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.api.v1.data_standard_v2._common import _conflict_detail  # noqa: E402
from app.models.data_standard import DataStandard  # noqa: E402

from tests.test_imp_exp_dict_roundtrip import _key  # noqa: E402


@pytest.mark.asyncio
async def test_code_conflict_says_code_not_name(client, db_session):
    """编码撞车（名称全新）→ 必须说编码，不能说名称。"""
    raw, key = await _key(db_session, "cf_a")
    db_session.add(DataStandard(id="cf-a1", api_key_id=key.id,
                                code="TAKEN", name="已占用的编码"))
    await db_session.commit()

    r = await client.post("/api/v1/data-standards-v2", headers={"X-API-Key": raw},
                          json={"name": "全新的名字", "code": "TAKEN"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "标准编码" in detail, detail
    assert "TAKEN" in detail, detail
    # 名称并未冲突，不得谎报
    assert "标准名称" not in detail, detail


@pytest.mark.asyncio
async def test_name_conflict_says_name(client, db_session):
    """名称撞车（编码全新）→ 说名称，但不得把无辜的编码也带进来。"""
    raw, key = await _key(db_session, "cf_b")
    db_session.add(DataStandard(id="cf-b1", api_key_id=key.id,
                                code="OTHER", name="撞名"))
    await db_session.commit()

    r = await client.post("/api/v1/data-standards-v2", headers={"X-API-Key": raw},
                          json={"name": "撞名", "code": "BRAND_NEW"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "标准名称" in detail, detail
    assert "撞名" in detail, detail


@pytest.mark.asyncio
async def test_import_child_conflict_names_the_real_entity(client, db_session):
    """导入时的子表冲突不得报成「标准名称已存在」——那是另一个实体。

    用重复表名造冲突（uq_st_layer_name）。注意不能用重复字段名造：
    v2.22.3 起行级重复（字段名/字典项）走「保首个 + 告警」不再 409
    （真实源数据五份全有字段重名，PLAN 达 116 处）；结构级重复
    （表/字典）仍硬失败但按真实约束归因。
    """
    raw, key = await _key(db_session, "cf_c")
    payload = {
        "name": "冲突导入", "layer_groups": [{
            "group_name": "道路",
            "tables": [
                {"table_name": "路面", "table_code": "SURF_A",
                 "geometry_type": "polygon", "fields": []},
                {"table_name": "路面", "table_code": "SURF_B",
                 "geometry_type": "polygon", "fields": []},
            ],
        }],
    }
    r = await client.post("/api/v1/data-standards-v2/import/json",
                          headers={"X-API-Key": raw}, json=payload)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    # 重复的是「数据表名称」，不是「标准名称」
    assert "数据表" in detail, detail
    assert "标准名称" not in detail, detail


class TestImportRowLevelDeduplication:
    """行级重复（同表字段名/同字典条目码）保首个 + 告警，不再整单 409。"""

    async def test_duplicate_fields_and_items_deduped_with_warning(
            self, client, db_session):
        raw, key = await _key(db_session, "cf_dedup")
        payload = {
            "name": "去重导入", "layer_groups": [{
                "group_name": "道路",
                "tables": [{
                    "table_name": "路面", "table_code": "SURF",
                    "fields": [
                        {"field_name": "mat", "data_type": "string", "length": 32},
                        {"field_name": "mat", "data_type": "string", "length": 64},
                        {"field_name": "grade", "data_type": "string"},
                    ],
                }],
            }],
            "dictionaries": [{
                "dict_name": "材质", "dict_code": "CZ",
                "items": [
                    {"item_code": "A", "item_name": "沥青"},
                    {"item_code": "A", "item_name": "沥青（重复）"},
                    {"item_code": "B", "item_name": "水泥"},
                ],
            }],
        }
        r = await client.post("/api/v1/data-standards-v2/import/json",
                              headers={"X-API-Key": raw}, json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        # 计数必须是去重后的真值：2 字段（mat 保首个）、2 条目（A 保首个）
        assert body["fields_count"] == 2, body
        assert body["dictionaries_count"] == 1
        w = " ".join(body["warnings"])
        assert "mat" in w and "重复" in w, body["warnings"]
        assert "'A'" in w and "重复" in w, body["warnings"]


class TestConflictDetailTranslation:
    """纯函数级：约束名 → 文案。未知约束不得谎报「名称已存在」。"""

    def test_known_constraints_map_to_entity_and_value(self):
        cases = [
            ("uq_ds_api_name", "标准名称", "我的标准"),
            ("uq_ds_api_code", "标准编码", "MY_CODE"),
            ("uq_sd_standard_name", "字典名称", "材料"),
            ("uq_di_dict_code", "字典项编码", "D1"),
            ("uq_sf_table_field", "字段名称", "mat"),
            ("uq_st_layer_name", "数据表名称", "路面"),
            ("uq_st_layer_code", "数据表编码", "SURF"),
            ("uq_slg_std_parent_name", "层组名称", "道路"),
            ("uq_slg_std_parent_code", "层组编码", "RD"),
        ]
        for cname, entity, value in cases:
            detail = _conflict_detail(cname, value)
            assert entity in detail, f"{cname} -> {detail!r} 未提到 {entity}"
            assert value in detail, f"{cname} -> {detail!r} 未回显 {value}"

    def test_unknown_constraint_falls_back_without_lying(self):
        detail = _conflict_detail("uq_some_future_thing", "X")
        # 未知约束：保留约束名，不谎报「标准名称已存在」
        assert "uq_some_future_thing" in detail, detail
        assert "标准名称" not in detail, detail

    def test_missing_constraint_falls_back(self):
        detail = _conflict_detail(None, "X")
        assert detail, "必须给出兜底文案"
        assert "标准名称" not in detail, detail
