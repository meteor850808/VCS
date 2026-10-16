# -*- coding: utf-8 -*-
"""行级去重 + update/upsert 兜底：三格式 × 全路径对齐 JSON 路径的既有契约。

用户实测报告（2026-10-08）：
  - Excel/CSV 导入带同表重复字段名的文件 → 纯新建 409（uq_sf_table_field）
  - 同一文件选「新建/更新」(upsert) → **500**（update 分支没包 IntegrityError）

根因：v2.22.3 只给 JSON 路径加了「行级重复保首个+告警」与 IntegrityError
兜底；Excel v2 / Excel legacy / CSV archive / CSV legacy 四条路径全没有。
本文件把同一契约铺满全路径：
  A. 同表重复字段名 / 同字典重复条目码 → 201 + 告警 + 计数为实际创建数
  B. 结构级重复（同层组重复表名）在 update/upsert 下 → **409 精确归因**（不是 500）
"""
import io
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

import tests.test_imp_exp_dict_roundtrip as T  # noqa: E402

BASE = "/api/v1/data-standards-v2"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_db(test_db):
    yield


def _v2_xlsx_with_dups() -> bytes:
    """v2 四 Sheet 模板，故意埋两处行级重复：
    - ROAD 表里 field_name='DIZHI' ×2（uq_sf_table_field）
    - 字典 CZ 里 item_code='A' ×2（uq_di_dict_code）
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = "标准信息"
    for row in [["字段名", "值"], ["标准名称", "重复字段Excel"],
                ["标准编码", "DUP-X1"], ["分类", "custom"]]:
        ws0.append(row)
    ws1 = wb.create_sheet("层组定义")
    for row in [["层组名", "层组编码", "上级编码", "描述"],
                ["道路层", "DL", "", ""]]:
        ws1.append(row)
    ws2 = wb.create_sheet("数据表定义")
    for row in [["层组编码", "表名", "表编码", "类型", "几何类型", "描述"],
                ["DL", "道路", "ROAD", "spatial", "LineString", ""]]:
        ws2.append(row)
    ws3 = wb.create_sheet("字段定义")
    for row in [["表编码", "字段名", "别名", "类型", "长度", "精度", "必填", "主键", "描述"],
                ["ROAD", "DIZHI", None, "string", 50, None, "", "", ""],
                ["ROAD", "DIZHI", None, "string", 64, None, "", "", ""],   # ← 重复
                ["ROAD", "NAME", None, "string", 100, None, "", "", ""]]:
        ws3.append(row)
    ws4 = wb.create_sheet("字典定义")
    for row in [["字典编码", "字典名称", "适用范围", "字典项编码", "字典项名称", "描述"],
                ["CZ", "材质", "standard", "A", "沥青", ""],
                ["CZ", "材质", "standard", "A", "沥青重复", ""],           # ← 重复
                ["CZ", "材质", "standard", "B", "水泥", ""]]:
        ws4.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _legacy_xlsx_with_dup_fields() -> bytes:
    """旧扁平结构（非 v2 模板）：单 Sheet，字段行 DIZHI ×2。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "道路"
    for row in [["字段名", "类型", "长度", "是否可空", "允许值", "描述"],
                ["DIZHI", "string", 50, "是", None, None],
                ["DIZHI", "string", 64, "是", None, None],
                ["NAME", "string", 100, "是", None, None]]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _v2_xlsx_dup_tables() -> bytes:
    """结构级重复：同层组下两个同名表（仍应 409，但不得 500）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = "标准信息"
    for row in [["字段名", "值"], ["标准名称", "重复表Excel"],
                ["标准编码", "DUPT-X1"], ["分类", "custom"]]:
        ws0.append(row)
    ws1 = wb.create_sheet("层组定义")
    for row in [["层组名", "层组编码", "上级编码", "描述"],
                ["道路层", "DL", "", ""]]:
        ws1.append(row)
    ws2 = wb.create_sheet("数据表定义")
    for row in [["层组编码", "表名", "表编码", "类型", "几何类型", "描述"],
                ["DL", "道路", "ROAD_A", "spatial", "LineString", ""],
                ["DL", "道路", "ROAD_B", "spatial", "LineString", ""]]:  # ← 同层组同名表
        ws2.append(row)
    ws3 = wb.create_sheet("字段定义")
    ws3.append(["表编码", "字段名", "别名", "类型", "长度", "精度", "必填", "主键", "描述"])
    ws3.append(["ROAD_A", "NAME", None, "string", 50, None, "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _export_csv(client, raw, std_id) -> bytes:
    r = await client.post(f"{BASE}/{std_id}/export?format=csv",
                          headers={"X-API-Key": raw})
    assert r.status_code == 200, r.status_code
    return r.content


def _inject_dup_rows(zip_bytes: bytes):
    """往归档 ZIP 的 字段.csv / 字典项.csv 里各注入一行重复（返回改写后的 ZIP）。"""
    zin = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "字段.csv":
                text = data.decode("utf-8-sig")
                lines = text.splitlines()
                lines.append(lines[1])          # 复制首个字段行 → 同表重复字段名
                data = ("\r\n".join(lines)).encode("utf-8-sig")
            elif info.filename == "字典项.csv":
                text = data.decode("utf-8-sig")
                lines = text.splitlines()
                lines.append(lines[1])          # 复制首个条目行 → 同字典重复条目码
                data = ("\r\n".join(lines)).encode("utf-8-sig")
            zout.writestr(info.filename, data)
    return out.getvalue()


def _zip_parts(zip_bytes: bytes):
    """归档导入契约：ZIP 要拆成内层文件按多 part 发（端点不解压整包）。"""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return [("files", (n, zf.read(n), "application/octet-stream"))
            for n in zf.namelist()]


class TestExcelV2RowDedup:
    async def test_dup_fields_and_items_deduped_with_warning(self, client, db_session):
        raw, key = await T._key(db_session, "dup_x2")
        r = await client.post(f"{BASE}/import/excel",
                              headers={"X-API-Key": raw},
                              data={"name": "重复字段Excel"},
                              files={"file": ("a.xlsx", _v2_xlsx_with_dups(), XLSX_CT)})
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        assert body["fields_count"] == 2, body      # DIZHI 保首个 + NAME
        w = " ".join(body["warnings"])
        assert "DIZHI" in w and "重复" in w, body["warnings"]
        assert "'A'" in w and "重复" in w, body["warnings"]

    async def test_upsert_structural_dup_409_not_500(self, client, db_session):
        """结构级重复（同层组同名表）在 upsert 下必须 409 精确归因，不是 500。"""
        raw, key = await T._key(db_session, "dup_xt")
        # 先建一个同名标准，让 upsert 命中 update 分支
        std_id = await T._seed_rich_standard(db_session, key, "重复表Excel")
        await db_session.commit()
        r = await client.post(f"{BASE}/import/excel",
                              headers={"X-API-Key": raw},
                              data={"name": "重复表Excel", "mode": "upsert"},
                              files={"file": ("a.xlsx", _v2_xlsx_dup_tables(), XLSX_CT)})
        assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text[:200]}"
        assert "数据表" in r.json()["detail"], r.text


class TestExcelLegacyRowDedup:
    async def test_dup_fields_deduped_with_warning(self, client, db_session):
        raw, key = await T._key(db_session, "dup_lg")
        r = await client.post(f"{BASE}/import/excel",
                              headers={"X-API-Key": raw},
                              data={"name": "重复字段旧Excel"},
                              files={"file": ("a.xlsx", _legacy_xlsx_with_dup_fields(), XLSX_CT)})
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        assert body["fields_count"] == 2, body
        w = " ".join(body["warnings"])
        assert "DIZHI" in w and "重复" in w, body["warnings"]


class TestCsvArchiveRowDedup:
    async def test_dup_fields_and_items_deduped_with_warning(self, client, db_session):
        raw, key = await T._key(db_session, "dup_cv")
        std_id = await T._seed_rich_standard(db_session, key, "重复字段CSV")
        await db_session.commit()
        zip_bytes = await _export_csv(client, raw, std_id)
        r = await client.post(f"{BASE}/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "重复字段CSV-新"},
                              files=_zip_parts(_inject_dup_rows(zip_bytes)))
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        w = " ".join(body["warnings"])
        assert "重复" in w, body["warnings"][:6]

    async def test_upsert_dup_table_rows_are_lenient(self, client, db_session):
        """归档路径对重复表行是既有宽容语义（v2.22.3）：保首个 + 告警，upsert 下 201。"""
        raw, key = await T._key(db_session, "dup_cvt")
        std_id = await T._seed_rich_standard(db_session, key, "重复表CSV")
        await db_session.commit()
        zip_bytes = await _export_csv(client, raw, std_id)
        zin = zipfile.ZipFile(io.BytesIO(zip_bytes))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "数据表.csv":
                    lines = data.decode("utf-8-sig").splitlines()
                    lines.append(lines[1])          # 复制首行 → 同层组同表名
                    data = ("\r\n".join(lines)).encode("utf-8-sig")
                zout.writestr(info.filename, data)
        r = await client.post(f"{BASE}/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "重复表CSV", "mode": "upsert"},
                              files=_zip_parts(out.getvalue()))
        assert r.status_code == 201, r.text[:300]
        w = " ".join(r.json()["warnings"])
        assert "重复" in w, r.json()["warnings"][:6]

    async def test_upsert_dict_name_dup_409_not_500(self, client, db_session):
        """字典重名行（不同 code 同名）撞 uq_sd_standard_name —— upsert 下 409 不是 500。"""
        raw, key = await T._key(db_session, "dup_cvn")
        std_id = await T._seed_rich_standard(db_session, key, "重名字典CSV")
        await db_session.commit()
        zip_bytes = await _export_csv(client, raw, std_id)
        zin = zipfile.ZipFile(io.BytesIO(zip_bytes))
        dict_name = None
        for info in zin.infolist():
            if info.filename == "字典.csv":
                lines = zin.read(info.filename).decode("utf-8-sig").splitlines()
                dict_name = lines[1].split(",")[1]
                break
        assert dict_name, "归档里没找到字典行"
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "字典.csv":
                    lines = data.decode("utf-8-sig").splitlines()
                    # 同名不同 code 的第二行 → uq_sd_standard_name
                    lines.append(f"ZZNEW,{dict_name},standard,")
                    data = ("\r\n".join(lines)).encode("utf-8-sig")
                zout.writestr(info.filename, data)
        r = await client.post(f"{BASE}/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "重名字典CSV", "mode": "upsert"},
                              files=_zip_parts(out.getvalue()))
        assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text[:200]}"
        assert "字典" in r.json()["detail"], r.text


class TestCsvLegacyRowDedup:
    async def test_dup_header_columns_deduped_with_warning(self, client, db_session):
        """legacy CSV 表头出现重复列名 → 保首个 + 告警，不 409。"""
        raw, key = await T._key(db_session, "dup_cl")
        csv_text = "DIZHI,DIZHI,NAME\r\nv1,v2,v3\r\n"
        r = await client.post(f"{BASE}/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "重复列CSV"},
                              files={"files": ("t.csv", csv_text.encode("utf-8-sig"), "text/csv")})
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        assert body["fields_count"] == 2, body
        w = " ".join(body["warnings"])
        assert "DIZHI" in w and "重复" in w, body["warnings"]


class TestJsonUpsertConflictNot500:
    async def test_upsert_structural_dup_409_not_500(self, client, db_session):
        """JSON upsert 撞结构级重复同样必须 409（此前 update 分支裸奔 → 500）。"""
        raw, key = await T._key(db_session, "dup_js")
        std_id = await T._seed_rich_standard(db_session, key, "重复表JSON")
        await db_session.commit()
        payload = {
            "name": "重复表JSON",
            "layer_groups": [{
                "group_name": "道路",
                "tables": [
                    {"table_name": "路面", "table_code": "RD_A", "fields": []},
                    {"table_name": "路面", "table_code": "RD_B", "fields": []},
                ],
            }],
        }
        r = await client.post(f"{BASE}/import/json?mode=upsert",
                              headers={"X-API-Key": raw}, json=payload)
        assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text[:200]}"
        assert "数据表" in r.json()["detail"], r.text
