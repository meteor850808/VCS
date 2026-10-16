# -*- coding: utf-8 -*-
"""知识图谱端点 × 真实参照物（一厅八室数据标准）。

用真实数据当 oracle —— 期望值全部离线预计算自源 JSON，不从实现反推：

  SHGT_LAND:   层组=6 表=67 字段=1348 字典=73 有效绑定对=214
               （312 个绑定字段聚合到 214 条边；89 个绑定指向未声明 code
                 —— 导入时被跳过并告警，图里不得出现）
  SHGT_SURVEY: 层组=5 表=255 字段=3288 字典=187 条目=2031 绑定字段=19
               边=17 —— 体积 oracle：摘要载荷必须远小于明细总和

这两份分别考察「聚合正确性」与「大载荷摘要不泄漏明细」。
"""
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.security import generate_api_key
from app.models.api_key import ApiKey

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_DIR = Path(r"C:\Users\Administrator\Desktop\AI项目\数据标准调整"
               r"\一厅八室数据标准-转换产出\json")
BASE = "/api/v1/data-standards-v2"

# 模块级缓存：同一份参照物只导入一次（key 行落库后跨 session 有效）
_WORLD: dict[str, tuple[str, str]] = {}


@pytest.fixture(scope="module")
def ref_dir() -> Path:
    if not REF_DIR.exists():
        pytest.skip(f"参照物目录不存在: {REF_DIR}")
    return REF_DIR


@pytest.mark.parametrize("name", ["SHGT_LAND", "SHGT_SURVEY"])
async def test_import_survives_duplicate_item_codes(client, db_session, name):
    """真实数据脏度 oracle：LAND 8 条 / SURVEY 3 条字典内重复码项。

    一厅八室源数据同字典内存在重复 item_code（如 LAND CT47 的 '214'×4），
    而库上有 uq_di_dict_code 唯一约束 —— 导入必须「保留首个 + 告警」，
    不能整单 409 炸掉（告警而非失败是 v2.22.3 的既有原则）。
    """
    raw, hashed = generate_api_key()
    db_session.add(ApiKey(key_hash=hashed, name=f"dup_{name}", is_active=True))
    await db_session.commit()

    body = json.loads((REF_DIR / f"{name}.json").read_text(encoding="utf-8"))
    r = await client.post(f"{BASE}/import/json?mode=create",
                          headers={"X-API-Key": raw}, json=body)
    assert r.status_code == 201, f"{name} 导入失败: {r.text[:400]}"
    warn_text = " ".join(r.json().get("warnings") or [])
    assert "重复" in warn_text or "duplicate" in warn_text.lower(), \
        f"重复码项必须告警，实际 warnings: {r.json().get('warnings')[:5]}"


@pytest_asyncio.fixture
async def ref_world(db_session, client):
    """(key, {文件名: standard_id}) —— 首个用例建 key 并导入，后续复用。"""
    if "key" not in _WORLD:
        raw, hashed = generate_api_key()
        db_session.add(ApiKey(key_hash=hashed, name="graph_ref_oracle", is_active=True))
        await db_session.commit()
        _WORLD["key"] = raw

    async def _import(name: str) -> str:
        if name not in _WORLD:
            body = json.loads((REF_DIR / f"{name}.json").read_text(encoding="utf-8"))
            r = await client.post(f"{BASE}/import/json?mode=upsert",
                                  headers={"X-API-Key": _WORLD["key"]}, json=body)
            assert r.status_code == 201, f"{name} 导入失败: {r.text[:400]}"
            _WORLD[name] = r.json()["standard_id"]
        return _WORLD[name]

    yield _WORLD["key"], _import


class TestGraphAgainstLandReference:
    """LAND：聚合正确性 —— 312 绑定字段 → 214 条边，89 个未声明 code 丢弃。"""

    async def test_summary_counts_match_source(self, client, ref_world):
        key, imp = ref_world
        sid = await imp("SHGT_LAND")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        assert r.status_code == 200, r.text[:300]
        st = r.json()["stats"]
        assert st["group_count"] == 6
        assert st["table_count"] == 67
        # 源 1348 字段，其中 24 个同表重名被去重（保首个+告警）→ 实际 1324
        assert st["field_count"] == 1324
        assert st["dict_count"] == 73
        # 源 312 个带 dict_codes 的字段 → 去重丢 24 个重名后续 → 218 绑定字段
        #   → 聚合为 214 条 (table,dict) 边（89 个绑定指向未声明 code，导入即跳过）
        assert st["linked_field_count"] == 218
        assert st["binds_edge_count"] == 214

    async def test_dangling_codes_produce_no_edges_or_nodes(self, client, ref_world):
        """绑定指向未声明的 dict_code → 导入即跳过（告警），图里无悬挂。"""
        key, imp = ref_world
        sid = await imp("SHGT_LAND")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        body = r.json()
        node_ids = {n["id"] for n in body["nodes"]}
        dangling = [e for e in body["edges"] if e["target"] not in node_ids]
        assert not dangling, f"悬挂边 {len(dangling)} 条"

    async def test_biggest_table_detail(self, client, ref_world):
        """最大表「经营性用地合同」89 字段，明细端点要扛得住。"""
        key, imp = ref_world
        sid = await imp("SHGT_LAND")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        t = next(n for n in r.json()["nodes"]
                 if n["kind"] == "table" and n["name"] == "经营性用地合同")
        assert t["field_count"] == 89

        r2 = await client.get(f"{BASE}/{sid}/graph/nodes/{t['id']}",
                              headers={"X-API-Key": key})
        assert r2.status_code == 200
        fields = r2.json()["fields"]
        assert len(fields) == 89
        orderings = [f["ordering"] for f in fields if f["ordering"] is not None]
        assert orderings == sorted(orderings), "字段必须按 ordering 升序"


class TestGraphAgainstSurveyReference:
    """SURVEY：体积 oracle —— 3288 字段/2031 条目的标准，摘要必须轻。"""

    async def test_summary_counts_match_source(self, client, ref_world):
        key, imp = ref_world
        sid = await imp("SHGT_SURVEY")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        st = r.json()["stats"]
        assert st["group_count"] == 5
        assert st["table_count"] == 255
        # 源 3288 字段，111 个同表重名被去重 → 实际 3177
        assert st["field_count"] == 3177
        assert st["dict_count"] == 187
        assert st["linked_field_count"] == 17
        assert st["binds_edge_count"] == 17

    async def test_summary_payload_stays_small(self, client, ref_world):
        """3288 字段的明细约 1.5MB —— 摘要必须远小于它。

        钉上限 400KB：节点 447 + 边 ~280 + 徽章，实际应在 ~100KB 量级；
        超限即意味着有人把明细塞回了摘要。
        """
        key, imp = ref_world
        sid = await imp("SHGT_SURVEY")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        size = len(json.dumps(r.json(), ensure_ascii=False))
        assert size < 400_000, f"摘要载荷 {size/1024:.0f}KB —— 疑似混入明细"

    async def test_house_table_detail(self, client, ref_world):
        """SURVEY 最大表「户」：源 84 字段，34 个重名去重后落库 50。"""
        key, imp = ref_world
        sid = await imp("SHGT_SURVEY")
        r = await client.get(f"{BASE}/{sid}/graph", headers={"X-API-Key": key})
        t = next(n for n in r.json()["nodes"]
                 if n["kind"] == "table" and n["name"] == "户")
        assert t["field_count"] == 50
        r2 = await client.get(f"{BASE}/{sid}/graph/nodes/{t['id']}",
                              headers={"X-API-Key": key})
        assert r2.status_code == 200
        assert len(r2.json()["fields"]) == 50
