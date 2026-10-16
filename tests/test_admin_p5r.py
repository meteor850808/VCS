"""P5r: admin.py 路由级收官 100%（39% → 100%）。

真实 vcs_test PostgreSQL integration，覆盖 9 组端点全分支：
  _verify_admin 403 / create key(含 group 404) / list / revoke / activate /
  regenerate / hard delete / set key group(分配/移出/两级 404) /
  group CRUD(重名 409/成员统计/list 子查询/成员列表/del 置空) 等。
"""

import pytest
import pytest_asyncio

from app.main import app
from tests.conftest import _TestSession


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _autouse_test_db(test_db):
    yield


# ══════════════════════════════════════════════════════════════════════════
# 工具：直接建数据
# ══════════════════════════════════════════════════════════════════════════

async def _make_key(db_session, name="k-1", group_id=None, active=True):
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    raw, h = generate_api_key()
    k = ApiKey(key_hash=h, name=name, group_id=group_id, is_active=active,
               description=f"desc-{name}")
    db_session.add(k)
    await db_session.commit()
    return k


async def _make_group(db_session, name="g-1", desc="gd"):
    from app.models.key_group import KeyGroup
    g = KeyGroup(name=name, description=desc)
    db_session.add(g)
    await db_session.commit()
    return g


# ══════════════════════════════════════════════════════════════════════════
# API Key 端点
# ══════════════════════════════════════════════════════════════════════════

