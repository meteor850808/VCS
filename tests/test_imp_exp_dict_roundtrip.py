# -*- coding: utf-8 -*-
"""v2.22.3 — 数据标准「字典绑定随模板往返」+「CSV 导出补齐」。

本文件守卫两个 v2.22.2 查实的技术债的闭合：

1. **字典绑定不可随模板往返**（BASELINE §17）
   修复方案 A：「字段定义」Sheet 增第 10 列「字典编码」（逗号分隔多值，
   匹配 `StandardFieldDictLink` 的 `UniqueConstraint(field_id, dict_id)` N:M）。
   JSON 侧同构：字段对象增 `dict_codes` 数组。
   - 首个编码记为 `is_primary=True`（列表首个即主字典）。
   - `filter_condition` 保持 None —— 该列全仓 0 处消费（死字段），不臆造语义。

2. **导出 CSV 端点缺失**（BASELINE §17）
   `export_standard` 补 `csv` 分支，产出 ZIP（每表一个 `<表名>.csv`，
   表头 = 字段名，与 CSV 导入 `_create_csv_children` 的 `rows[0]` 约定一致）。
   **ZIP 内不含字典**：CSV 导入路径无字典概念，字典走 Excel/JSON 往返。

回归护栏：往返测试用 **ORM 直搭** 源状态（不依赖被测的导入路径），
确保测的是"导出→再导入"这条真实链路，而非"导入自己认自己"。
"""
import io
import csv
import json as _json
import zipfile
import uuid

import pytest
import pytest_asyncio
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.api.v1.data_standard_v2 import import_export as ie
from app.api.v1.data_standard_v2 import _common as C
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

pytestmark = pytest.mark.asyncio

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _key(db_session, name):
    raw, hashed = generate_api_key()
    k = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(k)
    await db_session.commit()
    return raw, k


# ═══════════════════════════════════════════════════════════════════════════
# ORM 直搭源状态（不经导入路径 —— 往返测试的"给定"必须独立于被测代码）
# ═══════════════════════════════════════════════════════════════════════════

async def _seed_full_standard(db_session, key, name, *, dict_codes=("MAT", "GRADE")):
    """标准 → 层组 → 表 → 字段 → 字典(2) + 字典项 + 字段-字典关联(N:M)。

    返回 (std_id, field_id, {dict_code: dict_id})
    """
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id, name=name, code="RT-1")
    lg = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                            group_name="道路", group_code="RD")
    tbl = StandardTable(id=str(uuid.uuid4()), standard_id=std.id, layer_id=lg.id,
                        table_name="路面", table_code="SURF", table_type="attribute")
    fld = StandardField(id=str(uuid.uuid4()), standard_id=std.id, table_id=tbl.id,
                        field_name="mat", field_alias="材料", data_type="string")
    dicts = {}
    for code, item_code in zip(dict_codes, ("A", "B")):
        d = StandardDictionary(id=str(uuid.uuid4()), standard_id=std.id,
                               api_key_id=key.id, dict_name=f"字典{code}", dict_code=code)
        db_session.add(d)
        db_session.add(DictionaryItem(id=str(uuid.uuid4()), dict_id=d.id,
                                      item_code=item_code, item_name=item_code, ordering=0))
        dicts[code] = d.id

    db_session.add_all([std, lg, tbl, fld])
    db_session.add(C._primary_table_link(tbl))
    for i, code in enumerate(dict_codes):
        db_session.add(StandardFieldDictLink(
            id=str(uuid.uuid4()), field_id=fld.id, dict_id=dicts[code],
            is_primary=(i == 0), ordering=i,
        ))
    await db_session.commit()
    return std.id, fld.id, dicts


