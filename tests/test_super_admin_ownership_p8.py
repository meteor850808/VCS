# -*- coding: utf-8 -*-
"""超级管理员写归属列：所有 api_key_id 外键列必须落 NULL。

回归背景（2026-11-11）：超管的 ``api_key.id`` 是虚拟 ID
``00000000-0000-0000-0000-000000000001``，``api_keys`` 表没有这一行。
把它写进任何带外键的 ``api_key_id`` 列都会 500
（``ForeignKeyViolationError``）。``POST /api/v1/convert`` 已先修，
本文件锁定其余端点：一律写 ``api_key.owner_id``（超管 → NULL）。

同时用静态扫描兜住「新增代码又写回虚拟 ID」的回归。
"""

import uuid
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.models.data_standard import DataStandard
from app.models.quality_rule import QualityRule
from app.models.source_data import SourceTable, StandardVersion
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

# 超管鉴权：X-API-Key 命中 ADMIN_INIT_KEY 即走 AuthContext.create_super_admin
ADMIN = {"X-API-Key": settings.admin_init_key}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


@pytest_asyncio.fixture
async def tolerant_client():
    """不把服务端异常抛给测试：断言 HTTP 状态码，而不是接 IntegrityError。"""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _admin_standard(db_session) -> DataStandard:
    """造一个归属于超管（api_key_id IS NULL）的数据标准。"""
    std = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=None,
        code=f"ADM-{uuid.uuid4().hex[:8]}",
        name=f"超管标准 {uuid.uuid4().hex[:6]}",
        category="custom",
        status="draft",
    )
    db_session.add(std)
    await db_session.flush()
    return std


async def _admin_field(db_session, std: DataStandard) -> StandardField:
    """给超管标准挂一个可生成 LENGTH_LIMIT 规则的字段。"""
    lg = StandardLayerGroup(
        id=str(uuid.uuid4()), standard_id=std.id,
        group_name="超管层组", group_code=f"ADM{uuid.uuid4().hex[:4]}",
    )
    db_session.add(lg)
    await db_session.flush()
    t = StandardTable(
        id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
        table_name="超管表", table_code=f"ADM_T{uuid.uuid4().hex[:4]}",
        table_type="spatial",
    )
    db_session.add(t)
    await db_session.flush()
    f = StandardField(
        id=str(uuid.uuid4()), standard_id=std.id, table_id=t.id,
        field_name="道路等级", data_type="string", length=10,
        nullable=False, is_required=True,
    )
    db_session.add(f)
    await db_session.flush()
    return f


class TestQualityRuleRows:
    """质检规则表是唯一 NOT NULL 的 api_key_id 列，必须先放宽再允许超管写。"""

    async def test_created_rule_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/quality-rules",
            headers=ADMIN,
            json={"rule_type": "LENGTH_LIMIT", "rule_name": "超管规则",
                  "params": {"max_length": 10}},
        )
        assert resp.status_code == 201, resp.text
        rule = await db_session.get(QualityRule, resp.json()["id"])
        assert rule is not None
        assert rule.api_key_id is None, rule.api_key_id

    async def test_generated_rules_have_null_owner(self, tolerant_client, db_session):
        std = await _admin_standard(db_session)
        await _admin_field(db_session, std)
        await db_session.commit()

        resp = await tolerant_client.post(
            f"/api/v1/quality-rules/generate/{std.id}", headers=ADMIN,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json(), "标准含字段时应至少生成一条规则"
        for row in resp.json():
            saved = await db_session.get(QualityRule, row["id"])
            assert saved is not None
            assert saved.api_key_id is None, (row["rule_name"], saved.api_key_id)


class TestSourceDataRows:

    async def test_source_table_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/source-tables",
            headers=ADMIN,
            json={"table_name": f"超管源表 {uuid.uuid4().hex[:6]}"},
        )
        assert resp.status_code == 201, resp.text
        table = await db_session.get(SourceTable, resp.json()["id"])
        assert table is not None
        assert table.api_key_id is None, table.api_key_id

    async def test_dictionary_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/source-dictionaries",
            headers=ADMIN,
            json={"dict_name": f"超管字典 {uuid.uuid4().hex[:6]}"},
        )
        assert resp.status_code == 201, resp.text
        d = await db_session.get(StandardDictionary, resp.json()["id"])
        assert d is not None
        assert d.api_key_id is None, d.api_key_id


class TestStandardRows:

    async def test_created_standard_has_null_owner(self, tolerant_client, db_session):
        resp = await tolerant_client.post(
            "/api/v1/data-standards-v2",
            headers=ADMIN,
            json={"name": f"超管标准 {uuid.uuid4().hex[:6]}",
                  "code": f"ADM-{uuid.uuid4().hex[:8]}"},
        )
        assert resp.status_code == 201, resp.text
        std = await db_session.get(DataStandard, resp.json()["id"])
        assert std is not None
        assert std.api_key_id is None, std.api_key_id

    async def test_published_version_has_null_owner(self, tolerant_client, db_session):
        std = await _admin_standard(db_session)
        await db_session.commit()

        resp = await tolerant_client.post(
            f"/api/v1/data-standards-v2/{std.id}/publish",
            headers=ADMIN,
            json={"change_log": "超管发布"},
        )
        assert resp.status_code == 201, resp.text
        version = await db_session.get(StandardVersion, resp.json()["id"])
        assert version is not None
        assert version.api_key_id is None, version.api_key_id


class TestNoVirtualKeyIdWrites:
    """静态兜底：`app/` 下不得再出现 `api_key.id` / `auth.id` 引用。

    `AuthContext.id` 直接委托 `api_key.id`，超管下同样返回虚拟 ID
    `00000000-...-0001`，写进带外键的列会 500。唯一允许出现这两者的文件是
    `app/core/auth_context.py` —— 它负责产出 `id` 与 `owner_id`。
    """

    _FORBIDDEN = ("api_key.id", "auth.id")
    _EXEMPT_FILES = {"app/core/auth_context.py"}

    def test_app_has_no_virtual_key_id_references(self):
        root = Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for path in sorted((root / "app").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in self._EXEMPT_FILES:
                continue
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if any(token in code for token in self._FORBIDDEN):
                    offenders.append(f"{rel}:{no}: {line.strip()}")
        assert offenders == [], (
            "写归属用 auth.owner_id（超管落 NULL），读过滤用 auth.scope_clause：\n"
            + "\n".join(offenders)
        )

    def test_exemption_surface_is_single_file(self):
        assert TestNoVirtualKeyIdWrites._EXEMPT_FILES == {"app/core/auth_context.py"}
