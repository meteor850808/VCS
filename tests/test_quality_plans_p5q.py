"""P5q: quality_plans.py 路由级 100%（44% → 100%）。

使用真实 vcs_test PostgreSQL（conftest test_db）+ httpx AsyncClient。
覆盖 6 端点全分支：
  list_plans        GET    /api/v1/quality-plans
  get_plan          GET    /api/v1/quality-plans/{id}          404
  create_plan       POST   /api/v1/quality-plans               201 / 409 重名
  update_plan       PUT    /api/v1/quality-plans/{id}          404 / 400 无字段 / 409 重名 / 正常
  delete_plan       DELETE /api/v1/quality-plans/{id}          404 / 正常
  init_default_plan POST   /api/v1/quality-plans/default       已存在 / 正常幂等创建
"""

import pytest
import pytest_asyncio

from app.main import app


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _autouse_test_db(test_db):
    yield


def _plan_payload(**over):
    data = {
        "name": "测试方案",
        "description": "desc",
        "attr_enabled": True,
        "topo_config": {"enabled_rules": ["no_dangles"]},
        "standard_id": "std-1",
        "extra": {"k": "v"},
    }
    data.update(over)
    return data


class TestListPlans:
    async def test_empty(self, client, api_key_header):
        resp = await client.get("/api/v1/quality-plans", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"items": [], "total": 0}

    async def test_with_items(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        p = QualityPlan(name="Algo", description="a")
        db_session.add(p)
        await db_session.commit()
        resp = await client.get("/api/v1/quality-plans", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Algo"


class TestGetPlan:
    async def test_ok(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        p = QualityPlan(name="Getme")
        db_session.add(p)
        await db_session.commit()
        resp = await client.get(f"/api/v1/quality-plans/{p.id}", headers=api_key_header)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Getme"

    async def test_404(self, client, api_key_header):
        resp = await client.get("/api/v1/quality-plans/nope", headers=api_key_header)
        assert resp.status_code == 404


class TestCreatePlan:
    async def test_ok_defaults(self, client, api_key_header, db_session):
        resp = await client.post(
            "/api/v1/quality-plans", json=_plan_payload(), headers=api_key_header
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试方案"
        assert data["topo_config"] == {"enabled_rules": ["no_dangles"]}
        assert data["standard_id"] == "std-1"
        assert data["extra"] == {"k": "v"}

    async def test_409_duplicate(self, client, api_key_header, db_session):
        resp = await client.post(
            "/api/v1/quality-plans", json=_plan_payload(name="重置"), headers=api_key_header
        )
        assert resp.status_code == 201
        resp2 = await client.post(
            "/api/v1/quality-plans", json=_plan_payload(name="重置"), headers=api_key_header
        )
        assert resp2.status_code == 409
        assert "存在" in resp2.json()["detail"]

    async def test_minimal_create(self, client, api_key_header):
        resp = await client.post(
            "/api/v1/quality-plans", json={"name": "极简"}, headers=api_key_header
        )
        assert resp.status_code == 201
        assert resp.json()["attr_enabled"] is True
        assert resp.json()["description"] is None


class TestUpdatePlan:
    async def test_404(self, client, api_key_header):
        resp = await client.put(
            "/api/v1/quality-plans/nope", json={"name": "x"}, headers=api_key_header
        )
        assert resp.status_code == 404

    async def test_400_empty(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        p = QualityPlan(name="Empty")
        db_session.add(p)
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/quality-plans/{p.id}", json={}, headers=api_key_header
        )
        assert resp.status_code == 400
        assert "无更新" in resp.json()["detail"]

    async def test_409_dup_name(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        a = QualityPlan(name="UP-A")
        b = QualityPlan(name="UP-B")
        db_session.add_all([a, b])
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/quality-plans/{b.id}",
            json={"name": "UP-A"},
            headers=api_key_header,
        )
        assert resp.status_code == 409

    async def test_ok_partial(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        p = QualityPlan(name="OldName", description="old")
        db_session.add(p)
        await db_session.commit()
        resp = await client.put(
            f"/api/v1/quality-plans/{p.id}",
            json={"description": "new", "attr_enabled": False},
            headers=api_key_header,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "new"
        assert resp.json()["attr_enabled"] is False
        assert resp.json()["name"] == "OldName"  # 未传字段不改变


class TestDeletePlan:
    async def test_404(self, client, api_key_header):
        resp = await client.delete("/api/v1/quality-plans/nope", headers=api_key_header)
        assert resp.status_code == 404

    async def test_ok(self, client, api_key_header, db_session):
        from app.models.quality_plan import QualityPlan
        p = QualityPlan(name="DelMe")
        db_session.add(p)
        await db_session.commit()
        pid = p.id
        resp = await client.delete(f"/api/v1/quality-plans/{pid}", headers=api_key_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["deleted"] == "DelMe"
        # 确认已删
        from sqlalchemy import select
        got = await db_session.execute(
            select(QualityPlan).where(QualityPlan.id == pid)
        )
        assert got.scalar_one_or_none() is None


class TestInitDefaultPlan:
    async def test_creates_once(self, client, api_key_header, db_session):
        from sqlalchemy import select
        from app.models.quality_plan import QualityPlan

        resp = await client.post("/api/v1/quality-plans/default", headers=api_key_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True and "plan" in data
        assert data["plan"]["name"] == "默认"
        assert data["plan"]["topo_config"]["enabled_rules"]

        # 幂等：第二次 → 已存在
        resp2 = await client.post("/api/v1/quality-plans/default", headers=api_key_header)
        assert resp2.status_code == 200
        assert resp2.json()["msg"] == "默认方案已存在"

        got = await db_session.execute(
            select(QualityPlan).where(QualityPlan.name == "默认")
        )
        assert len(got.scalars().all()) == 1  # 未被重复创建