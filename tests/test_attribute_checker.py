# -*- coding: utf-8 -*-
"""P5i — app/services/quality/attribute_checker.py 服务级 100%。

覆盖：
- load_standard_tables：标准不存在 / children 递归 / standard_table_id 过滤 /
  dict_links → items 值域聚合。
- load_features_by_file：三表查询行展开、properties=None → {}。
- run_attribute_checks 九项检查（A1-A9）多表 fail 全套、多表 pass 全套路、
  单表模式（A1 pass + 无 geometry 表 A2 跳过）、无标准表守卫、无要素守卫。
- _summarize：空结果 / 三档 severity 计数。

策略：loader 用 StandardTableDef / dict 直接构造，monkeypatch 两个加载函数，
不触碰 PostGIS 分区表。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.core.security import generate_api_key
from app.services.quality import attribute_checker as ac

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


# ── 构造 helpers ────────────────────────────────────────────────────────────

def _fdef(name, data_type="string", length=None, precision=None, nullable=True,
          required=False, pk=False, dict_codes=None):
    return ac.StandardFieldDef(
        field_name=name, data_type=data_type, length=length, precision=precision,
        nullable=nullable, is_required=required, is_primary_key=pk,
        dict_item_codes=list(dict_codes or []),
    )


def _tdef(name, *fields, geometry=None, table_id=None):
    return ac.StandardTableDef(
        table_name=name, table_code=name.upper(), geometry_type=geometry,
        fields=list(fields), table_id=table_id or f"tid-{name}",
    )


def _feat(layer, gtype, props, fidx=0):
    return {
        "feature_index": fidx,
        "layer_name": layer,
        "source_name": "src",
        "geometry_type": gtype,
        "properties": props,
    }


def _async_of(value):
    """包成返回固定值、参数可变的协程 loader（monkeypatch 用）。"""

    async def _loader(db, *args, **kwargs):
        return value

    return _loader


# ── load_standard_tables ────────────────────────────────────────────────────

class TestLoadStandardTables:

    async def test_standard_not_found(self, db_session):
        tables = await ac.load_standard_tables(db_session, str(uuid.uuid4()))
        assert tables == []

    async def test_recursive_collect_and_dict_aggregation(self, db_session):
        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="ac_ld_a", is_active=True)
        db_session.add(key)
        await db_session.flush()
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id, name="检查标准")
        db_session.add(std)
        await db_session.flush()
        root = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                                  group_name="根", ordering=0)
        db_session.add(root)
        await db_session.flush()
        child = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                                   parent_id=root.id, group_name="子", ordering=0)
        db_session.add(child)
        await db_session.flush()
        t1 = StandardTable(id=str(uuid.uuid4()), layer_id=child.id, standard_id=std.id,
                           table_name="深表", table_code="DEEP", table_type="attribute",
                           geometry_type="Point")
        db_session.add(t1)
        await db_session.flush()
        # 字段 A：link → 字典（items X/Y）
        f1 = StandardField(id=str(uuid.uuid4()), table_id=t1.id, standard_id=std.id,
                           field_name="a", data_type="string", is_required=True)
        d1 = StandardDictionary(id=str(uuid.uuid4()), standard_id=std.id,
                                dict_name="字典1", dict_code="D1")
        db_session.add_all([f1, d1])
        await db_session.flush()
        db_session.add_all([
            DictionaryItem(id=str(uuid.uuid4()), dict_id=d1.id,
                           item_code="X", item_name="甲"),
            DictionaryItem(id=str(uuid.uuid4()), dict_id=d1.id,
                           item_code="Y", item_name="乙"),
        ])
        await db_session.flush()
        l1 = StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f1.id, dict_id=d1.id)
        db_session.add(l1)
        # 字段 B：link → 字典（无 items）
        f2 = StandardField(id=str(uuid.uuid4()), table_id=t1.id, standard_id=std.id,
                           field_name="b", data_type="string")
        d2 = StandardDictionary(id=str(uuid.uuid4()), standard_id=std.id,
                                dict_name="空字典", dict_code="D2")
        db_session.add_all([f2, d2])
        await db_session.flush()
        db_session.add(StandardFieldDictLink(id=str(uuid.uuid4()), field_id=f2.id, dict_id=d2.id))
        await db_session.commit()

        tables = await ac.load_standard_tables(db_session, std.id)
        assert len(tables) == 1
        t = tables[0]
        assert t.table_name == "深表"
        assert t.geometry_type == "Point"
        assert t.table_id == t1.id
        by_name = {f.field_name: f for f in t.fields}
        assert sorted(by_name["a"].dict_item_codes) == ["X", "Y"]
        assert by_name["b"].dict_item_codes == []
        assert by_name["a"].is_required is True

    async def test_standard_table_id_filter(self, db_session):
        raw, hashed = generate_api_key()
        key = ApiKey(key_hash=hashed, name="ac_ld_b", is_active=True)
        db_session.add(key)
        await db_session.flush()
        std = DataStandard(id=str(uuid.uuid4()), api_key_id=key.id, name="过滤标准")
        db_session.add(std)
        await db_session.flush()
        lg = StandardLayerGroup(id=str(uuid.uuid4()), standard_id=std.id,
                                group_name="L", ordering=0)
        db_session.add(lg)
        await db_session.flush()
        t1 = StandardTable(id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
                           table_name="T1", table_type="attribute")
        t2 = StandardTable(id=str(uuid.uuid4()), layer_id=lg.id, standard_id=std.id,
                           table_name="T2", table_type="attribute")
        db_session.add_all([t1, t2])
        await db_session.commit()

        tables = await ac.load_standard_tables(db_session, std.id, standard_table_id=t2.id)
        assert [t.table_name for t in tables] == ["T2"]
        # 全部
        tables_all = await ac.load_standard_tables(db_session, std.id)
        assert {t.table_name for t in tables_all} == {"T1", "T2"}


# ── load_features_by_file ───────────────────────────────────────────────────

class TestLoadFeaturesByFile:

    async def test_reads_three_tables_with_props_fallback(self):
        db = AsyncMock()
        calls = iter([
            [
                SimpleNamespace(feature_index=0, layer_name="L1", source_name="a.geojson",
                                geometry_type="Point", properties={"id": 1}),
                SimpleNamespace(feature_index=1, layer_name="L1", source_name="a.geojson",
                                geometry_type="Point", properties=None),
            ],
            [], [],  # 其余两表无行
        ])

        def _exec(stmt):
            return SimpleNamespace(all=lambda: next(calls))

        db.execute = AsyncMock(side_effect=_exec)
        feats = await ac.load_features_by_file(db, "file-x", "key-x")
        assert len(feats) == 2
        assert feats[0]["feature_index"] == 0
        assert feats[0]["properties"] == {"id": 1}
        assert feats[1]["properties"] == {}
        assert feats[0]["geometry_type"] == "Point"
        assert db.execute.await_count == 3  # 三张表各一查


# ── run_attribute_checks ────────────────────────────────────────────────────

class TestRunAttributeChecks:

    async def test_std_has_no_tables(self, db_session, monkeypatch):
        async def _load(db, sid, s_tid=None):
            return []

        monkeypatch.setattr(ac, "load_standard_tables", _load)
        results, summary = await ac.run_attribute_checks(
            db_session, str(uuid.uuid4()), str(uuid.uuid4()), "k")
        assert results[0].rule_code == "31010001"
        assert results[0].status == "fail"
        assert summary == {"total": 1, "passed": 0, "failed": 1,
                           "by_severity": {"error": 1}}

    async def test_std_ok_but_no_features(self, db_session, monkeypatch):
        async def _load(db, sid, s_tid=None):
            return [_tdef("T1")]

        async def _load_f(db, fid, kid):
            return []

        monkeypatch.setattr(ac, "load_standard_tables", _load)
        monkeypatch.setattr(ac, "load_features_by_file", _load_f)
        results, summary = await ac.run_attribute_checks(
            db_session, "s", "f", "k")
        assert results[0].rule_code == "31010007"
        assert "无任何要素" in results[0].message

    async def test_multi_table_all_pass(self, db_session, monkeypatch):
        std_tables = [_tdef(
            "路网层",
            _fdef("required", required=True),
            _fdef("dict", dict_codes=["X", "Y"]),
            _fdef("pk", pk=True),
            _fdef("pre", precision=2),
            _fdef("len", length=3),
            geometry="LineString",
        )]
        feats = [
            _feat("路网层", "LineString",
                  {"required": "r1", "dict": "X", "pk": "a1", "pre": "1.23", "len": "abc"}, 0),
            _feat("路网层", "LineString",
                  {"required": "r2", "dict": "Y", "pk": "a2", "pre": "2.50", "len": "def"}, 1),
        ]
        monkeypatch.setattr(ac, "load_standard_tables", _async_of(std_tables))
        monkeypatch.setattr(ac, "load_features_by_file", _async_of(feats))

        results, summary = await ac.run_attribute_checks(db_session, "s", "f", "k")
        by_code = {r.rule_code: r for r in results}
        # A1 全图层存在 → pass
        assert by_code["31010001"].status == "pass"
        assert by_code["31010002"].status == "pass"   # LineString 匹配
        assert by_code["31010003"].status == "pass"
        assert by_code["31010004"].status == "pass"
        assert by_code["31010005"].status == "pass"
        assert by_code["31010006"].status == "pass"
        assert by_code["31010008"].status == "pass"
        assert by_code["31010009"].status == "pass"
        assert "31010007" not in by_code  # A7 无要素才出现
        assert summary["failed"] == 0
        assert summary["total"] == 8

    async def test_multi_table_all_fail(self, db_session, monkeypatch):
        std_tables = [
            _tdef("路网层",
                  _fdef("required", required=True),
                  _fdef("dict", dict_codes=["X", "Y"]),
                  _fdef("pk", pk=True),
                  _fdef("pre", precision=2),
                  _fdef("len", length=3),
                  geometry="LineString"),
            _tdef("控制点层", _fdef("code"), geometry="Point"),
        ]
        feats = [
            _feat("路网层", "LineString",
                  {"required": "r1", "dict": "X", "pk": "dup1", "pre": "1.23", "len": "abc",
                   "extra": "e1"}, 0),
            _feat("路网层", "Point",
                  {"required": "", "dict": "Z", "pk": "dup1", "pre": "1.234", "len": "abcdef"}, 1),
            _feat("路网层", "LineString",
                  {"required": "r3", "dict": "X", "pk": "u2", "pre": "", "len": "ok"}, 2),
        ]
        monkeypatch.setattr(ac, "load_standard_tables", _async_of(std_tables))
        monkeypatch.setattr(ac, "load_features_by_file", _async_of(feats))

        results, summary = await ac.run_attribute_checks(db_session, "s", "f", "k")
        by_code = {r.rule_code: r for r in results}
        # A1 缺「控制点层」→ fail
        assert by_code["31010001"].status == "fail"
        assert "缺失必要图层" in by_code["31010001"].message
        assert by_code["31010002"].status == "fail"
        assert by_code["31010003"].status == "fail"
        assert "多余字段" in by_code["31010003"].message
        a4 = by_code["31010004"]
        assert a4.status == "fail"
        assert a4.failed_count == 1
        assert a4.samples == [1]  # 该行 feature_index
        assert by_code["31010005"].status == "fail"
        assert by_code["31010005"].failed_count == 1
        assert "值不在允许值域" in by_code["31010005"].message
        assert by_code["31010006"].status == "fail"
        assert by_code["31010006"].failed_count == 2
        assert by_code["31010008"].status == "fail"
        assert by_code["31010008"].failed_count == 1
        assert by_code["31010009"].status == "fail"
        assert by_code["31010009"].failed_count == 1
        # 控制点层无要素 → 另产出 A7
        assert "31010007" in by_code
        assert by_code["31010007"].layer == "控制点层"
        assert summary["total"] == 9
        assert summary["failed"] == 9
        assert summary["by_severity"] == {"error": 5, "warning": 4, "info": 0}

    async def test_single_table_mode(self, db_session, monkeypatch):
        t = _tdef("目标表", _fdef("name", required=True), table_id="T-1")
        std_tables = [t]
        feats = [
            _feat("临时文件名", "Polygon",
                  {"name": "甲"}, 0),
            _feat("临时文件名", "Polygon",
                  {"name": "乙"}, 1),
        ]
        async def _load(db, sid, s_tid=None):
            return std_tables if s_tid else []

        async def _load_f(db, fid, kid):
            return feats

        monkeypatch.setattr(ac, "load_standard_tables", _load)
        monkeypatch.setattr(ac, "load_features_by_file", _load_f)

        results, summary = await ac.run_attribute_checks(
            db_session, "s", "f", "k", standard_table_id="T-1")
        by_code = {r.rule_code: r for r in results}
        # A1 单表模式 → 直接 pass 且消息含表名
        assert by_code["31010001"].status == "pass"
        assert "目标表" in by_code["31010001"].message
        # 无 geometry → A2 不产出；A3/A4 pass
        assert "31010002" not in by_code
        assert by_code["31010003"].status == "pass"
        assert by_code["31010004"].status == "pass"
        assert summary["failed"] == 0

    async def test_a3_missing_and_extra_with_ghost_field(self, db_session, monkeypatch):
        """缺失+多余字段同现；标准字段不在数据列 → 跳过逐字段检查。"""
        std_tables = [_tdef("L",
                            _fdef("a", required=True),
                            _fdef("b"),
                            _fdef("c"),
                            _fdef("ghost"),
                            geometry="Point")]
        feats = [_feat("L", "Point", {"a": "x", "b": "y", "d": "z"}, 0)]
        monkeypatch.setattr(ac, "load_standard_tables", _async_of(std_tables))
        monkeypatch.setattr(ac, "load_features_by_file", _async_of(feats))

        results, summary = await ac.run_attribute_checks(db_session, "s", "f", "k")
        by_code = {r.rule_code: r for r in results}
        a3 = by_code["31010003"]
        assert a3.status == "fail"
        assert "缺失字段" in a3.message and "多余字段" in a3.message
        # a 必填通过；b/c/ghost 中 c、ghost 不在列 → continue
        assert by_code["31010004"].status == "pass"
        assert "31010007" not in by_code

    async def test_a7_empty_layer(self, db_session, monkeypatch):
        """目标表有要素、另一表无要素 → A7 fail + 跳过其余检查。"""
        std_tables = [
            _tdef("有图层", _fdef("a")),
            _tdef("空图层", _fdef("b")),
        ]
        feats = [_feat("有图层", "Point", {"a": 1}, 0)]
        monkeypatch.setattr(ac, "load_standard_tables", _async_of(std_tables))
        monkeypatch.setattr(ac, "load_features_by_file", _async_of(feats))

        results, summary = await ac.run_attribute_checks(db_session, "s", "f", "k")
        a7s = [r for r in results if r.rule_code == "31010007"]
        assert len(a7s) == 1
        assert a7s[0].layer == "空图层"
        assert a7s[0].status == "fail"
        assert "无要素" in a7s[0].message


# ── _summarize ──────────────────────────────────────────────────────────────

class TestSummarize:

    def test_empty_results(self):
        assert ac._summarize([]) == {
            "total": 0, "passed": 0, "failed": 0,
            "by_severity": {"error": 0, "warning": 0, "info": 0},
        }

    def test_mixed_status_severity(self):
        results = [
            ac.CheckResult(rule_code="R1", rule_name="a", status="pass", severity="info"),
            ac.CheckResult(rule_code="R2", rule_name="b", status="fail", severity="error"),
            ac.CheckResult(rule_code="R3", rule_name="c", status="fail", severity="warning"),
            ac.CheckResult(rule_code="R4", rule_name="d", status="fail", severity="info"),
        ]
        s = ac._summarize(results)
        assert s == {"total": 4, "passed": 1, "failed": 3,
                     "by_severity": {"error": 1, "warning": 1, "info": 1}}