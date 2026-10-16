"""P6e-2: data_standard_parser.py + structure_analyzer.py + 标准模型 → 100%。

- data_standard_parser: v1 空 sheet/空行/缺字段名/类型告警/超长行截断/
  nullable 与 allowed_values 直测；v2 全 sheet 模板（表头跳过/类型告警/
  空字典编码/load_workbook 异常）
- structure_analyzer: 标准不存在 → ValueError
- 模型: StandardFieldDictLink.dict_name / StandardLayerGroup 层级链 /
  StandardDictionary.is_global
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from unittest.mock import PropertyMock

import openpyxl
import pytest

from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.services import data_standard_parser as dsp
from app.services.structure_analyzer import auto_create_standard_from_schema


def _save_workbook(path: Path, sheets: dict[str, list[list]]):
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def _save_empty_workbook(path: Path) -> Path:
    wb = openpyxl.Workbook()
    wb.save(path)
    return path


# ── data_standard_parser v1 ───────────────────────────────────────────────────

class TestParseStandardExcelV1:

    def test_empty_sheet_skipped(self, tmp_path):
        """55：空 sheet（无任何行）→ continue。"""
        p = _save_empty_workbook(tmp_path / "e.xlsx")
        r = dsp.parse_standard_excel(str(p))
        assert r.sheets == [] and r.warnings == []

    def test_blank_row_skipped(self, tmp_path):
        """69-70：跳过全空行。"""
        p = _save_workbook(tmp_path / "b.xlsx", {
            "字段": [["字段名", "类型"], [None, None], ["a", "string"]],
        })
        r = dsp.parse_standard_excel(str(p))
        assert [f.field_name for f in r.sheets[0].fields] == ["a"]

    def test_empty_field_name_skipped(self, tmp_path):
        """73-74：字段名为空继续。"""
        p = _save_workbook(tmp_path / "n.xlsx", {
            "Sheet1": [["字段名", "类型"], [None, "string"], ["b", "integer"]],
        })
        r = dsp.parse_standard_excel(str(p))
        assert [f.field_name for f in r.sheets[0].fields] == ["b"]

    def test_unknown_type_warns(self, tmp_path):
        """77-79：类型不可识别 → warning + string 兜底。"""
        p = _save_workbook(tmp_path / "t.xlsx", {
            "Sheet1": [["字段名", "类型"], ["x", "blob"]],
        })
        r = dsp.parse_standard_excel(str(p))
        assert r.warnings[0].startswith("字段 'x' 类型 'blob'")
        assert r.sheets[0].fields[0].field_type == "string"

    def test_header_none_cell(self, tmp_path):
        """115-116：表头存在 None 单元格 → continue。"""
        p = _save_workbook(tmp_path / "h.xlsx", {
            "Sheet1": [[None, "字段名", "类型"], ["a", "x", "string"]],
        })
        r = dsp.parse_standard_excel(str(p))
        assert r.sheets[0].fields[0].field_name == "x"

    def test_short_row_tail_columns_none(self, tmp_path):
        """130-131：行比表头短 → _get_cell 越界 → None。"""
        p = _save_workbook(tmp_path / "s.xlsx", {
            "Sheet1": [["字段名", "类型", "长度"], ["a"]],
        })
        r = dsp.parse_standard_excel(str(p))
        f = r.sheets[0].fields[0]
        assert f.field_name == "a" and f.length is None

    def test_parse_nullable_none(self):
        assert dsp._parse_nullable(None) is True

    def test_parse_allowed_values(self):
        assert dsp._parse_allowed_values(None) is None
        assert dsp._parse_allowed_values("  ") is None
        assert dsp._parse_allowed_values("a，b、c, d") == ["a", "b", "c", "d"]
        assert dsp._parse_allowed_values(",,,") is None

    def test_full_field_row(self, tmp_path):
        p = _save_workbook(tmp_path / "f.xlsx", {
            "Sheet1": [["字段名", "类型", "长度", "是否可空", "允许值", "描述"],
                       ["code", "integer", 10, "否", "1,2", "d"]],
        })
        r = dsp.parse_standard_excel(str(p))
        f = r.sheets[0].fields[0]
        assert f.length == 10 and f.nullable is False
        assert f.allowed_values == ["1", "2"] and f.description == "d"
        assert f.ordering == 0


# ── data_standard_parser v2 ───────────────────────────────────────────────────

class TestParseStandardExcelV2:

    V2_SHEETS = {
        "标准信息": [["字段名", "值"],
                     ["标准名称", "测试标准"],
                     ["标准编码", "TS001"],
                     ["分类", "national"],
                     ["描述", "desc1"]],
        "层组定义": [["层组名", "层组编码", "上级编码", "描述"],
                     ["层组名", "group_name"],  # 表头行 → 288 跳过
                     ["地物层", "DW", "", "层1"],
                     ["道路层", "DL", "DW", "层2"]],
        "数据表定义": [["层组编码", "表名", "表编码", "类型", "几何类型", "描述"],
                       ["层组编码", "层组名", "group_code"],  # 301 跳过
                       ["DW", "道路表", "ROAD", "spatial", "LineString", "t1"]],
        "字段定义": [["表编码", "字段名", "别名", "类型", "长度", "精度", "必填", "主键", "描述"],
                    ["字段名", "field_name"],  # 317 跳过
                    ["ROAD", "name", "名称", "string", 20, 0, "是", "否", "n1"],
                    ["ROAD", "geom", "几何", "bogus", "", "", "", "", None]],  # 321-322
        "字典定义": [["字典编码", "字典名称", "适用范围", "项编码", "项名称", "描述"],
                     ["字典编码", "dict_code"],  # 344 跳过
                     [" ", "空白码字典"],        # 空格编码 → 保留 + 告警（见下）
                     ["SEX", "性别", "standard", "M", "男", "x"],
                     ["SEX", "性别", "standard", "F", "女", None]],
    }

    def test_v2_full_parse(self, tmp_path):
        p = _save_workbook(tmp_path / "v2.xlsx", dict(self.V2_SHEETS))
        r = dsp.parse_standard_excel_v2(str(p))
        assert r.recognized is True
        assert r.name == "测试标准" and r.code == "TS001"
        assert r.category == "national" and r.description == "desc1"
        assert [g.group_name for g in r.groups] == ["地物层", "道路层"]
        assert r.groups[1].parent_code == "DW"
        assert [t.table_name for t in r.tables] == ["道路表"]
        assert r.tables[0].geometry_type == "LineString"
        # 字段：bogus 类型告警 → string
        assert [f.field_name for f in r.fields] == ["name", "geom"]
        assert r.fields[1].data_type == "string"
        assert r.fields[0].is_required is True
        assert any("类型 'bogus'" in w for w in r.warnings)
        # 空白编码的字典不再被静默丢弃：实体保留，编码留空，仅告警。
        # 与 _create_v2_children / _create_json_children 的既有契约一致 ——
        # 那两处本就 `dict_code or None` 且只在有编码时注册字段绑定。
        assert [d.dict_name for d in r.dictionaries] == ["空白码字典", "性别"]
        assert r.dictionaries[0].dict_code == ""
        assert any("空白码字典" in w and "编码" in w for w in r.warnings), r.warnings
        sex = r.dictionaries[1]
        assert sex.dict_code == "SEX"
        assert sex.items == [("M", "男"), ("F", "女")]
        assert sex.scope == "standard"

    def test_v2_not_recognized(self, tmp_path):
        """263-265：无模板 sheet → recognized False。"""
        p = _save_workbook(tmp_path / "plain.xlsx", {"普通表": [["a", "b"]]})
        r = dsp.parse_standard_excel_v2(str(p))
        assert r.recognized is False and r.groups == []

    def test_v2_bad_file(self, tmp_path):
        """256-259：load_workbook 异常 → warning。"""
        p = tmp_path / "bad.xlsx"
        p.write_bytes(b"not an excel file")
        r = dsp.parse_standard_excel_v2(str(p))
        assert r.warnings[0].startswith("Excel 解析失败")
        assert r.recognized is False

    def test_v2_bool_int_helpers(self):
        assert dsp._to_bool("是") is True and dsp._to_bool("√") is True
        assert dsp._to_bool("否") is False and dsp._to_bool("") is False
        assert dsp._to_bool("weird") is False  # default
        assert dsp._to_bool(None, default=True) is True
        assert dsp._to_int("长度") is None and dsp._to_int("精度") is None
        assert dsp._to_int("12.5") == 12
        assert dsp._to_int("abc") is None and dsp._to_int(None) is None

    def test_v2_export_style_sheets(self, tmp_path):
        """导出命名（层组/数据表/字段/字典）也能解析。"""
        p = _save_workbook(tmp_path / "export.xlsx", {
            "标准信息": [["字段名", "值"], ["标准名称", "S"]],
            "层组": [["层组名", "层组编码"], ["G1", "g1"]],
            "数据表": [["层组编码", "表名", "表编码"], ["g1", "T1", "t1"]],
            "字段": [["表编码", "字段名", "别名", "类型"], ["t1", "f1", "F", "float"]],
            "字典": [["字典编码", "字典名称"], ["D1", "字典1"]],
        })
        r = dsp.parse_standard_excel_v2(str(p))
        assert r.recognized is True
        assert [g.group_name for g in r.groups] == ["G1"]
        assert [t.table_name for t in r.tables] == ["T1"]
        assert [f.field_name for f in r.fields] == ["f1"]
        assert r.dictionaries[0].dict_code == "D1"

    def test_rows_of_missing_sheet(self, tmp_path):
        """216-221：_rows_of 找不到 sheet → []。"""
        p = _save_workbook(tmp_path / "x.xlsx", {"A": [["1"]]})
        wb = openpyxl.load_workbook(p)
        assert dsp._rows_of(wb, "不存在") == []

    def test_v2_field_row_too_short(self, tmp_path):
        """316：len<2 → 字段行跳过。"""
        p = _save_workbook(tmp_path / "short.xlsx", {
            "字段定义": [["表编码", "字段名"], ["t1"]],
        })
        r = dsp.parse_standard_excel_v2(str(p))
        assert r.recognized is True and r.fields == []

    def test_v2_group_table_header_skip_when_row_empty(self, tmp_path):
        """287/300：row[0] 为 None 的表头行继续。"""
        p = _save_workbook(tmp_path / "h.xlsx", {
            "标准信息": [["字段名", "值"], ["标准名称", "S"]],
            "层组": [["层组名"], [None], ["G9", "g9"]],
        })
        r = dsp.parse_standard_excel_v2(str(p))
        assert [g.group_name for g in r.groups] == ["G9"]


# ── structure_analyzer ────────────────────────────────────────────────────────

class TestAutoCreateStandardFromSchema:

    async def test_standard_not_found_raises(self):
        """55-56：标准不存在 → ValueError。"""
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="数据标准 x 不存在"):
            await auto_create_standard_from_schema(
                db, "x", "层组", "表", [{"name": "a", "type": "string"}])
        db.get.assert_awaited_once()


# ── 标准模型 ──────────────────────────────────────────────────────────────────

class TestStandardFieldDictLink:

    def test_dict_name_loaded(self):
        """62：dictionary 已加载 → 返回名称。"""
        link = StandardFieldDictLink()
        link.dictionary = StandardDictionary(dict_name="性别字典")
        assert link.dict_name == "性别字典"

    def test_dict_name_unloaded(self):
        """63：dictionary 为 None → None。"""
        link = StandardFieldDictLink()
        link.dictionary = None
        assert link.dict_name is None

    def test_dict_name_lazy_load_error(self, monkeypatch):
        """63-64：关系访问抛异常（async lazy load 失败）→ 兜底 None。"""
        import app.models.standard_field_dict_link as mod

        link = StandardFieldDictLink(field_id="f1", dict_id="d1")
        monkeypatch.setattr(
            mod.StandardFieldDictLink, "dictionary",
            PropertyMock(side_effect=RuntimeError("lazy load failed")))
        assert link.dict_name is None
        monkeypatch.undo()


class TestStandardLayerGroup:

    def _chain(self):
        root = StandardLayerGroup(id="1", group_name="根", group_code="root",
                                  parent_id=None)
        mid = StandardLayerGroup(id="2", group_name="中", group_code="mid",
                                 parent_id="1")
        leaf = StandardLayerGroup(id="3", group_name="叶", group_code="leaf",
                                  parent_id="2")
        mid.parent = root
        leaf.parent = mid
        return root, mid, leaf

    def test_depth_chain(self):
        """87-92：多级 parent 链 depth。"""
        root, mid, leaf = self._chain()
        assert root.depth == 0
        assert mid.depth == 1
        assert leaf.depth == 2

    def test_is_root(self):
        """97：非顶层 is_root False。"""
        _, mid, _ = self._chain()
        assert mid.is_root is False

    def test_get_path_chain(self):
        """101-106：从根到当前节点路径。"""
        root, mid, leaf = self._chain()
        assert root.get_path() == ["根"]
        assert mid.get_path() == ["根", "中"]
        assert leaf.get_path() == ["根", "中", "叶"]

    def test_table_count(self):
        from app.models.standard_layer_group_table_link import (
            StandardLayerGroupTableLink,
        )
        from app.models.standard_table import StandardTable

        g = StandardLayerGroup(id="9", group_name="G", parent_id=None)
        g.tables = [StandardTable(id="t1", standard_id="s", table_name="T")]
        g.table_links = [StandardLayerGroupTableLink(id="l1")]
        assert g.table_count == 2  # 1 主归属 + 1 次归属（is_primary 默认 False）


class TestStandardDictionary:

    def test_is_global(self):
        """71：scope global/standard。"""
        assert StandardDictionary(scope="global", dict_name="g").is_global is True
        assert StandardDictionary(scope="standard", dict_name="s").is_global is False

    def test_item_count(self):
        from app.models.dictionary_item import DictionaryItem

        d = StandardDictionary(dict_name="d")
        d.items = [DictionaryItem(id="i1"), DictionaryItem(id="i2")]
        assert d.item_count == 2