class TestCreateApiKey:
    async def test_unauthorized_403(self, client):
        resp = await client.post("/api/v1/admin/apikeys", json={"name": "x"})
        assert resp.status_code == 403

    async def test_ok(self, client, admin_init_header, db_session):
        resp = await client.post(
            "/api/v1/admin/apikeys",
            headers=admin_init_header,
            json={"name": "ak-ok", "description": "d"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "ak-ok" and data["api_key"].startswith("vcs_")
        assert "仅显示一次" in data["message"]

    async def test_with_group(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="wg")
        resp = await client.post(
            "/api/v1/admin/apikeys",
            headers=admin_init_header,
            json={"name": "ak-g", "group_id": g.id},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "ak-g"

    async def test_group_404(self, client, admin_init_header, db_session):
        resp = await client.post(
            "/api/v1/admin/apikeys",
            headers=admin_init_header,
            json={"name": "ak-bad-g", "group_id": "no-such-group"},
        )
        assert resp.status_code == 404
        assert "分组不存在" in resp.json()["detail"]


class TestListApiKeys:
    async def test_with_group_name(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="lg")
        k = await _make_key(db_session, name="lk", group_id=g.id)
        resp = await client.get("/api/v1/admin/apikeys", headers=admin_init_header)
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            if item["id"] == k.id:
                assert item["group_name"] == "lg"
                assert item["group_id"] == g.id
                break
        else:
            pytest.fail("key not in list")


class TestRevokeApiKey:
    async def test_404(self, client, admin_init_header, db_session):
        resp = await client.delete("/api/v1/admin/apikeys/nope",
                                   headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok_soft_delete(self, client, admin_init_header, db_session):
        from app.models.api_key import ApiKey
        from sqlalchemy import select
        k = await _make_key(db_session, name="rev")
        resp = await client.delete(f"/api/v1/admin/apikeys/{k.id}",
                                   headers=admin_init_header)
        assert resp.status_code == 204
        async with _TestSession() as sess:
            got = (await sess.execute(
                select(ApiKey).where(ApiKey.id == k.id))).scalar_one()
            assert got.is_active is False


class TestActivateApiKey:
    async def test_404(self, client, admin_init_header):
        resp = await client.put("/api/v1/admin/apikeys/nope/activate",
                                headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok(self, client, admin_init_header, db_session):
        k = await _make_key(db_session, name="act", active=False)
        resp = await client.put(f"/api/v1/admin/apikeys/{k.id}/activate",
                                headers=admin_init_header)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True


class TestRegenerateApiKey:
    async def test_404(self, client, admin_init_header):
        resp = await client.post("/api/v1/admin/apikeys/nope/regenerate",
                                 headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok(self, client, admin_init_header, db_session):
        k = await _make_key(db_session, name="reg")
        old_hash = k.key_hash
        resp = await client.post(f"/api/v1/admin/apikeys/{k.id}/regenerate",
                                 headers=admin_init_header)
        assert resp.status_code == 200
        assert resp.json()["name"] == "reg"
        assert resp.json()["api_key"].startswith("vcs_")
        assert "旧密钥立即失效" in resp.json()["message"]
        # 哈希已变（expire 后重查，避免会话内过期对象）
        from app.models.api_key import ApiKey
        from sqlalchemy import select
        async with _TestSession() as sess:
            got = (await sess.execute(
                select(ApiKey).where(ApiKey.id == k.id))).scalar_one()
            assert got.key_hash != old_hash


class TestHardDeleteApiKey:
    async def test_404(self, client, admin_init_header):
        resp = await client.delete("/api/v1/admin/apikeys/nope/hard",
                                   headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok(self, client, admin_init_header, db_session):
        from app.models.api_key import ApiKey
        from sqlalchemy import select
        k = await _make_key(db_session, name="hd")
        resp = await client.delete(f"/api/v1/admin/apikeys/{k.id}/hard",
                                   headers=admin_init_header)
        assert resp.status_code == 204
        got = (await db_session.execute(
            select(ApiKey).where(ApiKey.id == k.id))).scalar_one_or_none()
        assert got is None


class TestSetKeyGroup:
    async def test_key_404(self, client, admin_init_header):
        resp = await client.put("/api/v1/admin/apikeys/nope/group",
                                headers=admin_init_header, json={"group_id": None})
        assert resp.status_code == 404

    async def test_group_404(self, client, admin_init_header, db_session):
        k = await _make_key(db_session, name="skg")
        resp = await client.put(f"/api/v1/admin/apikeys/{k.id}/group",
                                headers=admin_init_header,
                                json={"group_id": "no-such"})
        assert resp.status_code == 404

    async def test_assign(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="skg-g")
        k = await _make_key(db_session, name="skg-k")
        resp = await client.put(f"/api/v1/admin/apikeys/{k.id}/group",
                                headers=admin_init_header,
                                json={"group_id": g.id})
        assert resp.status_code == 200
        assert resp.json()["group_id"] == g.id

    async def test_unassign(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="skg-u")
        k = await _make_key(db_session, name="skg-uk", group_id=g.id)
        resp = await client.put(f"/api/v1/admin/apikeys/{k.id}/group",
                                headers=admin_init_header,
                                json={"group_id": None})
        assert resp.status_code == 200
        assert resp.json()["group_id"] is None


# ══════════════════════════════════════════════════════════════════════════
# Group 端点
# ══════════════════════════════════════════════════════════════════════════

class TestCreateGroup:
    async def test_ok(self, client, admin_init_header, db_session):
        resp = await client.post("/api/v1/admin/groups",
                                 headers=admin_init_header,
                                 json={"name": "cg", "description": "desc"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "cg" and data["api_keys"] == []

    async def test_409(self, client, admin_init_header, db_session):
        await _make_group(db_session, name="dup")
        resp = await client.post("/api/v1/admin/groups",
                                 headers=admin_init_header,
                                 json={"name": "dup"})
        assert resp.status_code == 409
        assert "已存在" in resp.json()["detail"]


class TestListGroups:
    async def test_member_count(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="lg-cnt")
        await _make_key(db_session, name="cnt-1", group_id=g.id)
        await _make_key(db_session, name="cnt-2", group_id=g.id)
        await _make_key(db_session, name="cnt-inactive", group_id=g.id, active=False)
        resp = await client.get("/api/v1/admin/groups", headers=admin_init_header)
        assert resp.status_code == 200
        for row in resp.json():
            if row["name"] == "lg-cnt":
                # 只统计 is_active True → 2
                assert row["member_count"] == 2
                break
        else:
            pytest.fail("group missing in list")


class TestGetGroup:
    async def test_404(self, client, admin_init_header):
        resp = await client.get("/api/v1/admin/groups/nope",
                                headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok_with_members(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="gg")
        await _make_key(db_session, name="gg-k", group_id=g.id)
        resp = await client.get(f"/api/v1/admin/groups/{g.id}",
                                headers=admin_init_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gg"
        assert len(data["api_keys"]) == 1
        assert data["api_keys"][0]["group_name"] == "gg"


class TestUpdateGroup:
    async def test_404(self, client, admin_init_header):
        resp = await client.put("/api/v1/admin/groups/nope",
                                headers=admin_init_header,
                                json={"name": "x"})
        assert resp.status_code == 404

    async def test_409(self, client, admin_init_header, db_session):
        await _make_group(db_session, name="ug-a")
        b = await _make_group(db_session, name="ug-b")
        resp = await client.put(f"/api/v1/admin/groups/{b.id}",
                                headers=admin_init_header,
                                json={"name": "ug-a"})
        assert resp.status_code == 409

    async def test_update_name_and_desc(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="ug-old")
        resp = await client.put(f"/api/v1/admin/groups/{g.id}",
                                headers=admin_init_header,
                                json={"name": "ug-new", "description": "nd"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "ug-new"
        assert resp.json()["description"] == "nd"

    async def test_update_desc_only(self, client, admin_init_header, db_session):
        g = await _make_group(db_session, name="ug-desc")
        resp = await client.put(f"/api/v1/admin/groups/{g.id}",
                                headers=admin_init_header,
                                json={"description": "only"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "ug-desc"  # name 不变
        assert resp.json()["description"] == "only"


class TestDeleteGroup:
    async def test_404(self, client, admin_init_header):
        resp = await client.delete("/api/v1/admin/groups/nope",
                                   headers=admin_init_header)
        assert resp.status_code == 404

    async def test_ok_nullifies_key_group(self, client, admin_init_header, db_session):
        from app.models.api_key import ApiKey
        from sqlalchemy import select
        g = await _make_group(db_session, name="dg")
        k = await _make_key(db_session, name="dg-k", group_id=g.id)
        resp = await client.delete(f"/api/v1/admin/groups/{g.id}",
                                   headers=admin_init_header)
        assert resp.status_code == 204
        # 组已删（独立会话重查，避免会话内过期对象）
        async with _TestSession() as sess:
            got = (await sess.execute(
                select(ApiKey).where(ApiKey.id == k.id))).scalar_one()
            assert got.group_id is None  # 外键 SET NULL 等效