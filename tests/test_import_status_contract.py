# -*- coding: utf-8 -*-
"""导入状态码跨格式契约：update/upsert 三格式统一 201。

背景：v2.22.2/§六 曾把「update/upsert 三格式统一返回 201」记为技术债。
取证（2026-10-08）发现该债从记录时点起就已满足 —— 三个端点装饰器均为
`status_code=201`，行为矩阵实测一致。债的实际残留是**契约未被集中钉死**：
既有断言散落在各场景测试里，任何一格被改回 200/204 都不会被专门发现。

本文件把矩阵钉在一处（含 mode 校验与未命中语义），防契约漂移。

矩阵（命中 = 同名标准存在，走更新分支）：
  | 格式   | update(命中) | upsert(命中) | upsert(未命中) | update(未命中) | mode 非法 |
  |--------|--------------|--------------|----------------|----------------|-----------|
  | json   | 201/updated  | 201/updated  | 201/created    | 404            | 400       |
  | excel  | 201/updated  | 201/updated  | （同 json 语义，走 _resolve_import_target）|      | 400       |
  | csv    | 201/updated  | 201/updated  | （同上）       |                | 400       |

设计取舍留档：update 回 201 而非 200 是**沿用的既有跨格式契约**（导入端点
整体按"创建或替换"语义建模，`ImportResult.updated` 区分两种结果），改 200
是破坏性契约变更、无用户可见收益 —— 见 revision_v2_22_3 §六·补五。
"""
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

import tests.test_imp_exp_dict_roundtrip as T  # noqa: E402

BASE = "/api/v1/data-standards-v2"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_CT = "application/zip"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


async def _seeded(client, db_session, name):
    """建一把 key + 一个同名标准 + 一份 Excel/CSV 导出件（供回导命中）。"""
    raw, key = await T._key(db_session, f"sc_{abs(hash(name)) % 10000}")
    std_id = await T._seed_rich_standard(db_session, key, name)
    await db_session.commit()
    exports = {}
    for fmt in ("excel", "csv"):
        r = await client.post(f"{BASE}/{std_id}/export?format={fmt}",
                              headers={"X-API-Key": raw})
        assert r.status_code == 200, (fmt, r.status_code)
        exports[fmt] = r.content
    return raw, exports


class TestJsonStatusMatrix:
    async def test_update_and_upsert_hit_return_201_updated(self, client, db_session):
        raw, _ = await _seeded(client, db_session, "状态契约JSON")
        h = {"X-API-Key": raw}
        for mode in ("update", "upsert"):
            r = await client.post(f"{BASE}/import/json?mode={mode}", headers=h, json={
                "name": "状态契约JSON", "layer_groups": [],
            })
            assert r.status_code == 201, f"json/{mode} 命中应 201，实际 {r.status_code}"
            assert r.json()["updated"] is True, mode

    async def test_upsert_miss_creates_201_update_miss_404(self, client, db_session):
        raw, _ = await _seeded(client, db_session, "状态契约JSON未命中")
        h = {"X-API-Key": raw}
        r = await client.post(f"{BASE}/import/json?mode=upsert", headers=h, json={
            "name": "upsert新标准", "code": "SC-NEW-1", "layer_groups": [],
        })
        assert r.status_code == 201 and r.json()["updated"] is False
        r2 = await client.post(f"{BASE}/import/json?mode=update", headers=h, json={
            "name": "根本没有这标准", "layer_groups": [],
        })
        assert r2.status_code == 404

    async def test_invalid_mode_400(self, client, db_session):
        raw, _ = await _seeded(client, db_session, "状态契约JSON坏mode")
        r = await client.post(f"{BASE}/import/json?mode=bogus",
                              headers={"X-API-Key": raw}, json={"name": "x"})
        assert r.status_code == 400


class TestExcelStatusMatrix:
    async def test_update_and_upsert_hit_return_201_updated(self, client, db_session):
        raw, exports = await _seeded(client, db_session, "状态契约Excel")
        for mode in ("update", "upsert"):
            r = await client.post(f"{BASE}/import/excel",
                                  headers={"X-API-Key": raw},
                                  data={"name": "状态契约Excel", "mode": mode},
                                  files={"file": ("a.xlsx", exports["excel"], XLSX_CT)})
            assert r.status_code == 201, f"excel/{mode} 应 201，实际 {r.status_code}"
            assert r.json()["updated"] is True, mode

    async def test_invalid_mode_400(self, client, db_session):
        raw, exports = await _seeded(client, db_session, "状态契约Excel坏mode")
        r = await client.post(f"{BASE}/import/excel",
                              headers={"X-API-Key": raw},
                              data={"name": "状态契约Excel坏mode", "mode": "bogus"},
                              files={"file": ("a.xlsx", exports["excel"], XLSX_CT)})
        assert r.status_code == 400


class TestCsvStatusMatrix:
    async def test_update_and_upsert_hit_return_201_updated(self, client, db_session):
        raw, exports = await _seeded(client, db_session, "状态契约CSV")
        for mode in ("update", "upsert"):
            r = await client.post(f"{BASE}/import/csv",
                                  headers={"X-API-Key": raw},
                                  data={"name": "状态契约CSV", "mode": mode},
                                  files={"files": ("a.zip", exports["csv"], ZIP_CT)})
            assert r.status_code == 201, f"csv/{mode} 应 201，实际 {r.status_code}"
            assert r.json()["updated"] is True, mode

    async def test_invalid_mode_400(self, client, db_session):
        raw, exports = await _seeded(client, db_session, "状态契约CSV坏mode")
        r = await client.post(f"{BASE}/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "状态契约CSV坏mode", "mode": "bogus"},
                              files={"files": ("a.zip", exports["csv"], ZIP_CT)})
        assert r.status_code == 400