def _wb_bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _v2_xlsx_with_dicts(std_name, field_dict_cell):
    """v2 模板：1 层组 + 1 表 + 2 字段；字段定义的第 10 列为「字典编码」。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "标准信息"
    ws.append(["字段名", "值"])
    ws.append(["标准名称", std_name])
    ws.append(["标准编码", "RT-1"])
    ws2 = wb.create_sheet("层组定义")
    ws2.append(["层组名", "层组编码", "父级编码", "描述"])
    ws2.append(["道路", "RD", "", ""])
    ws3 = wb.create_sheet("数据表定义")
    ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
    ws3.append(["RD", "路面", "SURF", "attribute", "", ""])
    ws4 = wb.create_sheet("字段定义")
    ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度",
                "精度", "必填", "主键", "描述", "字典编码"])
    ws4.append(["SURF", "mat", "材料", "string", "", "", "", "", "", field_dict_cell])
    ws4.append(["SURF", "name", "名称", "string", "", "", "", "", "", ""])
    ws5 = wb.create_sheet("字典定义")
    ws5.append(["字典编码", "字典名称", "适用范围", "字典项编码", "字典项名称", "描述"])
    for code, item in (("MAT", "A"), ("GRADE", "B")):
        ws5.append([code, f"字典{code}", "standard", item, item, ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _links_of(db_session, field_id):
    rows = (await db_session.execute(
        select(StandardFieldDictLink).where(StandardFieldDictLink.field_id == field_id)
    )).scalars().all()
    out = []
    for r in rows:
        d = await db_session.get(StandardDictionary, r.dict_id)
        out.append((d.dict_code, r.is_primary))
    return sorted(out)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Excel：模板列 / 解析 / 建链
# ═══════════════════════════════════════════════════════════════════════════

class TestExcelDictCodeColumn:

    async def test_template_field_sheet_has_dict_code_header(self, client):
        """下载模板的「字段定义」Sheet 必须有第 10 列「字典编码」，且有示例行 + 填写说明。"""
        r = await client.get("/api/v1/data-standards-v2/template/excel")
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert "字段定义" in wb.sheetnames
        header = [c.value for c in wb["字段定义"][1]]
        assert header[9] == "字典编码"
        cells = [row[9] for row in wb["字段定义"].iter_rows(min_row=2, values_only=True)]
        # 示例行演示多字典写法
        assert any(c and "," in str(c) for c in cells), f"模板应演示多字典编码写法: {cells}"
        # 填写说明行，且该行不得被解析成幽灵字段（字段名为空）
        hints = [c for c in cells if c and "字典定义" in str(c)]
        assert hints, "模板应写明字典编码的填写约束"
        hint_row = next(r_ for r_ in wb["字段定义"].iter_rows(min_row=2, values_only=True)
                        if r_[9] and "字典定义" in str(r_[9]))
        assert not (hint_row[0] or "").strip() and not (hint_row[1] or "").strip()
        wb.close()

    def test_parser_splits_multiple_separators(self, tmp_path):
        """解析器：逗号/中文逗号/分号/空白 都要能拆；空值/表头/重复项不产生脏编码。"""
        from app.services.data_standard_parser import parse_standard_excel_v2

        def _dict_codes(cell):
            wb = Workbook()
            ws = wb.active
            ws.title = "标准信息"
            ws.append(["字段名", "值"])
            ws.append(["标准名称", "S"])
            ws2 = wb.create_sheet("层组定义")
            ws2.append(["层组名", "层组编码", "父级编码", "描述"])
            ws2.append(["G", "G", "", ""])
            ws3 = wb.create_sheet("数据表定义")
            ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
            ws3.append(["G", "T", "TC", "attribute", "", ""])
            ws4 = wb.create_sheet("字段定义")
            ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度",
                        "精度", "必填", "主键", "描述", "字典编码"])
            ws4.append(["TC", "f", "", "string", "", "", "", "", "", cell])
            p = tmp_path / f"d_{abs(hash(str(cell)))}.xlsx"
            p.write_bytes(_wb_bytes(wb))
            res = parse_standard_excel_v2(str(p))
            return res.fields[0].dict_codes

        assert _dict_codes("MAT") == ["MAT"]
        assert _dict_codes("MAT,GRADE") == ["MAT", "GRADE"]
        assert _dict_codes("MAT，GRADE") == ["MAT", "GRADE"]
        assert _dict_codes("MAT, GRADE") == ["MAT", "GRADE"]
        assert _dict_codes(" MAT , GRADE ") == ["MAT", "GRADE"]
        assert _dict_codes("MAT;GRADE") == ["MAT", "GRADE"]
        assert _dict_codes("MAT MAT") == ["MAT"]          # 同格重复 → 去重（否则撞唯一约束）
        assert _dict_codes("") == []
        assert _dict_codes(None) == []
        assert _dict_codes("字典编码") == []                # 表头残留不算编码

    def test_parser_skips_hint_row_as_ghost_field(self, tmp_path):
        """模板末尾只有末列有字的「填写说明行」不得被解析成字段。"""
        from app.services.data_standard_parser import parse_standard_excel_v2

        wb = Workbook()
        ws = wb.active
        ws.title = "标准信息"
        ws.append(["字段名", "值"])
        ws.append(["标准名称", "S"])
        ws2 = wb.create_sheet("层组定义")
        ws2.append(["层组名", "层组编码", "父级编码", "描述"])
        ws2.append(["G", "G", "", ""])
        ws3 = wb.create_sheet("数据表定义")
        ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
        ws3.append(["G", "T", "TC", "attribute", "", ""])
        ws4 = wb.create_sheet("字段定义")
        ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度",
                    "精度", "必填", "主键", "描述", "字典编码"])
        ws4.append(["TC", "f", "", "string", "", "", "", "", "", "MAT"])
        ws4.append(["", "", "", "", "", "", "", "", "", "编码须与字典定义一致"])
        # 纯空格字段名：openpyxl 读回空串是 None（走 row[1] is None 分支），
        # 但用户敲进去的空格是真实字符串，必须由 not fname 分支兜住
        ws4.append(["TC", "  ", "", "string", "", "", "", "", "", ""])
        p = tmp_path / "hint.xlsx"
        p.write_bytes(_wb_bytes(wb))
        res = parse_standard_excel_v2(str(p))
        assert [f.field_name for f in res.fields] == ["f"]
        assert res.fields[0].dict_codes == ["MAT"]
        assert not any("" in w or " " in w for w in res.warnings), res.warnings

    async def test_legacy_9col_template_still_imports(self, client, db_session):
        """向后兼容：旧 9 列模板（无字典编码列）必须照常导入，不报错、无绑定。"""
        raw, _ = await _key(db_session, "rt_legacy")
        wb = Workbook()
        ws = wb.active
        ws.title = "标准信息"
        ws.append(["字段名", "值"])
        ws.append(["标准名称", "旧9列标准"])
        ws2 = wb.create_sheet("层组定义")
        ws2.append(["层组名", "层组编码", "父级编码", "描述"])
        ws2.append(["G", "G", "", ""])
        ws3 = wb.create_sheet("数据表定义")
        ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
        ws3.append(["G", "T", "TC", "attribute", "", ""])
        ws4 = wb.create_sheet("字段定义")
        ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度",
                    "精度", "必填", "主键", "描述"])          # ← 仅 9 列
        ws4.append(["TC", "f", "", "string", "", "", "", "", ""])
        ws5 = wb.create_sheet("字典定义")
        ws5.append(["字典编码", "字典名称", "适用范围", "字典项编码", "字典项名称", "描述"])
        ws5.append(["MAT", "材料", "standard", "A", "沥青", ""])
        buf = io.BytesIO()
        wb.save(buf)

        r = await client.post("/api/v1/data-standards-v2/import/excel",
                              headers={"X-API-Key": raw},
                              data={"name": "旧9列标准", "mode": "create"},
                              files={"file": ("old9.xlsx", buf.getvalue(), XLSX_CT)})
        assert r.status_code == 201
        assert r.json()["fields_count"] == 1
        fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == r.json()["standard_id"])
        )).scalars().one()
        assert await _links_of(db_session, fld.id) == []

    async def test_import_creates_n_m_links(self, client, db_session):
        """核心：多值逗号 → 建 N 条关联，首个 is_primary=True。"""
        raw, _ = await _key(db_session, "rt_nm")
        r = await client.post(
            "/api/v1/data-standards-v2/import/excel",
            headers={"X-API-Key": raw},
            data={"name": "绑定往返A", "mode": "create"},
            files={"file": ("d.xlsx", _v2_xlsx_with_dicts("绑定往返A", "MAT,GRADE"), XLSX_CT)},
        )
        assert r.status_code == 201
        std_id = r.json()["standard_id"]
        fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == std_id,
                                        StandardField.field_name == "mat")
        )).scalars().one()
        assert await _links_of(db_session, fld.id) == [("GRADE", False), ("MAT", True)]

    async def test_import_unknown_dict_code_warns(self, client, db_session):
        """编码未在「字典定义」声明 → 跳过 + warning（不静默丢）。"""
        raw, _ = await _key(db_session, "rt_unknown")
        r = await client.post(
            "/api/v1/data-standards-v2/import/excel",
            headers={"X-API-Key": raw},
            data={"name": "未知字典", "mode": "create"},
            files={"file": ("d.xlsx", _v2_xlsx_with_dicts("未知字典", "MAT,NOPE"), XLSX_CT)},
        )
        assert r.status_code == 201
        warns = [w for w in r.json()["warnings"] if "NOPE" in w]
        assert warns, f"未声明的字典编码必须产生告警，实际: {r.json()['warnings']}"
        std_id = r.json()["standard_id"]
        fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == std_id,
                                        StandardField.field_name == "mat")
        )).scalars().one()
        assert await _links_of(db_session, fld.id) == [("MAT", True)]

    async def test_export_includes_dict_code_column(self, client, db_session):
        """Excel 导出「字段定义」第 10 列回填字典编码（逗号分隔）。"""
        raw, key = await _key(db_session, "rt_x")
        std_id, _, _ = await _seed_full_standard(db_session, key, "导出带绑定")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=excel",
                              headers={"X-API-Key": raw})
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb["字段定义"]
        header = [c.value for c in ws[1]]
        assert header[9] == "字典编码"
        row = next(r_ for r_ in ws.iter_rows(min_row=2, values_only=True) if r_[1] == "mat")
        assert set(str(row[9]).split(",")) == {"MAT", "GRADE"}
        wb.close()


# ═══════════════════════════════════════════════════════════════════════════
# 2. JSON：dict_codes 同构
# ═══════════════════════════════════════════════════════════════════════════

class TestJsonDictCodes:

    async def test_json_template_shows_dict_codes(self, client):
        r = await client.get("/api/v1/data-standards-v2/template/json")
        assert r.status_code == 200
        tpl = _json.loads(r.content)
        fields = tpl["layer_groups"][0]["tables"][0]["fields"]
        assert any("dict_codes" in f for f in fields), "JSON 模板需演示 dict_codes 写法"
        codes = next(f["dict_codes"] for f in fields if f.get("dict_codes"))
        assert codes == [tpl["dictionaries"][0]["dict_code"]]

    async def test_json_import_creates_links(self, client, db_session):
        raw, _ = await _key(db_session, "rt_j")
        r = await client.post(
            "/api/v1/data-standards-v2/import/json",
            headers={"X-API-Key": raw},
            json={
                "name": "JSON绑定", "code": "JB-1",
                "layer_groups": [{
                    "group_name": "G", "group_code": "G",
                    "tables": [{"table_name": "T", "table_code": "TC",
                                "fields": [
                                    {"field_name": "mat", "dict_codes": ["MAT", "GRADE"]},
                                    {"field_name": "plain"},
                                ]}],
                }],
                "dictionaries": [
                    {"dict_name": "材料", "dict_code": "MAT",
                     "items": [{"item_code": "A", "item_name": "沥青"}]},
                    {"dict_name": "等级", "dict_code": "GRADE",
                     "items": [{"item_code": "B", "item_name": "一级"}]},
                ],
            },
        )
        assert r.status_code == 201
        std_id = r.json()["standard_id"]
        mat = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == std_id,
                                        StandardField.field_name == "mat")
        )).scalars().one()
        plain = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == std_id,
                                        StandardField.field_name == "plain")
        )).scalars().one()
        assert await _links_of(db_session, mat.id) == [("GRADE", False), ("MAT", True)]
        assert await _links_of(db_session, plain.id) == []

    async def test_json_import_unknown_code_warns(self, client, db_session):
        raw, _ = await _key(db_session, "rt_j2")
        r = await client.post(
            "/api/v1/data-standards-v2/import/json",
            headers={"X-API-Key": raw},
            json={
                "name": "JSON未知字典", "code": "JB-2",
                "layer_groups": [{"group_name": "G", "tables": [
                    {"table_name": "T", "table_code": "TC",
                     "fields": [{"field_name": "f", "dict_codes": ["NOPE"]}]}]}],
                "dictionaries": [{"dict_name": "D", "dict_code": "MAT", "items": []}],
            },
        )
        assert r.status_code == 201
        assert any("NOPE" in w for w in r.json()["warnings"])

    async def test_json_export_includes_dict_codes(self, client, db_session):
        raw, key = await _key(db_session, "rt_jx")
        std_id, _, _ = await _seed_full_standard(db_session, key, "JSON导出绑定")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=json",
                              headers={"X-API-Key": raw})
        assert r.status_code == 200
        data = _json.loads(r.content)
        fields = data["layer_groups"][0]["tables"][0]["fields"]
        codes = next(f["dict_codes"] for f in fields if f["field_name"] == "mat")
        assert set(codes) == {"MAT", "GRADE"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. 端到端往返（ORM 直搭源 → 导出 → 再导入 → 绑定仍在）
# ═══════════════════════════════════════════════════════════════════════════

class TestRoundTripPreservesLinks:

    async def test_excel_export_then_reimport_keeps_links(self, client, db_session):
        """Excel 往返：导出 → 原样再导入(mode=update) → 绑定完整复原。

        这是本次闭合的核心断言：_common._delete_standard_children 在 update 时
        删光全部子记录，绑定能回来**只可能**因为导出把编码带了出去、
        导入又建了回来。
        """
        raw, key = await _key(db_session, "rt_e2e")
        std_id, _, _ = await _seed_full_standard(db_session, key, "Excel往返")

        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=excel",
                               headers={"X-API-Key": raw})
        assert r1.status_code == 200

        r2 = await client.post(
            "/api/v1/data-standards-v2/import/excel",
            headers={"X-API-Key": raw},
            data={"name": "Excel往返", "mode": "update"},
            files={"file": ("rt.xlsx", r1.content, XLSX_CT)},
        )
        assert r2.status_code == 201, r2.text
        assert r2.json()["updated"] is True
        new_id = r2.json()["standard_id"]
        assert new_id == std_id

        fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == new_id,
                                        StandardField.field_name == "mat")
        )).scalars().one()
        assert await _links_of(db_session, fld.id) == [("GRADE", False), ("MAT", True)]

    async def test_json_export_then_reimport_keeps_links(self, client, db_session):
        raw, key = await _key(db_session, "rt_e2e_j")
        std_id, _, _ = await _seed_full_standard(db_session, key, "JSON往返")

        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=json",
                               headers={"X-API-Key": raw})
        assert r1.status_code == 200

        r2 = await client.post(
            "/api/v1/data-standards-v2/import/json",
            headers={"X-API-Key": raw},
            json=_json.loads(r1.content),
            params={"mode": "update"},
        )
        assert r2.status_code == 201, r2.text
        new_id = r2.json()["standard_id"]
        fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == new_id,
                                        StandardField.field_name == "mat")
        )).scalars().one()
        assert await _links_of(db_session, fld.id) == [("GRADE", False), ("MAT", True)]


# ═══════════════════════════════════════════════════════════════════════════
# 4. CSV 归档格式：manifest（仅格式标识）+ 6 个规范化角色 CSV
#
# 与 Excel 模板同构的连接键（层组编码/表编码/字典编码），行顺序即 ordering。
# manifest 不含结构数据（A2-1），只含 format/version/encoding/roles/diagnostics。
# ═══════════════════════════════════════════════════════════════════════════

_CSV_ROLES = {
    "standard": "标准信息.csv",
    "layer_groups": "层组.csv",
    "tables": "数据表.csv",
    "fields": "字段.csv",
    "dictionaries": "字典.csv",
    "dictionary_items": "字典项.csv",
}
_CSV_MANIFEST_NAME = "_manifest.json"


async def _seed_rich_standard(db_session, key, name, *, with_empty_dict=True):
    """无损往返的富结构：嵌套层组 + 同名不同编码的两张表 + 空字典 + 多字典项。"""
    std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id, name=name,
                       code=f"RC-{uuid.uuid4().hex[:6]}", description="往返校验用", category="custom")
    g1 = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                            group_name="道路", group_code="RD", description="主层组", ordering=0)
    g2 = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id, parent_id=g1.id,
                            group_name="路面细分", group_code="RD_SUB", ordering=1)
    # 同名表、不同编码：表名不唯一正是「一表一文件」布局做不到无损的原因
    t1 = StandardTable(id=str(uuid.uuid4()), standard_id=std.id, layer_id=g1.id,
                       table_name="路面", table_code="SURF", table_type="attribute",
                       geometry_type="polygon", description="面表", ordering=0)
    t2 = StandardTable(id=str(uuid.uuid4()), standard_id=std.id, layer_id=g2.id,
                       table_name="路面", table_code="SURF2", table_type="attribute", ordering=1)
    f1 = StandardField(id=str(uuid.uuid4()), standard_id=std.id, table_id=t1.id,
                       field_name="mat", field_alias="材料", data_type="string",
                       length=32, is_required=True, default_value="A", description="面材料",
                       ordering=0)
    f2 = StandardField(id=str(uuid.uuid4()), standard_id=std.id, table_id=t2.id,
                       field_name="span", field_alias="跨度", data_type="float",
                       precision=2, is_primary_key=True, ordering=0)
    db_session.add_all([std, g1, g2, t1, t2, f1, f2])
    db_session.add(C._primary_table_link(t1))
    db_session.add(C._primary_table_link(t2))

    dict_ids = {}
    for code, dname, items in (("MAT", "面材料", [("A", "沥青"), ("B", "水泥")]),
                               ("GRADE", "等级", [("L1", "一级")])):
        d = StandardDictionary(id=str(uuid.uuid4()), standard_id=std.id, api_key_id=key.id,
                               dict_code=code, dict_name=dname, scope="standard", description=f"{dname}说明")
        db_session.add(d)
        dict_ids[code] = d.id
        for i, (ic, iname) in enumerate(items):
            db_session.add(DictionaryItem(id=str(uuid.uuid4()), dict_id=d.id,
                                          item_code=ic, item_name=iname, ordering=i))
    if with_empty_dict:
        # 空字典：Excel 导出会被 if d.items 静默丢弃，CSV 归档必须能表达
        db_session.add(StandardDictionary(
            id=str(uuid.uuid4()), standard_id=std.id, api_key_id=key.id,
            dict_code="EMPTY", dict_name="空字典", scope="standard"))
        dict_ids["EMPTY"] = None

    db_session.add(StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f1.id,
                                         dict_id=dict_ids["MAT"], is_primary=True, ordering=0))
    db_session.add(StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f1.id,
                                         dict_id=dict_ids["GRADE"], is_primary=False, ordering=1))
    await db_session.commit()
    return std.id


async def _snapshot(db_session, std_id):
    """导出/导入两侧都用它取结构快照，用于断言「往返无损」。"""
    groups = (await db_session.execute(
        select(StandardLayerGroup).where(StandardLayerGroup.standard_id == std_id)
        .order_by(StandardLayerGroup.ordering))).scalars().all()
    gcode = {g.id: (g.group_code or "") for g in groups}
    tables = (await db_session.execute(
        select(StandardTable).where(StandardTable.standard_id == std_id)
        .order_by(StandardTable.ordering))).scalars().all()
    tcode = {t.id: (t.table_code or "") for t in tables}
    t2layer = {t.id: gcode.get(t.layer_id, "") for t in tables}
    fields = (await db_session.execute(
        select(StandardField).where(StandardField.standard_id == std_id)
        .order_by(StandardField.ordering))).scalars().all()
    dicts = (await db_session.execute(
        select(StandardDictionary).where(StandardDictionary.standard_id == std_id))).scalars().all()
    dcode = {d.id: (d.dict_code or "") for d in dicts}
    items = (await db_session.execute(
        select(DictionaryItem).where(DictionaryItem.dict_id.in_([d.id for d in dicts] or [""]))
        .order_by(DictionaryItem.ordering))).scalars().all()

    links = []
    for f in fields:
        rows = (await db_session.execute(
            select(StandardFieldDictLink).where(StandardFieldDictLink.field_id == f.id)
            .order_by(StandardFieldDictLink.ordering))).scalars().all()
        for r in rows:
            links.append((tcode.get(f.table_id, ""), f.field_name,
                          dcode.get(r.dict_id, ""), r.is_primary))

    return {
        "groups": [(g.group_name, g.group_code or "", gcode.get(g.parent_id, "") if g.parent_id else "",
                    g.description or "") for g in groups],
        "tables": [(t2layer[t.id], t.table_name, t.table_code or "", t.table_type,
                    t.geometry_type or "", t.description or "") for t in tables],
        "fields": [(tcode.get(f.table_id, ""), f.field_name, f.field_alias or "", f.data_type,
                    f.length, f.precision, bool(f.is_required), bool(f.is_primary_key),
                    f.description or "", f.default_value or "") for f in fields],
        "dicts": sorted((d.dict_code or "", d.dict_name, d.scope or "", d.description or "")
                        for d in dicts),
        "items": sorted((dcode.get(i.dict_id, ""), i.item_code, i.item_name,
                         i.parent_code or "", i.description or "", i.ordering) for i in items),
        "links": sorted(links),
    }


def _csv_rows(raw: bytes):
    """读归档里的某个角色 CSV → list[list[str]]（含表头行）。"""
    text = raw.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _upload_all(zf):
    return [("files", (n, zf.read(n), "application/octet-stream")) for n in zf.namelist()]


def _files_from_zip(raw: bytes):
    return _upload_all(zipfile.ZipFile(io.BytesIO(raw)))


async def _export_csv(client, raw_key, std_id) -> bytes:
    r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                          headers={"X-API-Key": raw_key})
    assert r.status_code == 200, r.text
    return r.content


def _repack(raw: bytes, *, rows=None, manifest=None, drop=(), extra=None) -> bytes:
    """从导出的归档出发改写后重新打包，模拟「用户在 Excel 里手改归档」。

    rows     —— {角色: 完整行列表（含表头行）}
    manifest —— dict（就地 update）或 callable(manifest_dict)
    drop     —— 要移除的成员名
    extra    —— 额外塞进去的 {成员名: 字节}
    """
    members = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)
    for role, body in (rows or {}).items():
        buf = io.StringIO()
        csv.writer(buf).writerows(body)
        members[_CSV_ROLES[role]] = ("﻿" + buf.getvalue()).encode("utf-8")
    if manifest is not None:
        m = _json.loads(members[_CSV_MANIFEST_NAME].decode("utf-8"))
        manifest(m) if callable(manifest) else m.update(manifest)
        members[_CSV_MANIFEST_NAME] = _json.dumps(m, ensure_ascii=False).encode("utf-8")
    for name in drop:
        members.pop(name, None)
    members.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def _import_csv(client, raw_key, files, name, mode="create", **form):
    form.setdefault("name", name)
    form.setdefault("mode", mode)
    return await client.post("/api/v1/data-standards-v2/import/csv",
                             headers={"X-API-Key": raw_key}, data=form, files=files)


class TestExportCsvArchive:
    """导出侧：manifest + 6 角色 CSV，A2-1 下 manifest 不得含结构数据。"""

    async def test_archive_has_manifest_and_six_role_files(self, client, db_session):
        raw, key = await _key(db_session, "rt_a1")
        std_id = await _seed_rich_standard(db_session, key, "归档结构")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = set(zf.namelist())
        assert _CSV_MANIFEST_NAME in names
        assert set(_CSV_ROLES.values()) <= names, f"缺角色文件: {set(_CSV_ROLES.values()) - names}"

    async def test_manifest_carries_no_structural_data(self, client, db_session):
        """钉住 A2-1：manifest 只有格式元数据，CSV 是唯一数据源。

        若日后有人把结构数据塞进 manifest，就会出现「改 CSV 无效」的双源问题。
        """
        raw, key = await _key(db_session, "rt_a2")
        std_id = await _seed_rich_standard(db_session, key, "manifest 纯净")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            m = _json.loads(zf.read(_CSV_MANIFEST_NAME).decode("utf-8"))
        assert m["format"] == "vcs-standard-csv"
        assert m["version"] == 1
        assert m["roles"] == _CSV_ROLES
        # 任何看起来像结构数据的键都不允许存在
        forbidden = {"layer_groups", "tables", "fields", "dictionaries", "dictionary_items",
                     "data", "standard", "items", "links"}
        assert not (forbidden & set(m)), f"manifest 混入了结构数据: {forbidden & set(m)}"

    async def test_dictionary_is_split_into_two_roles(self, client, db_session):
        """字典拆两文件：同一字典在 字典.csv 只出现一行，不随字典项重复。"""
        raw, key = await _key(db_session, "rt_a3")
        std_id = await _seed_rich_standard(db_session, key, "字典拆分")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            dicts = _csv_rows(zf.read(_CSV_ROLES["dictionaries"]))
            items = _csv_rows(zf.read(_CSV_ROLES["dictionary_items"]))
        codes = [row[0] for row in dicts[1:]]
        assert codes.count("MAT") == 1, f"MAT 有 2 个字典项，不应在 字典.csv 出现两次: {codes}"
        assert set(codes) == {"MAT", "GRADE", "EMPTY"}
        # 2 项 + 1 项 = 3 行字典项
        assert sorted(row[0] for row in items[1:]) == ["GRADE", "MAT", "MAT"]

    async def test_empty_dictionary_survives(self, client, db_session):
        """空字典（无字典项）必须出现在 字典.csv，且 字典项.csv 无对应行。

        Excel 导出在这里是 `if d.items:` 静默丢弃 —— 这是本次改造发现的既存缺陷。
        """
        raw, key = await _key(db_session, "rt_a4")
        std_id = await _seed_rich_standard(db_session, key, "空字典")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            dicts = _csv_rows(zf.read(_CSV_ROLES["dictionaries"]))
            items = _csv_rows(zf.read(_CSV_ROLES["dictionary_items"]))
        assert "EMPTY" in [row[0] for row in dicts[1:]]
        assert "EMPTY" not in [row[0] for row in items[1:]]

    async def test_all_csv_members_use_utf8_bom(self, client, db_session):
        """中文用户在 Excel 里双击 CSV：BOM 缺失就是乱码。"""
        raw, key = await _key(db_session, "rt_a5")
        std_id = await _seed_rich_standard(db_session, key, "BOM")
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for name in _CSV_ROLES.values():
                assert zf.read(name).startswith(b"\xef\xbb\xbf"), f"{name} 缺 UTF-8 BOM"

    async def test_duplicate_codes_recorded_as_diagnostics(self, client, db_session):
        """编码重复会让往返有损；二进制响应无法携带告警，故写入 manifest.diagnostics。"""
        raw, key = await _key(db_session, "rt_a6")
        std_id = await _seed_rich_standard(db_session, key, "重复编码")
        t = (await db_session.execute(
            select(StandardTable).where(StandardTable.standard_id == std_id))).scalars().all()
        t[1].table_code = "SURF"          # 制造表编码重复
        await db_session.commit()
        r = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                              headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            m = _json.loads(zf.read(_CSV_MANIFEST_NAME).decode("utf-8"))
        assert any("SURF" in d for d in m["diagnostics"]), m

    async def test_empty_standard_yields_header_only_roles(self, client, db_session):
        """空标准 → 6 个只有表头的 CSV（不是空 ZIP），保证格式自解释。"""
        raw, key = await _key(db_session, "rt_a7")
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id, name="空标准")
        db_session.add(std)
        await db_session.commit()
        r = await client.post(f"/api/v1/data-standards-v2/{std.id}/export?format=csv",
                              headers={"X-API-Key": raw})
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for role, name in _CSV_ROLES.items():
                assert name in zf.namelist()
                # 标准信息是单行语义（表头 + 1 行），其余角色空标准下只有表头
                expected = 2 if role == "standard" else 1
                assert len(_csv_rows(zf.read(name))) == expected, f"{name} 行数不对"

    async def test_export_still_400_for_unknown_format(self, client, db_session):
        raw, _ = await _key(db_session, "rt_a8")
        r = await client.post("/api/v1/data-standards-v2/import/json",
                              headers={"X-API-Key": raw}, json={"name": "未知格式"})
        std_id = r.json()["standard_id"]
        r2 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=xml",
                               headers={"X-API-Key": raw})
        assert r2.status_code == 400
        assert "不支持的导出格式" in r2.json()["detail"]


class TestImportCsvArchive:
    """导入侧：manifest 路径无损往返 + legacy 路径零破坏。"""

    async def test_archive_roundtrip_preserves_whole_structure(self, client, db_session):
        """核心断言：导出 → 导入，层组/表/字段/字典/字典项/绑定 全等。"""
        raw, key = await _key(db_session, "rt_i1")
        std_id = await _seed_rich_standard(db_session, key, "往返源")
        before = await _snapshot(db_session, std_id)

        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "往返副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        after = await _snapshot(db_session, r2.json()["standard_id"])

        assert after["groups"] == before["groups"]
        assert after["tables"] == before["tables"]
        assert after["fields"] == before["fields"]
        assert after["dicts"] == before["dicts"]
        assert after["items"] == before["items"]
        assert after["links"] == before["links"]

    async def test_archive_roundtrip_keeps_nested_group_parent(self, client, db_session):
        """父级编码必须还原成真正的父子关系，不能塌成平铺。"""
        raw, key = await _key(db_session, "rt_i2")
        std_id = await _seed_rich_standard(db_session, key, "嵌套父级")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "嵌套副本", "mode": "create"}, files=files)
        child = next(g for g in (await _snapshot(db_session, r2.json()["standard_id"]))["groups"]
                     if g[1] == "RD_SUB")
        assert child[2] == "RD", f"子层组父级应为 RD，实际 {child[2]!r}"

    async def test_archive_roundtrip_preserves_dict_links(self, client, db_session):
        raw, key = await _key(db_session, "rt_i3")
        std_id = await _seed_rich_standard(db_session, key, "绑定往返")
        fld_id = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == std_id,
                                         StandardField.field_name == "mat"))).scalars().one().id
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "绑定副本", "mode": "create"}, files=files)
        new_std = r2.json()["standard_id"]
        new_fld = (await db_session.execute(
            select(StandardField).where(StandardField.standard_id == new_std,
                                         StandardField.field_name == "mat"))).scalars().one()
        assert await _links_of(db_session, new_fld.id) == [("GRADE", False), ("MAT", True)]
        assert new_fld.id != fld_id

    async def test_archive_unknown_dict_code_warns_not_fails(self, client, db_session):
        raw, key = await _key(db_session, "rt_i4")
        std_id = await _seed_rich_standard(db_session, key, "未知编码")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as out:
                for n in zf.namelist():
                    if n == _CSV_ROLES["fields"]:
                        rows = _csv_rows(zf.read(n))
                        rows[1][-2] = "MAT,NOPE"        # NOPE 不在 字典.csv
                        s = io.StringIO()
                        csv.writer(s).writerows(rows)
                        out.writestr(n, "﻿" + s.getvalue())
                    else:
                        out.writestr(n, zf.read(n))
            buf.seek(0)
            files = _upload_all(zipfile.ZipFile(buf))
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "未知编码副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert any("NOPE" in w for w in r2.json()["warnings"]), r2.json()["warnings"]

    async def test_archive_duplicate_code_warns_on_import(self, client, db_session):
        """用户手改 CSV 引入重复编码 → 导入侧也要检出（首次出现生效）。

        断言必须锚定「表编码重复」+ 编码本身，不能用裸「重复」：
        种子/导入名一旦含「重复」，导入必发的标准名称冲突告警就会满足它。
        """
        raw, key = await _key(db_session, "rt_i5")
        std_id = await _seed_rich_standard(db_session, key, "手改归档")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as out:
                for n in zf.namelist():
                    if n == _CSV_ROLES["tables"]:
                        rows = _csv_rows(zf.read(n))
                        rows.append(list(rows[1]))     # 整行复制 → 表编码重复
                        s = io.StringIO()
                        csv.writer(s).writerows(rows)
                        out.writestr(n, "﻿" + s.getvalue())
                    else:
                        out.writestr(n, zf.read(n))
            buf.seek(0)
            files = _upload_all(zipfile.ZipFile(buf))
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "手改归档副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert any("表编码重复" in w and "SURF" in w for w in r2.json()["warnings"]), \
            r2.json()["warnings"]
        # 「首次出现生效」= 重复行不落库（DB 上有 uq_st_layer_code / uq_st_layer_name）
        assert r2.json()["tables_count"] == 2, r2.json()["tables_count"]

    async def test_archive_diagnostics_are_replayed_as_warnings(self, client, db_session):
        """manifest.diagnostics 必须在导入时回放，否则用户看不到归档有损。"""
        raw, key = await _key(db_session, "rt_i6")
        std_id = await _seed_rich_standard(db_session, key, "诊断回放")
        t = (await db_session.execute(
            select(StandardTable).where(StandardTable.standard_id == std_id))).scalars().all()
        t[1].table_code = "SURF"
        await db_session.commit()
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "诊断回放副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert any("SURF" in w and "重复" in w for w in r2.json()["warnings"]), r2.json()["warnings"]

    async def test_legacy_csv_without_manifest_still_imports(self, client, db_session):
        """零破坏回归：无 manifest 的外部 CSV 仍按「每文件一张表、字段取首行」导入。"""
        raw, key = await _key(db_session, "rt_i7")
        files = [
            ("files", ("控制点.csv", "﻿pt_id,pt_name,pt_type\n001,控制点1,GPS\n".encode("utf-8"), "text/csv")),
            ("files", ("水准点.csv", "﻿bm_id,bm_name,bm_level\n001,水准点1,一等\n".encode("utf-8"), "text/csv")),
        ]
        r = await client.post("/api/v1/data-standards-v2/import/csv",
                              headers={"X-API-Key": raw},
                              data={"name": "外部CSV", "mode": "create"}, files=files)
        assert r.status_code == 201, r.text
        assert r.json()["tables_count"] == 2
        assert r.json()["fields_count"] == 6
        snap = await _snapshot(db_session, r.json()["standard_id"])
        assert [t[1] for t in snap["tables"]] == ["控制点", "水准点"]
        assert all(f[3] == "string" for f in snap["fields"]), "legacy 路径字段类型恒为 string"

    async def test_archive_missing_required_role_is_400(self, client, db_session):
        raw, key = await _key(db_session, "rt_i8")
        std_id = await _seed_rich_standard(db_session, key, "缺角色")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = [f for f in _upload_all(zf)
                     if not f[1][0].endswith(_CSV_ROLES["fields"])]
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "缺角色副本", "mode": "create"}, files=files)
        assert r2.status_code == 400, r2.text
        assert "fields" in r2.json()["detail"]

    async def test_archive_future_version_is_400(self, client, db_session):
        raw, key = await _key(db_session, "rt_i9")
        std_id = await _seed_rich_standard(db_session, key, "未来版本")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as out:
                for n in zf.namelist():
                    if n == _CSV_MANIFEST_NAME:
                        m = _json.loads(zf.read(n).decode("utf-8"))
                        m["version"] = 99
                        out.writestr(n, _json.dumps(m, ensure_ascii=False))
                    else:
                        out.writestr(n, zf.read(n))
            buf.seek(0)
            files = _upload_all(zipfile.ZipFile(buf))
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "未来版本副本", "mode": "create"}, files=files)
        assert r2.status_code == 400, r2.text
        assert "版本" in r2.json()["detail"]

    async def test_manifest_role_filenames_are_honored(self, client, db_session):
        """roles 映射到自定义文件名也要能导入（manifest 是唯一真相，不硬编码中文名）。"""
        raw, key = await _key(db_session, "rt_j1")
        std_id = await _seed_rich_standard(db_session, key, "自定义角色名")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as out:
                for n in zf.namelist():
                    m = None
                    if n == _CSV_MANIFEST_NAME:
                        m = _json.loads(zf.read(n).decode("utf-8"))
                        m["roles"] = {k: f"role-{k}.csv" for k in _CSV_ROLES}
                    if m is not None:
                        out.writestr(n, _json.dumps(m, ensure_ascii=False))
                    elif n in _CSV_ROLES.values():
                        role = next(k for k, v in _CSV_ROLES.items() if v == n)
                        out.writestr(f"role-{role}.csv", zf.read(n))
                    else:
                        out.writestr(n, zf.read(n))
            buf.seek(0)
            files = _upload_all(zipfile.ZipFile(buf))
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "自定义角色名副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert (await _snapshot(db_session, r2.json()["standard_id"]))["tables"] == \
            (await _snapshot(db_session, std_id))["tables"]

    async def test_archive_undeclared_extra_file_warns_and_ignored(self, client, db_session):
        raw, key = await _key(db_session, "rt_j2")
        std_id = await _seed_rich_standard(db_session, key, "多余文件")
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as out:
                for n in zf.namelist():
                    out.writestr(n, zf.read(n))
                out.writestr("随手放的一个表.csv", "﻿a,b\n".encode("utf-8"))
            buf.seek(0)
            files = _upload_all(zipfile.ZipFile(buf))
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "多余文件副本", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert r2.json()["tables_count"] == 2, "未声明的文件不得被当成表导入"
        assert any("随手放的一个表" in w for w in r2.json()["warnings"]), r2.json()["warnings"]

    async def test_archive_upsert_rebuilds_from_manifest(self, client, db_session):
        """mode=upsert 走 manifest 路径同样是「先删后建」，不留旧绑定残留。"""
        raw, key = await _key(db_session, "rt_j3")
        std_id = await _seed_rich_standard(db_session, key, "源标准")
        before = await _snapshot(db_session, std_id)
        r1 = await client.post(f"/api/v1/data-standards-v2/{std_id}/export?format=csv",
                               headers={"X-API-Key": raw})
        with zipfile.ZipFile(io.BytesIO(r1.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "源标准", "mode": "upsert"}, files=files)
        assert r2.status_code == 201, r2.text
        assert r2.json()["updated"] is True
        assert r2.json()["standard_id"] == std_id
        assert await _snapshot(db_session, std_id) == before

    async def test_template_csv_is_the_same_six_roles(self, client):
        """模板与导出同构：填好模板即可导入，且带样例行可跑通。"""
        r = await client.get("/api/v1/data-standards-v2/template/csv")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            m = _json.loads(zf.read(_CSV_MANIFEST_NAME).decode("utf-8"))
            assert m["roles"] == _CSV_ROLES
            for name in _CSV_ROLES.values():
                assert len(_csv_rows(zf.read(name))) >= 2, f"{name} 缺样例行"
            tables = _csv_rows(zf.read(_CSV_ROLES["tables"]))
        assert tables[0] == ["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"]


class TestCsvTemplateRoundTrip:
    async def test_template_imports_cleanly(self, client, db_session):
        """模板本身必须可被自家导入器接受（否则模板就是骗人的）。"""
        raw, key = await _key(db_session, "rt_j4")
        r = await client.get("/api/v1/data-standards-v2/template/csv")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            files = _upload_all(zf)
        r2 = await client.post("/api/v1/data-standards-v2/import/csv",
                               headers={"X-API-Key": raw},
                               data={"name": "模板往返", "mode": "create"}, files=files)
        assert r2.status_code == 201, r2.text
        assert r2.json()["tables_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. 归档的降级与防御路径
#
# 归档是用户会在 Excel 里手改的文件，「不完整/不自洽」是常态而非异常。
# 契约：必需角色缺席 → 400；其余一切 → 尽力导入 + 明确告警，绝不静默丢数据，
# 也不因为一个坏行把整份归档打成 5xx。
# ═══════════════════════════════════════════════════════════════════════════

class TestArchiveDegradation:

    async def test_declared_role_without_file_warns_and_treats_as_empty(self, client, db_session):
        """manifest 声明了字典角色但文件没上传 → 告警 + 当空字典导入。"""
        raw, key = await _key(db_session, "rt_k1")
        std_id = await _seed_rich_standard(db_session, key, "缺字典文件")
        patched = _repack(await _export_csv(client, raw, std_id),
                          drop=(_CSV_ROLES["dictionaries"], _CSV_ROLES["dictionary_items"]))
        r = await _import_csv(client, raw, _files_from_zip(patched), "缺字典文件副本")
        assert r.status_code == 201, r.text
        assert r.json()["tables_count"] == 2
        assert any("dictionaries" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_missing_layer_groups_synthesizes_one_group(self, client, db_session):
        """缺 layer_groups 角色 → 合成单个默认层组，所有表都进去（不是每表一个组）。"""
        raw, key = await _key(db_session, "rt_k2")
        std_id = await _seed_rich_standard(db_session, key, "缺层组角色")
        patched = _repack(await _export_csv(client, raw, std_id),
                          drop=(_CSV_ROLES["layer_groups"],))
        r = await _import_csv(client, raw, _files_from_zip(patched), "缺层组角色副本")
        assert r.status_code == 201, r.text
        snap = await _snapshot(db_session, r.json()["standard_id"])
        assert len(snap["groups"]) == 1, snap["groups"]
        # 合成层组没有 group_code，所以快照里表所属层显示为空 —— 用层组 id 确认归属
        only_group_id = (await db_session.execute(
            select(StandardLayerGroup.id).where(
                StandardLayerGroup.standard_id == r.json()["standard_id"]))).scalars().one()
        owners = (await db_session.execute(
            select(StandardTable.layer_id).where(
                StandardTable.standard_id == r.json()["standard_id"]))).scalars().all()
        assert set(owners) == {only_group_id}, owners
        assert any("layer_groups" in w for w in r.json()["warnings"]), r.json()["warnings"]
        # 种子数据刻意含两张同名「路面」表：合进单层组后必然撞 uq_st_layer_name，
        # 第二张按「首次出现生效」跳过（而不是把整份归档打成 409）。
        assert r.json()["tables_count"] == 1, r.json()
        assert any("同一层组下重复" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_unknown_layer_code_lands_in_fallback_group(self, client, db_session):
        """表引用的层组编码没声明 → 落兜底层组并告警，表本身不丢。"""
        raw, key = await _key(db_session, "rt_k3")
        std_id = await _seed_rich_standard(db_session, key, "层组编码对不上")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["tables"]))
        rows[1][0] = "NOPE"
        patched = _repack(raw_csv, rows={"tables": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "层组编码对不上副本")
        assert r.status_code == 201, r.text
        assert r.json()["tables_count"] == 2
        assert any("NOPE" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_unknown_parent_code_treated_as_top_level(self, client, db_session):
        """父级编码没声明 → 按顶级层组处理并告警（不是静默丢层级）。"""
        raw, key = await _key(db_session, "rt_k4")
        std_id = await _seed_rich_standard(db_session, key, "父级编码缺失")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["layer_groups"]))
        rows[2][2] = "NO_PARENT"
        patched = _repack(raw_csv, rows={"layer_groups": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "父级编码缺失副本")
        assert r.status_code == 201, r.text
        child = next(g for g in (await _snapshot(db_session, r.json()["standard_id"]))["groups"]
                     if g[1] == "RD_SUB")
        assert child[2] == "", f"父级应为空（顶级），实际 {child[2]!r}"
        assert any("NO_PARENT" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_field_with_unknown_table_code_warns_and_skips(self, client, db_session):
        raw, key = await _key(db_session, "rt_k5")
        std_id = await _seed_rich_standard(db_session, key, "字段表编码对不上")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["fields"]))
        rows[1][0] = "NO_TABLE"
        patched = _repack(raw_csv, rows={"fields": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "字段表编码对不上副本")
        assert r.status_code == 201, r.text
        assert r.json()["fields_count"] == 1, r.json()
        assert any("NO_TABLE" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_item_with_unknown_dict_code_warns_and_skips(self, client, db_session):
        raw, key = await _key(db_session, "rt_k6")
        std_id = await _seed_rich_standard(db_session, key, "字典项编码对不上")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["dictionary_items"]))
        rows[1][0] = "NO_DICT"
        patched = _repack(raw_csv, rows={"dictionary_items": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "字典项编码对不上副本")
        assert r.status_code == 201, r.text
        assert any("NO_DICT" in w for w in r.json()["warnings"]), r.json()["warnings"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. 重复编码 —— 「首次出现生效」
#
# 编码是连接键，重复会让引用指向不确定。DB 上有 uq_st_layer_code /
# uq_slg_std_parent_code 等约束，所以重复行必须**跳过**而不是插进去撞约束。
# ═══════════════════════════════════════════════════════════════════════════

class TestArchiveDuplicateCodes:

    async def test_duplicate_layer_group_code_skipped(self, client, db_session):
        """重复层组编码必须跳过。

        样本刻意用「编码相同、名称不同」：若复制整行，name 检查会兜住，
        去重逻辑的回归就不会被本用例发现（名称去重是另一条独立分支）。
        另外种子名不能包含「层组编码重复」，否则导入必发的标准名称冲突告警
        会以子串方式满足断言，形成假阳性。
        """
        raw, key = await _key(db_session, "rt_l1")
        std_id = await _seed_rich_standard(db_session, key, "组码撞车")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["layer_groups"]))
        rows.append(["另一个名字", rows[1][1], "", "同码不同名"])   # 编码 RD 重复，名称不同
        patched = _repack(raw_csv, rows={"layer_groups": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "组码撞车副本")
        assert r.status_code == 201, r.text
        assert len((await _snapshot(db_session, r.json()["standard_id"]))["groups"]) == 2
        assert any("层组编码重复" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_duplicate_table_name_in_same_layer_skipped(self, client, db_session):
        """同层组同名（编码不同）也要跳过：DB 有 uq_st_layer_name。"""
        raw, key = await _key(db_session, "rt_l2")
        std_id = await _seed_rich_standard(db_session, key, "表名重复")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["tables"]))
        clone = list(rows[1])
        clone[2] = "SURF_OTHER"                  # 编码不同 → 只剩「同名」冲突
        rows.append(clone)
        patched = _repack(raw_csv, rows={"tables": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "表名重复副本")
        assert r.status_code == 201, r.text
        assert r.json()["tables_count"] == 2, r.json()
        assert any("同一层组下重复" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_duplicate_dict_code_warns_but_keeps_both_rows(self, client, db_session):
        """字典无唯一约束：重复行保留（不丢用户数据），但绑定只认首次出现。

        种子名不能包含「字典编码重复」：导入必发的标准名称冲突告警会带出这个名字，
        以子串方式满足下面的 warnings 断言，形成假阳性。
        """
        raw, key = await _key(db_session, "rt_l3")
        std_id = await _seed_rich_standard(db_session, key, "两本材料表")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["dictionaries"]))
        rows.append(["MAT", "另一个面层材料", "standard", "同码不同名"])
        patched = _repack(raw_csv, rows={"dictionaries": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "两本材料表副本")
        assert r.status_code == 201, r.text
        assert any("字典编码重复" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_dict_row_without_code_skips(self, client, db_session):
        raw, key = await _key(db_session, "rt_l4")
        std_id = await _seed_rich_standard(db_session, key, "字典缺编码")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["dictionaries"]))
        rows.append(["", "无名字典", "standard", "没有编码"])
        patched = _repack(raw_csv, rows={"dictionaries": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "字典缺编码副本")
        assert r.status_code == 201, r.text
        assert any("缺少字典编码" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_duplicate_layer_group_name_skipped(self, client, db_session):
        """同父级下同名层组（编码不同）也要跳过：DB 有 uq_slg_std_parent_name。"""
        raw, key = await _key(db_session, "rt_l5")
        std_id = await _seed_rich_standard(db_session, key, "层组名重复")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["layer_groups"]))
        rows.append(["道路", "RD_OTHER", "", "同名的另一个层组"])   # 同名、顶级、编码不同
        patched = _repack(raw_csv, rows={"layer_groups": rows})
        r = await _import_csv(client, raw, _files_from_zip(patched), "层组名重复副本")
        assert r.status_code == 201, r.text
        assert len((await _snapshot(db_session, r.json()["standard_id"]))["groups"]) == 2
        assert any("同一父级下重复" in w for w in r.json()["warnings"]), r.json()["warnings"]

    async def test_optional_role_absent_from_roles_degrades_silently(self, client, db_session):
        """可选角色连声明都没有 → 静默按空处理（不告警，因为用户本来就没打算给）。

        区分两种「没有」：声明了却没上传 = 上传出错，要告警；压根没声明 = 合法精简归档。
        """
        raw, key = await _key(db_session, "rt_l8b")
        std_id = await _seed_rich_standard(db_session, key, "精简归档")

        def _trim_roles(m):
            m["roles"] = {k: v for k, v in m["roles"].items() if k in ("tables", "fields")}

        patched = _repack(await _export_csv(client, raw, std_id), manifest=_trim_roles)
        r = await _import_csv(client, raw, _files_from_zip(patched), "精简归档副本")
        assert r.status_code == 201, r.text
        # 种子含两张同名「路面」表；layer_groups 未声明 → 合进单个合成层组 → 第二张按名跳过
        assert r.json()["tables_count"] == 1, r.json()
        assert not any("未提供角色" in w for w in r.json()["warnings"]), r.json()["warnings"]

    @pytest.mark.parametrize("manifest", [
        {"format": "vcs-standard-csv", "version": 1},                       # 无 roles
        {"format": "vcs-standard-csv", "version": 1, "roles": {}},          # roles 为空
        {"format": "vcs-standard-csv", "version": 1, "roles": "层组.csv"},   # roles 不是 dict
        {"format": "vcs-standard-csv", "version": 1, "roles": {"tables": "数据表.csv"}},  # 缺 fields
    ], ids=["无roles", "roles为空", "roles非dict", "缺fields"])
    async def test_manifest_without_required_roles_is_400(self, client, db_session, manifest):
        """必需角色未声明/声明不完整 → 400，提示要指名是哪个角色。"""
        raw, key = await _key(db_session, "rt_l8")
        files = [("files", (_CSV_MANIFEST_NAME, _json.dumps(manifest).encode(), "application/json"))]
        r = await _import_csv(client, raw, files, f"无必需角色{len(manifest)}")
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert "tables" in detail or "fields" in detail, detail

    async def test_group_with_blank_code_is_usable(self, client, db_session):
        """层组编码留空也要能导入：表按「第 0 个单元格」回落到兜底组，不丢表。"""
        raw, key = await _key(db_session, "rt_l6")
        std_id = await _seed_rich_standard(db_session, key, "层组编码留空")
        raw_csv = await _export_csv(client, raw, std_id)
        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["layer_groups"]))
        rows[1][1] = ""                                 # 顶层组的编码置空
        tables = _csv_rows(zipfile.ZipFile(io.BytesIO(raw_csv)).read(_CSV_ROLES["tables"]))
        patched = _repack(raw_csv, rows={"layer_groups": rows, "tables": tables})
        r = await _import_csv(client, raw, _files_from_zip(patched), "层组编码留空副本")
        assert r.status_code == 201, r.text
        # 顶层组编码为空 → 表的「RD」引用解析不到 → 走兜底组，但表本身全部保留
        assert r.json()["tables_count"] == 2, r.json()
        assert r.json()["fields_count"] == 2, r.json()

    async def test_blank_codes_do_not_pollute_diagnostics(self, client, db_session):
        """空编码不该被当成「重复」：只统计非空编码。"""
        raw, key = await _key(db_session, "rt_l7")
        std_id = await _seed_rich_standard(db_session, key, "空编码诊断")
        (await db_session.execute(
            select(StandardTable).where(StandardTable.standard_id == std_id))).scalars().all()
        for t in (await db_session.execute(
                select(StandardTable).where(StandardTable.standard_id == std_id))).scalars().all():
            t.table_code = None                          # 全部表都无编码
        await db_session.commit()
        raw_csv = await _export_csv(client, raw, std_id)
        with zipfile.ZipFile(io.BytesIO(raw_csv)) as zf:
            manifest = _json.loads(zf.read(_CSV_MANIFEST_NAME).decode("utf-8"))
        assert manifest["diagnostics"] == [], manifest["diagnostics"]


# ═══════════════════════════════════════════════════════════════════════════
# 8. 畸形归档 —— manifest 坏了就是 400，不能猜
# ═══════════════════════════════════════════════════════════════════════════

class TestArchiveMalformedManifest:

    async def test_unparsable_manifest_is_400(self, client, db_session):
        raw, key = await _key(db_session, "rt_m1")
        files = [("files", (_CSV_MANIFEST_NAME, b"{not json", "application/json"))]
        r = await _import_csv(client, raw, files, "坏JSON")
        assert r.status_code == 400, r.text
        assert "JSON" in r.json()["detail"]

    async def test_wrong_format_is_400(self, client, db_session):
        raw, key = await _key(db_session, "rt_m2")
        files = [("files", (_CSV_MANIFEST_NAME,
                            _json.dumps({"format": "something-else", "version": 1}).encode(),
                            "application/json"))]
        r = await _import_csv(client, raw, files, "错格式")
        assert r.status_code == 400, r.text
        assert "format" in r.json()["detail"]

    @pytest.mark.parametrize("version", ["1", 1.0, None, True, [1]])
    async def test_non_integer_version_is_400(self, client, db_session, version):
        raw, key = await _key(db_session, "rt_m3")
        files = [("files", (_CSV_MANIFEST_NAME,
                            _json.dumps({"format": "vcs-standard-csv", "version": version}).encode(),
                            "application/json"))]
        r = await _import_csv(client, raw, files, f"版本非整数{version!r}")
        assert r.status_code == 400, r.text
        assert "version" in r.json()["detail"]

    @pytest.mark.parametrize("payload", [[], "vcs-standard-csv", 42, None])
    async def test_non_object_manifest_is_400(self, client, db_session, payload):
        """manifest 不是 JSON 对象 → 400（不能当成 legacy 静默按每文件一表处理）。"""
        raw, key = await _key(db_session, "rt_m3b")
        files = [("files", (_CSV_MANIFEST_NAME, _json.dumps(payload).encode(), "application/json"))]
        r = await _import_csv(client, raw, files, "manifest非对象")
        assert r.status_code == 400, r.text
        assert "format" in r.json()["detail"]

    async def test_role_file_with_invalid_utf8_is_400(self, client, db_session):
        """角色文件坏了必须 400 —— 静默按空导入等于删数据。"""
        raw, key = await _key(db_session, "rt_m4")
        std_id = await _seed_rich_standard(db_session, key, "非UTF8")
        patched = _repack(await _export_csv(client, raw, std_id),
                          extra={_CSV_ROLES["tables"]: b"\xff\xfe\x00bad"})
        r = await _import_csv(client, raw, _files_from_zip(patched), "非UTF8副本")
        assert r.status_code == 400, r.text
        assert "UTF-8" in r.json()["detail"]

    async def test_short_and_non_numeric_cells_are_tolerated(self, client, db_session):
        """列数不齐/整数列填了非数字 → 当空处理，不整份归档失败。"""
        raw, key = await _key(db_session, "rt_m5")
        std_id = await _seed_rich_standard(db_session, key, "单元格容错")
        raw_csv = await _export_csv(client, raw, std_id)
        with zipfile.ZipFile(io.BytesIO(raw_csv)) as zf:
            tables = _csv_rows(zf.read(_CSV_ROLES["tables"]))
            fields = _csv_rows(zf.read(_CSV_ROLES["fields"]))
        fields[1] = ["SURF"]                       # 只有 1 列 → 后 10 列全部越界
        fields[2] = list(fields[2])
        fields[2][4] = "不是数字"                   # 长度列非整数
        patched = _repack(raw_csv, rows={"tables": tables, "fields": fields})
        r = await _import_csv(client, raw, _files_from_zip(patched), "单元格容错副本")
        assert r.status_code == 201, r.text
        assert r.json()["fields_count"] == 2, r.json()
        lengths = (await db_session.execute(
            select(StandardField.length).where(
                StandardField.standard_id == r.json()["standard_id"]))).scalars().all()
        assert None in list(lengths), list(lengths)

    # 「无文件名上传」的分支无法经 HTTP 触发（multipart 会先被 FastAPI 判 422），
    # 已在 tests/test_csv_upload_edge_cases.py 直接打 legacy 导入函数覆盖。


class TestArchiveStandardRole:
    """`标准信息.csv` 必须被真正消费，否则「无损往返」只是半句承诺。

    导出把 `DataStandard.description/category` 写进了 `标准信息.csv`；
    导入若不读它，往返后这两个字段就静默变空 —— 结构无损而元信息有损，
    属于最难被发现的一类数据丢失。
    """

    async def test_standard_role_restores_description_and_category(self, client, db_session):
        raw, key = await _key(db_session, "rt_meta1")
        std_id = await _seed_rich_standard(db_session, key, "元信息往返")
        std = await db_session.get(DataStandard, std_id)
        std.description = "路面病害检查（往返校验）"
        std.category = "municipal"
        await db_session.commit()

        r = await _import_csv(client, raw,
                              _files_from_zip(await _export_csv(client, raw, std_id)),
                              "元信息往返副本")
        assert r.status_code == 201, r.text
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.description == "路面病害检查（往返校验）"
        assert imported.category == "municipal"

    async def test_form_values_override_standard_role(self, client, db_session):
        """表单显式传了值 → 以表单为准（`标准信息.csv` 只是默认值来源）。

        `description` 传 None 之外的值即视为显式；`category` 的表单默认值是
        `'custom'`，无法区分「显式 custom」与「没传」，沿用本模块既有约定：
        非 `'custom'` 视为显式指定，否则回落到归档值。
        """
        raw, key = await _key(db_session, "rt_meta2")
        std_id = await _seed_rich_standard(db_session, key, "表单优先")
        std = await db_session.get(DataStandard, std_id)
        std.description, std.category = "归档里的描述", "archive"
        await db_session.commit()

        r = await _import_csv(client, raw,
                              _files_from_zip(await _export_csv(client, raw, std_id)),
                              "表单优先副本", description="表单里的描述", category="municipal")
        assert r.status_code == 201, r.text
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.description == "表单里的描述"
        assert imported.category == "municipal"

    async def test_category_default_custom_falls_back_to_archive(self, client, db_session):
        """表单没传 category（仍是默认 `'custom'`）→ 用归档里的分类。"""
        raw, key = await _key(db_session, "rt_meta2b")
        std_id = await _seed_rich_standard(db_session, key, "分类回落")
        std = await db_session.get(DataStandard, std_id)
        std.category = "archive"
        await db_session.commit()

        r = await _import_csv(client, raw,
                              _files_from_zip(await _export_csv(client, raw, std_id)),
                              "分类回落副本")
        assert r.status_code == 201, r.text
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.category == "archive"

    async def test_standard_role_name_conflict_warns(self, client, db_session):
        """`标准信息.csv` 里的名称与表单名称不一致 → 告警，但不静默改名。"""
        raw, key = await _key(db_session, "rt_meta3")
        std_id = await _seed_rich_standard(db_session, key, "原名")
        patched = _repack(await _export_csv(client, raw, std_id), rows={"standard": [
            ["标准名称", "标准描述", "分类"], ["另一个名字", "描述", "custom"]]})

        r = await _import_csv(client, raw, _files_from_zip(patched), "导入时的名字")
        assert r.status_code == 201, r.text
        assert any("另一个名字" in w and "导入时的名字" in w for w in r.json()["warnings"]), \
            r.json()["warnings"]
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.name == "导入时的名字", "名称以表单为准，归档不得静默改名"

    async def test_standard_role_absent_keeps_form_only(self, client, db_session):
        """归档没有 `标准信息.csv` → 完全按表单来，不报错也不告警。"""
        raw, key = await _key(db_session, "rt_meta4")
        std_id = await _seed_rich_standard(db_session, key, "无标准信息")

        def _drop(m):
            m["roles"].pop("standard", None)

        patched = _repack(await _export_csv(client, raw, std_id), manifest=_drop)
        r = await _import_csv(client, raw, _files_from_zip(patched), "无标准信息副本",
                              description="只有表单描述")
        assert r.status_code == 201, r.text
        assert not any("standard" in w for w in r.json()["warnings"]), r.json()["warnings"]
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.description == "只有表单描述"
        assert imported.category == "custom"

    async def test_update_mode_uses_standard_role_metadata(self, client, db_session):
        """update 模式同样消费 `标准信息.csv` —— 否则「改归档再导回」改不动元信息。"""
        raw, key = await _key(db_session, "rt_meta5")
        std_id = await _seed_rich_standard(db_session, key, "更新元信息")
        patched = _repack(await _export_csv(client, raw, std_id), rows={"standard": [
            ["标准名称", "标准描述", "分类"], ["更新元信息", "改过的描述", "national"]]})

        r = await _import_csv(client, raw, _files_from_zip(patched), "更新元信息", mode="update")
        assert r.status_code == 201, r.text
        std = await db_session.get(DataStandard, std_id)
        assert std.description == "改过的描述"
        assert std.category == "national"

    async def test_standard_role_only_first_row_is_used(self, client, db_session):
        """`标准信息.csv` 是单行语义；多出来的行告警而不是静默吞掉。

        种子名不能含「标准信息」：导入必发的标准名称冲突告警会带出它，
        以子串方式满足下面的 warnings 断言，形成假阳性。
        """
        raw, key = await _key(db_session, "rt_meta6")
        std_id = await _seed_rich_standard(db_session, key, "单值角色")
        patched = _repack(await _export_csv(client, raw, std_id), rows={"standard": [
            ["标准名称", "标准描述", "分类"],
            ["单值角色", "第一行描述", "custom"],
            ["第二行", "不该被用", "custom"]]})

        r = await _import_csv(client, raw, _files_from_zip(patched), "单值角色副本")
        assert r.status_code == 201, r.text
        assert any("标准信息" in w and "行" in w for w in r.json()["warnings"]), r.json()["warnings"]
        imported = await db_session.get(DataStandard, r.json()["standard_id"])
        assert imported.description == "第一行描述"

    async def test_standard_role_is_exported_with_header_row(self, client, db_session):
        """`标准信息.csv` 必须带表头行，且列数与 `_CSV_HEADERS["standard"]` 一致。

        回归守卫：曾导出成「首格是表头文字、后面直接跟数据」的 4 列形态，
        导入侧会把它当数据行 → 名称/描述/分类整体错位一列。
        """
        raw, key = await _key(db_session, "rt_meta7")
        std_id = await _seed_rich_standard(db_session, key, "表头守卫")
        std = await db_session.get(DataStandard, std_id)
        std.description = "描述不该被当成分类"
        await db_session.commit()

        rows = _csv_rows(zipfile.ZipFile(io.BytesIO(
            await _export_csv(client, raw, std_id))).read(_CSV_ROLES["standard"]))
        assert rows[0] == ["标准名称", "标准描述", "分类"], rows
        assert all(len(r) == 3 for r in rows), f"列数不齐: {rows}"
        assert rows[1] == ["表头守卫", "描述不该被当成分类", "custom"], rows


# ═══════════════════════════════════════════════════════════════════════════
# 5. 质检联动：绑定进来后规则能生成（本次闭合的真正目的）
# ═══════════════════════════════════════════════════════════════════════════

class TestLinksFeedQualityRules:

    async def test_imported_links_reach_rule_generator(self, client, db_session):
        """导入的绑定必须能被 rule_generator 读到并生成 VALUE_IN_SET。

        绑定长期不可导入的实际危害就在这里（值域质检整链失效），
        所以不只断言"有 link 行"，还要断言"下游真的用得上"。
        """
        from app.services.quality.rule_generator import generate_rules_for_standard

        raw, key = await _key(db_session, "rt_q")
        r = await client.post(
            "/api/v1/data-standards-v2/import/excel",
            headers={"X-API-Key": raw},
            data={"name": "质检联动", "mode": "create"},
            files={"file": ("q.xlsx", _v2_xlsx_with_dicts("质检联动", "MAT"), XLSX_CT)},
        )
        assert r.status_code == 201
        std_id = r.json()["standard_id"]

        rules = await generate_rules_for_standard(std_id, key.id, db_session)
        value_in_set = [ru for ru in rules if ru.rule_type == "VALUE_IN_SET"]
        assert value_in_set, "绑定导入后未生成 VALUE_IN_SET 规则，值域质检仍会失效"
        assert value_in_set[0].params["allowed_values"] == ["A"]


class TestWarningAssertionsAreNotFooledByNames:
    """守卫：`any("<文案>" in w ...)` 不能被标准名称冲突告警顺手满足。

    导入时只要归档名与表单名不同，就必发一条
    「标准名称为「X」，与导入名称「Y」不一致」告警，其中带出 X 和 Y。
    若断言文案是 X/Y 的子串（如断言「重复」而种子名叫「导入侧重复」），
    这条无关告警就会让断言恒真 —— 变异测试里表现为「变异存活」，但根因是
    测试本身没在测想测的东西。

    这里做静态扫描，把这类隐患挡在 review 之前。
    """

    #: 导入必发的名称冲突告警格式（见 import_export._merge_standard_meta）
    _NAME_CONFLICT = "标准名称为「{name}」"

    def test_no_warning_assertion_is_a_substring_of_a_test_fixture_name(self):
        import re as _re
        from pathlib import Path as _Path

        src = _Path(__file__).read_text(encoding="utf-8")
        lines = src.splitlines()

        blocks, name, start = [], None, 0
        for i, ln in enumerate(lines):
            m = _re.match(r"\s*async def (test_\w+)", ln)
            if m:
                if name:
                    blocks.append((name, start, i))
                name, start = m.group(1), i
        if name:
            blocks.append((name, start, len(lines)))

        problems: list[str] = []
        for tname, s, e in blocks:
            body = "\n".join(lines[s:e])
            fixtures = (_re.findall(r'_seed_rich_standard\(db_session,\s*key,\s*"([^"]+)"', body)
                        + _re.findall(r'_import_csv\([^,]+,\s*[^,]+,\s*[^,]+,\s*"([^"]+)"', body))
            needles = (_re.findall(r'any\(\s*"([^"]+)"\s+in\s+w', body)
                       + _re.findall(r"any\(\s*'([^']+)'\s+in\s+w", body))
            for needle in needles:
                for fx in fixtures:
                    if needle and needle in fx:
                        problems.append(
                            f"{tname} (line {s + 1}): 断言文案 {needle!r} 是 fixture 名 "
                            f"{fx!r} 的子串，会被名称冲突告警 {self._NAME_CONFLICT.format(name=fx)} 满足")

        assert not problems, "warnings 断言存在子串假阳性：\n  " + "\n  ".join(problems)

# ═══════════════════════════════════════════════════════════════════════════
# 7. 整包 ZIP 回导（v2.22.3 §六·补七：归档回导缺口）
#
# 导出产出 ZIP，但端点此前只认拆开的散装 part —— 用户/前端把整个 ZIP 当
# 单个文件上传会被当「非 CSV 文件」跳过 → 静默空导入（201 + 0 表）。
# ═══════════════════════════════════════════════════════════════════════════

class TestImportAcceptsWholeZip:
    """整包 ZIP 自动解包：与拆包导入等价，坏包 400，散装打包仍走 legacy。"""

    async def test_zip_as_single_part_roundtrips(self, client, db_session):
        """整包 ZIP create 导入 ≡ 拆包导入（结构全等、无「跳过非 CSV」告警）。"""
        raw, key = await _key(db_session, "zip_r1")
        src = await _seed_rich_standard(db_session, key, "整包回导源")
        await db_session.commit()
        before = await _snapshot(db_session, src)
        zip_bytes = await _export_csv(client, raw, src)

        r = await _import_csv(
            client, raw,
            [("files", ("归档.zip", zip_bytes, "application/zip"))],
            "整包回导副本")
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        w = " ".join(body["warnings"])
        assert "跳过非 CSV 文件" not in w, body["warnings"]
        after = await _snapshot(db_session, body["standard_id"])
        assert after == before, "整包 ZIP 导入应与拆包导入结构全等"

    async def test_zip_single_part_upsert_has_real_counts(self, client, db_session):
        """upsert 整包 ZIP 必须走归档路径出真实计数（修复前空导入恒 0 表）。"""
        raw, key = await _key(db_session, "zip_r2")
        src = await _seed_rich_standard(db_session, key, "整包更新源")
        await db_session.commit()
        zip_bytes = await _export_csv(client, raw, src)

        r = await _import_csv(
            client, raw,
            [("files", ("a.zip", zip_bytes, "application/zip"))],
            "整包更新源", mode="upsert")
        assert r.status_code == 201, r.text[:400]
        body = r.json()
        assert body["updated"] is True
        assert body["tables_count"] > 0, (
            f"整包 ZIP 应解包走归档路径，实际 {body}")

    async def test_corrupt_zip_is_400(self, client, db_session):
        """PK 魔数但已损坏 → 400，不能静默跳过变空导入（201+0 表）。"""
        raw, _ = await _key(db_session, "zip_r3")
        r = await _import_csv(
            client, raw,
            [("files", ("坏包.zip", b"PK\x03\x04broken-not-a-zip", "application/zip"))],
            "坏包标准")
        assert r.status_code == 400, f"期望 400，实际 {r.status_code}: {r.text[:200]}"
        assert "ZIP" in r.json()["detail"], r.text

    async def test_zip_of_plain_csvs_uses_legacy_path(self, client, db_session):
        """用户自己打包的散装 CSV（无 manifest）→ 解包后仍按 legacy 每文件一表。

        同时钉死解包细节：子目录条目按 basename 展平、目录条目与 `.` 开头的
        系统垃圾（.DS_Store）直接跳过，不得变成表名或告警。
        """
        raw, _ = await _key(db_session, "zip_r4")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("表A.csv", "地名,面积,用途\nx,y,z\n")
            z.writestr("资料/", "")                       # 目录条目 → 跳过
            z.writestr("资料/表B.csv", "路名,等级\na,3\n")  # 嵌套路径 → 展平
            z.writestr(".DS_Store", b"junk")              # 系统垃圾 → 跳过
        r = await _import_csv(
            client, raw,
            [("files", ("散装打包.zip", buf.getvalue(), "application/zip"))],
            "散装表组")
        assert r.status_code == 201, r.text[:300]
        body = r.json()
        assert body["tables_count"] == 2, body
        assert body["fields_count"] == 5, body
        assert ".DS_Store" not in " ".join(body["warnings"]), body["warnings"]

    async def test_zip_detected_by_magic_without_zip_suffix(self, client, db_session):
        """文件名不带 .zip 但内容是 ZIP → 魔数识别照样解包（前端可改名不改内容）。"""
        raw, key = await _key(db_session, "zip_magic")
        src = await _seed_rich_standard(db_session, key, "魔数识别源")
        await db_session.commit()
        zip_bytes = await _export_csv(client, raw, src)

        r = await _import_csv(
            client, raw,
            [("files", ("归档文件", zip_bytes, "application/octet-stream"))],
            "魔数识别副本")
        assert r.status_code == 201, r.text[:400]
        assert r.json()["tables_count"] > 0, r.json()

    async def test_zip_and_loose_part_collision_warns_keeps_first(self, client, db_session):
        """ZIP 内条目与外层散装 part 同名 → 保留先者 + 告警，归档结构不受污染。"""
        raw, key = await _key(db_session, "zip_r5")
        src = await _seed_rich_standard(db_session, key, "整包合并源")
        await db_session.commit()
        before = await _snapshot(db_session, src)
        zip_bytes = await _export_csv(client, raw, src)

        zin = zipfile.ZipFile(io.BytesIO(zip_bytes))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for info in zin.infolist():
                z.writestr(info, zin.read(info.filename))
            z.writestr("附加.csv", "x,y\n1,2\n")
        files = [
            ("files", ("归档与散装.zip", out.getvalue(), "application/zip")),
            ("files", ("附加.csv", b"q,w\n9,9\n", "text/csv")),
        ]
        r = await _import_csv(client, raw, files, "整包合并副本")
        assert r.status_code == 201, r.text[:400]
        w = " ".join(r.json()["warnings"])
        assert "重名" in w, r.json()["warnings"][:8]
        after = await _snapshot(db_session, r.json()["standard_id"])
        assert after == before, "重名合并不得破坏归档角色文件的导入"
