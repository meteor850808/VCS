# -*- coding: utf-8 -*-
"""P5g: data_standards.py 路由级测试 — 模板下载 / Excel 导入 / 列表分页 / 详情 / 删除。

背景（TDD 红用例先行）：
- 探针实测修复前 `POST /data-standards/import` 一律 500：
  `TypeError: 'sheet_count' is an invalid keyword argument for DataStandard`
  —— 模型 DataStandard 无 sheet_count 列，路由却以此构造 ORM 对象
  （第 5 处「无测试覆盖→逻辑静默失效」实证：导入功能在生产完全不可用，
  列表 sheet_count 亦恒 0）。
  已修复：模型补 `sheet_count: Integer NOT NULL DEFAULT 0` 列
  （生产库需 ALTER TABLE；vcs_test 由 create_all 重建已生效）。
- 探针实测全链路：真实 xlsx（openpyxl 构造 2 字段 1 sheet）解析 →
  DataStandard/DataStandardSheet/DataStandardField + SourceTable/SourceField
  + StandardVersion 全落库；非 xlsx/非法文件名/超 10MB/解析异常/无有效字段
  均按设计 400/413。
"""

import io
import tempfile
import uuid as _uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.config import settings
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard, DataStandardField, DataStandardSheet
from app.models.source_data import SourceField, SourceTable, StandardVersion
from app.services.data_standard_parser import StandardParseResult

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_bytes(sheet_name="地块", rows=None):
    """构造可被 openpyxl 真实解析的标准 Excel 字节。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["字段名", "类型", "长度", "可空", "允许值", "描述"])
    for r in rows or [
        ("地块编码", "string", 32, "否", "", "地块编码"),
        ("面积", "float", None, "是", "", "面积"),
    ]:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _create_key(db_session, name):
    raw, hashed = generate_api_key()
    key = ApiKey(id=str(_uuid.uuid4()), key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.commit()
    return raw, key


async def _import_ok(client, raw, filename="地块标准.xlsx", name="地块标准",
                     description="导入描述", content=None):
    """成功导入一条标准，返回响应 json。"""
    resp = await client.post(
        "/api/v1/data-standards/import",
        headers={"X-API-Key": raw},
        data={"name": name, "description": description},
        files={"file": (filename, content or _xlsx_bytes(), XLSX_MIME)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_standard(db_session, key, name, sheet_count=0):
    std = DataStandard(id=str(_uuid.uuid4()), api_key_id=key.id, name=name,
                       sheet_count=sheet_count)
    db_session.add(std)
    await db_session.commit()
    return std


# ═══════════════════════════════════════════════════════════════
# 模板下载
# ═══════════════════════════════════════════════════════════════

class TestDownloadTemplate:
    async def test_template_ok(self, client, db_session):
        raw, _ = await _create_key(db_session, "tpl_ok")
        resp = await client.get("/api/v1/data-standards/template",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert len(resp.content) > 0

    async def test_template_missing(self, client, db_session):
        raw, _ = await _create_key(db_session, "tpl_404")
        with patch("app.api.v1.data_standards._TEMPLATE_PATH",
                   Path("static/templates/__no_such__.xlsx")):
            resp = await client.get("/api/v1/data-standards/template",
                                    headers={"X-API-Key": raw})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# Excel 导入
# ═══════════════════════════════════════════════════════════════

class TestImportStandard:
    async def test_import_ok(self, client, db_session):
        """真实 xlsx 全链路: 6 张表全落库 + 响应结构。"""
        raw, key = await _create_key(db_session, "imp_ok")
        content = _xlsx_bytes()
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            out = await _import_ok(client, raw, name="地块标准", content=content)

        assert out["name"] == "地块标准" and out["description"] == "导入描述"
        assert len(out["sheets"]) == 1
        sheet = out["sheets"][0]
        assert sheet["sheet_name"] == "地块" and sheet["ordering"] == 0
        assert [f["field_name"] for f in sheet["fields"]] == ["地块编码", "面积"]
        assert sheet["fields"][0]["length"] == 32
        assert sheet["fields"][0]["nullable"] is False
        std_id = out["id"]

        n_std = (await db_session.execute(
            select(func.count()).select_from(DataStandard).where(
                DataStandard.id == std_id))).scalar()
        n_sh = (await db_session.execute(
            select(func.count()).select_from(DataStandardSheet).where(
                DataStandardSheet.standard_id == std_id))).scalar()
        n_fi = (await db_session.execute(
            select(func.count()).select_from(DataStandardField)
            .where(DataStandardField.id.in_(
                select(DataStandardField.id).join(DataStandardSheet)
                .where(DataStandardSheet.standard_id == std_id))))).scalar()
        n_st = (await db_session.execute(
            select(func.count()).select_from(SourceTable).where(
                SourceTable.standard_id == std_id))).scalar()
        n_sf = (await db_session.execute(
            select(func.count()).select_from(SourceField)
            .where(SourceField.table_id.in_(
                select(SourceTable.id).where(SourceTable.standard_id == std_id))))).scalar()
        n_sv = (await db_session.execute(
            select(func.count()).select_from(StandardVersion).where(
                StandardVersion.standard_id == std_id))).scalar()
        assert (n_std, n_sh, n_fi, n_st, n_sf, n_sv) == (1, 1, 2, 1, 2, 1)

        std = (await db_session.execute(
            select(DataStandard).where(DataStandard.id == std_id))).scalar_one()
        assert std.sheet_count == 1 and std.file_name == "地块标准.xlsx"
        ver = (await db_session.execute(
            select(StandardVersion).where(StandardVersion.standard_id == std_id))
        ).scalar_one()
        assert ver.version_label == "v1" and ver.change_log == "初始导入"
        assert ver.file_data == content  # 原文件字节落库

    async def test_import_default_name_from_stem(self, client, db_session):
        """name 缺省 → 取文件名 stem；description 缺省 → None。"""
        raw, _ = await _create_key(db_session, "imp_stem")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            resp = await client.post(
                "/api/v1/data-standards/import",
                headers={"X-API-Key": raw},
                files={"file": ("道路要素.xlsx", _xlsx_bytes("道路"), XLSX_MIME)},
            )
        assert resp.status_code == 201
        out = resp.json()
        assert out["name"] == "道路要素" and out["description"] is None

    async def test_bad_extension(self, client, db_session):
        raw, _ = await _create_key(db_session, "imp_ext")
        resp = await client.post(
            "/api/v1/data-standards/import",
            headers={"X-API-Key": raw},
            files={"file": ("x.csv", b"a,b", "text/csv")},
        )
        assert resp.status_code == 400

    async def test_evil_filename(self, client, db_session):
        raw, _ = await _create_key(db_session, "imp_evil")
        content = _xlsx_bytes()
        for evil in ("../up.xlsx", "a/b.xlsx", "a\\b.xlsx"):
            resp = await client.post(
                "/api/v1/data-standards/import",
                headers={"X-API-Key": raw},
                files={"file": (evil, content, XLSX_MIME)},
            )
            assert resp.status_code == 400, evil

    async def test_oversize(self, client, db_session):
        raw, _ = await _create_key(db_session, "imp_big")
        resp = await client.post(
            "/api/v1/data-standards/import",
            headers={"X-API-Key": raw},
            files={"file": ("big.xlsx", b"x" * (10 * 1024 * 1024 + 1), XLSX_MIME)},
        )
        assert resp.status_code == 413

    async def test_parse_error(self, client, db_session):
        raw, _ = await _create_key(db_session, "imp_parse")
        with patch("app.api.v1.data_standards.parse_standard_excel",
                   side_effect=ValueError("bad workbook")):
            resp = await client.post(
                "/api/v1/data-standards/import",
                headers={"X-API-Key": raw},
                files={"file": ("bad.xlsx", _xlsx_bytes(), XLSX_MIME)},
            )
        assert resp.status_code == 400
        assert "Excel 解析失败" in resp.json()["detail"]

    async def test_empty_sheets(self, client, db_session):
        raw, _ = await _create_key(db_session, "imp_empty")
        with patch("app.api.v1.data_standards.parse_standard_excel",
                   return_value=StandardParseResult(sheets=[])):
            resp = await client.post(
                "/api/v1/data-standards/import",
                headers={"X-API-Key": raw},
                files={"file": ("empty.xlsx", _xlsx_bytes(), XLSX_MIME)},
            )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
# 列表 / 详情 / 删除
# ═══════════════════════════════════════════════════════════════

class TestListStandards:
    async def test_list_pagination(self, client, db_session):
        raw, key = await _create_key(db_session, "list_pg")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            for i in range(3):
                await _import_ok(client, raw, name=f"标准{i}", filename=f"s{i}.xlsx")

        resp = await client.get("/api/v1/data-standards?page=1&page_size=2",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200 and len(resp.json()) == 2
        resp = await client.get("/api/v1/data-standards?page=2&page_size=2",
                                headers={"X-API-Key": raw})
        assert len(resp.json()) == 1
        # brief 含文件信息
        first = resp.json()[0]
        assert {"name", "description", "file_name", "sheet_count", "created_at"} <= set(first)

    async def test_list_scope_isolation(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "list_iso_a")
        raw_b, key_b = await _create_key(db_session, "list_iso_b")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            await _import_ok(client, raw_a, name="A 的标准")
        await _make_standard(db_session, key_b, "B 的本地")

        resp = await client.get("/api/v1/data-standards", headers={"X-API-Key": raw_b})
        assert resp.status_code == 200
        assert [s["name"] for s in resp.json()] == ["B 的本地"]  # A 的不可见

    async def test_list_page_size_cap(self, client, db_session):
        raw, _ = await _create_key(db_session, "list_cap")
        resp = await client.get("/api/v1/data-standards?page_size=99",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 422  # le=50 门禁


class TestGetStandard:
    async def test_detail(self, client, db_session):
        raw, _ = await _create_key(db_session, "det_ok")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            out = await _import_ok(client, raw, name="详细标准")

        resp = await client.get(f"/api/v1/data-standards/{out['id']}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        d = resp.json()
        assert d["name"] == "详细标准" and d["sheet_count"] == 1
        assert d["sheets"][0]["fields"][0]["field_name"] == "地块编码"
        assert d["sheets"][0]["fields"][0]["ordering"] == 0

    async def test_detail_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "det_404")
        resp = await client.get(f"/api/v1/data-standards/{_uuid.uuid4()}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_detail_forbidden_isolated(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "det_for_a")
        raw_b, _ = await _create_key(db_session, "det_for_b")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            out = await _import_ok(client, raw_a, name="他人标准")

        # 他人 key: owns 不通过 → 与不存在一致 404（不漏信息）
        resp = await client.get(f"/api/v1/data-standards/{out['id']}",
                                headers={"X-API-Key": raw_b})
        assert resp.status_code == 404


class TestDeleteStandard:
    async def test_delete_cascade(self, client, db_session):
        raw, _ = await _create_key(db_session, "del_cas")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            out = await _import_ok(client, raw, name="待删除")
        std_id = out["id"]
        n_st_before = (await db_session.execute(
            select(func.count()).select_from(SourceTable).where(
                SourceTable.standard_id == std_id))).scalar()
        assert n_st_before == 1

        resp = await client.delete(f"/api/v1/data-standards/{std_id}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 204

        assert (await db_session.execute(
            select(func.count()).select_from(DataStandard).where(
                DataStandard.id == std_id))).scalar() == 0
        assert (await db_session.execute(
            select(func.count()).select_from(DataStandardSheet).where(
                DataStandardSheet.standard_id == std_id))).scalar() == 0
        assert (await db_session.execute(
            select(func.count()).select_from(StandardVersion).where(
                StandardVersion.standard_id == std_id))).scalar() == 0
        # SourceField 随 sheet 级联清空
        assert (await db_session.execute(
            select(func.count()).select_from(SourceField)
            .where(SourceField.table_id.in_(
                select(SourceTable.id).where(SourceTable.standard_id == std_id))))).scalar() == 0
        # SourceTable 本身保留（数据表不属于标准级联域），standard_id 被 FK 置空
        st = (await db_session.execute(
            select(SourceTable).where(SourceTable.standard_id == std_id))).scalar_one_or_none()
        if st is not None:
            assert st.standard_id is None

    async def test_delete_not_found(self, client, db_session):
        raw, _ = await _create_key(db_session, "del_404")
        resp = await client.delete(f"/api/v1/data-standards/{_uuid.uuid4()}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_delete_forbidden_isolated(self, client, db_session):
        raw_a, key_a = await _create_key(db_session, "del_for_a")
        raw_b, _ = await _create_key(db_session, "del_for_b")
        tmp = Path(tempfile.mkdtemp())
        with patch.object(settings, "upload_dir", str(tmp)):
            out = await _import_ok(client, raw_a, name="删他人")

        resp = await client.delete(f"/api/v1/data-standards/{out['id']}",
                                   headers={"X-API-Key": raw_b})
        assert resp.status_code == 404
        assert (await db_session.execute(
            select(func.count()).select_from(DataStandard).where(
                DataStandard.id == out["id"]))).scalar() == 1  # 未被删