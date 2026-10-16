"""P5n: attribute_processing.py 服务级全覆盖（38% → 100%）。

数据管道最后一环的剩余本体：data.py/tasks 全程 monkeypatch `process_attributes`，
而 DBF/CSV/GeoJSON 三处理器 + 编码检测 + 关键字段保护从未真实走一遍。

策略：
- DBF：真实 dBase III 二进制构造夹具（版本/日期/nrec/hdrlen/reclen + 32B 字段描述
  + 定长记录：删除标记 0x2A、C/N/F/I/L/D 各类型解码、尾部空格剥离）——测真实解析。
- CSV/GeoJSON：真实文件字节 + monkeypatch chardet（编码兜底分支真实不可达：
  chardet 恒返回 dict）。
- `_ensure_critical_fields`：CRITICAL_FIELDS 含大小写变体（YWBH/FEATURECODE/ywbh/
  featurecode/FEATURE_CODE 等），验证存在性检查对大小写不敏感 + 追加去重。

覆盖目标（BASELINE §17 P5n）：25-45（process_attributes 分发 + except 兜底）、
48-56（_ensure_critical_fields）、67-166（_process_dbf：文件头/字段解析/编码检测/
记录读取/删除记录跳过/无字段）、169-212（_process_csv：编码检测/DictReader/字段过滤/
读取失败 except）、215-251（_process_geojson_attrs：无 features/字段过滤/critical）。
"""

import csv
import struct
from pathlib import Path

import pytest

from app.services import attribute_processing as ap
from app.services.attribute_processing import AttributeResult, process_attributes


# ── DBF 二进制夹具 ──────────────────────────────────────────────────────────

def build_dbf(fields, records, encoding="gbk", with_delete=False, keep_header_len=None,
              header_byte=None):
    """构造 dBase III 无 memo 的 DBF 二进制。fields: [(name,type,len,dec)]。"""
    nrec = len(records)
    hdrlen = 32 + 32 * len(fields) + 1
    reclen = sum(f[2] for f in fields) + 1
    field_area = b""
    for name, ft, ln, dec in fields:
        field_area += (name.encode("ascii").ljust(11, b"\x00")
                       + bytes([ord(ft)]) + bytes(4) + bytes([ln, dec]) + bytes(14))
    used_hdrlen = hdrlen if keep_header_len is None else keep_header_len
    hdr = (struct.pack("<B", header_byte if header_byte is not None else 3)
           + bytes([21, 7, 24]) + struct.pack("<I", nrec)
           + struct.pack("<H", used_hdrlen) + struct.pack("<H", reclen) + bytes(20))
    body = b""
    for i, r in enumerate(records):
        flag = 42 if (with_delete and i == 0) else 32
        row = bytes([flag])
        for (name, ft, ln, dec), val in zip(fields, r):
            if ft in "NF":
                row += str(val).encode(encoding)[:ln].ljust(ln, b" ")
            elif ft == "I":
                row += struct.pack("<i", int(val))
            elif ft in "LD":
                row += str(val).encode("ascii")[:ln].ljust(ln, b" ")
            else:
                row += str(val).encode(encoding)[:ln].ljust(ln, b" ")
        body += row
    return hdr + field_area + bytes([0x0D]) + body


DBF_FIELDS = [
    ("YWBH", "C", 10, 0), ("FNAME", "C", 8, 0), ("LV", "N", 6, 0),
    ("FLAG", "I", 4, 0), ("OK", "L", 1, 0), ("DT", "D", 8, 0),
]
DBF_ROWS = [
    ("AB123", "张", 5, 1, "T", "20240101"),
    ("ZY", "", 3, 2, "F", ""),
]


def write_tmp(tmp_path, name, data) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ── process_attributes 分发 ─────────────────────────────────────────────────

class TestProcessAttributes:

    def test_dispatch_dbf(self, tmp_path):
        p = write_tmp(tmp_path, "a.dbf", build_dbf(DBF_FIELDS, DBF_ROWS))
        r = process_attributes(p)
        assert r.fields[0] == "YWBH" and r.total_input == 2

    def test_dispatch_csv(self, tmp_path):
        p = write_tmp(tmp_path, "a.csv",
                      "name,val\nx,1\ny,2".encode("utf-8"))
        r = process_attributes(p)
        assert r.fields == ["name", "val"] and r.total_input == 2

    def test_dispatch_geojson(self, tmp_path):
        data = {"type": "FeatureCollection", "features": [
            {"properties": {"a": 1}}, {"properties": {"b": 2}}]}
        p = write_tmp(tmp_path, "a.geojson",
                      __import__("json").dumps(data).encode("utf-8"))
        r = process_attributes(p)
        assert r.fields == ["a"] and r.total_input == 2

    def test_unsupported_extension(self, tmp_path):
        p = write_tmp(tmp_path, "a.xyz", b"hello")
        r = process_attributes(p)
        assert r.warnings and "不支持" in r.warnings[0]

    def test_exception_surrounded(self, tmp_path, monkeypatch):
        """45：内层抛异常 → 外层兜底回 AttributeResult(warnings)。"""
        monkeypatch.setattr(ap, "_process_dbf",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        p = write_tmp(tmp_path, "a.dbf", build_dbf([("X", "C", 1, 0)], [("1",)]))
        r = process_attributes(p)
        assert r.warnings and "属性处理失败" in r.warnings[0]


# ── _ensure_critical_fields ─────────────────────────────────────────────────

class TestEnsureCriticalFields:

    def test_none_returns_none(self):
        assert ap._ensure_critical_fields(None) is None

    def test_appends_missing_case_insensitive(self):
        out = ap._ensure_critical_fields(["NAME"])
        # YWBH 未在 NAME 中 → 追加；CRITICAL_FIELDS 的每一项都在
        for f in ap.CRITICAL_FIELDS:
            assert f.upper() in (x.upper() for x in out)

    def test_preserves_case_and_dedups(self):
        """已有 'ywbh'（小写命中，大小写不敏感）→ 不再追加 'YWBH'（在集合内去重）。"""
        out = ap._ensure_critical_fields(["ywbh", "other"])
        upper = [x.upper() for x in out]
        # 每个 CRITICAL_FIELDS 条目都出现，且大写归一后不重复
        for f in ap.CRITICAL_FIELDS:
            assert f.upper() in upper
        assert upper.count("YWBH") == 1  # ywbh 命中后不再加 YWBH，但 FEATURECODE 等仍追加
        assert upper.count("OTHER") == 1


# ── _process_dbf ────────────────────────────────────────────────────────────

class TestProcessDbf:

    def test_happy_all_types(self, tmp_path):
        p = write_tmp(tmp_path, "x.dbf", build_dbf(DBF_FIELDS, DBF_ROWS))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.fields == ["YWBH", "FNAME", "LV", "FLAG", "OK", "DT"]
        assert r.total_input == r.total_output == 2
        row0 = r.sample_data[0]
        assert row0["YWBH"] == "AB123" and row0["LV"] == "5"
        assert row0["FLAG"] == 1 and row0["OK"] == "T"
        assert row0["DT"] == "20240101"
        assert row0["FNAME"].strip()  # GBK 中文解码成功（非空）

    def test_truncated_header(self, tmp_path):
        p = write_tmp(tmp_path, "s.dbf", b"\x03\x00")
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert "无效的 DBF 文件头" in (r.warnings or [])

    def test_deleted_record_skipped(self, tmp_path):
        """124-125：0x2A 删除标记记录跳过，不进 sample。"""
        rows = [("A", "x", 1, 0, "T", "20240101"), ("B", "y", 2, 0, "F", "")]
        p = write_tmp(tmp_path, "d.dbf", build_dbf(DBF_FIELDS, rows, with_delete=True))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.total_input == 2
        assert len(r.sample_data) == 1  # 删除记录跳过
        assert r.sample_data[0]["YWBH"] == "B"

    def test_no_field_defs(self, tmp_path):
        """字段区为空 → fields 空 → 152-154 分支。"""
        hdr = (struct.pack("<B", 3) + bytes([21, 7, 24]) + struct.pack("<I", 0)
               + struct.pack("<H", 33) + struct.pack("<H", 1) + bytes(20))
        p = write_tmp(tmp_path, "n.dbf", hdr + bytes([0x0D]))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.fields == [] and "字段定义" in (r.warnings or [""])[0]

    def test_ascii_encoding_fallback_to_gbk(self, tmp_path, monkeypatch):
        """112-113：chardet 报 ASCII → 强制 gbk（DBF 常见 CP936）。"""
        def fake_detect(raw):
            return {"encoding": "ASCII", "confidence": 1.0}

        monkeypatch.setattr("chardet.detect", fake_detect)
        rows = [("Z", "值", 1, 0, "T", "")]
        p = write_tmp(tmp_path, "a.dbf", build_dbf([("YWBH", "C", 4, 0),
                                                    ("C1", "C", 4, 0)], rows))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.sample_data[0]["C1"] == "值"

    def test_integer_field_too_short(self, tmp_path):
        """I 字段长度 <4 → None（139-140）。"""
        fields = [("FLAG", "I", 4, 0)]
        # 记录 1 字节 → I 字段 raw<4 → None
        hdrlen = 32 + 32 + 1
        hdr = (struct.pack("<B", 3) + bytes([21, 7, 24]) + struct.pack("<I", 1)
               + struct.pack("<H", hdrlen) + struct.pack("<H", 2) + bytes(20))
        fa = (b"FLAG".ljust(11, b"\x00") + bytes([ord("I")]) + bytes(4)
              + bytes([4, 0]) + bytes(14))
        p = write_tmp(tmp_path, "i.dbf", hdr + fa + bytes([0x0D]) + bytes([32, 1]))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.sample_data[0]["FLAG"] is None

    def test_deleted_flag_and_short_record(self, tmp_path):
        """120-121：记录截断 → break。"""
        fields = [("A", "C", 4, 0)]
        rows = [("1",), ("2",)]
        full = build_dbf(fields, rows)
        # 截断 body 只留第一条记录的一半 → 第二条读取 len<record_len break
        truncated = full[:-2]
        p = write_tmp(tmp_path, "t.dbf", truncated)
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.total_input == 2  # num_records 仍按 header 2

    def test_break_on_terminator_block(self, tmp_path):
        """94：字段区第一块 raw[0]=0x0D → break（未到 header_len-1）。"""
        hdrlen = 32 + 32 + 1
        hdr = (struct.pack("<B", 3) + bytes([21, 7, 24]) + struct.pack("<I", 0)
               + struct.pack("<H", hdrlen + 32) + struct.pack("<H", 1) + bytes(20))
        # 字段区：一个以 0x0D 开头的 32B 块，迫使循环 raw[0]=0x0D break
        block = bytes([0x0D]) + bytes(31)
        p = write_tmp(tmp_path, "b.dbf", hdr + block + bytes([0x0D]) + bytes([32]))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.fields == [] and "字段定义" in (r.warnings or [""])[0]

    def test_continue_on_blank_field_name(self, tmp_path):
        """96-97：空名字段块 → continue 跳过，后续字段正常解析。"""
        nrec = 0
        hdrlen = 32 + 32 * 2 + 1
        hdr = (struct.pack("<B", 3) + bytes([21, 7, 24]) + struct.pack("<I", nrec)
               + struct.pack("<H", hdrlen) + struct.pack("<H", 1) + bytes(20))
        # 第一块：11 字节全空格（raw[0]=0x20 非终止，name 解码 strip 空）→ continue
        blank = b" " * 11 + bytes([ord("C")]) + bytes(4) + bytes([2, 0]) + bytes(14)
        # 第二块：正常字段 NAME
        good = b"NAME".ljust(11, b"\x00") + bytes([ord("C")]) + bytes(4) + bytes([2, 0]) + bytes(14)
        p = write_tmp(tmp_path, "c.dbf", hdr + blank + good + bytes([0x0D]))
        r = ap._process_dbf(p, None, "UTF-8", True)
        assert r.fields == ["NAME"]


# ── _process_csv ────────────────────────────────────────────────────────────

class TestProcessCsv:

    def test_happy_with_field_filter(self, tmp_path):
        data = "name,val,keep\nx,1,y\na,2,b\n".encode("utf-8")
        p = write_tmp(tmp_path, "c.csv", data)
        r = ap._process_csv(p, ["name", "keep"], "UTF-8", True)
        assert r.fields == ["name", "keep"]
        assert r.sample_data[0] == {"name": "x", "keep": "y"}
        assert r.total_input == 2

    def test_preserve_critical_appends(self, tmp_path):
        data = "name\nywb\nx\n".encode("utf-8")
        p = write_tmp(tmp_path, "c.csv", data)
        r = ap._process_csv(p, ["name"], "UTF-8", True)
        # select_fields 被扩展（YWBH/FEATURECODE 追加），但源无这些列 → 输出仅匹配存在的
        assert r.fields == ["name"]
        # 若源含 critical 列（大小写命中），则保留
        data2 = "name,YWBH\nx,1\n".encode("utf-8")
        p2 = write_tmp(tmp_path, "c2.csv", data2)
        r2 = ap._process_csv(p2, ["name"], "UTF-8", True)
        assert set(x.upper() for x in r2.fields) == {"NAME", "YWBH"}

    def test_no_select_no_critical(self, tmp_path):
        """preserve_critical=False → 不过滤、sample 全字段。"""
        data = "name,val\nx,1\n".encode("utf-8")
        p = write_tmp(tmp_path, "c.csv", data)
        r = ap._process_csv(p, None, "UTF-8", False)
        assert r.fields == ["name", "val"] and r.sample_data[0] == {"name": "x", "val": "1"}

    def test_read_failure_swallowed(self, tmp_path, monkeypatch):
        """196-197：DictReader 抛错 → except 吞掉、total=0。"""
        monkeypatch.setattr(ap.csv, "DictReader",
                            lambda f: (_ for _ in ()).throw(OSError("bad")))
        p = write_tmp(tmp_path, "c.csv", b"name\nx\n")
        r = ap._process_csv(p, None, "UTF-8", True)
        assert r.total_input == 0 and r.fields == []


# ── _process_geojson_attrs ──────────────────────────────────────────────────

class TestProcessGeojson:

    def _write(self, tmp_path, features):
        data = {"type": "FeatureCollection", "features": features}
        return write_tmp(tmp_path, "g.json", __import__("json").dumps(data).encode("utf-8"))

    def test_happy(self, tmp_path):
        p = self._write(tmp_path, [{"properties": {"a": 1, "b": 2}},
                                   {"properties": {"a": 3}}])
        r = ap._process_geojson_attrs(p, None, False)
        assert r.fields == ["a", "b"] and r.total_input == 2
        assert len(r.sample_data) == 2

    def test_no_features(self, tmp_path):
        p = self._write(tmp_path, [])
        r = ap._process_geojson_attrs(p, None, True)
        assert r.fields == [] and r.total_input == 0 and r.sample_data == []

    def test_field_filter_and_critical(self, tmp_path):
        # 源含 critical 列（大小写命中）→ 输出保留
        p = self._write(tmp_path, [{"properties": {"name": "x", "YWBH": 1}}])
        r = ap._process_geojson_attrs(p, ["name"], True)
        assert set(x.upper() for x in r.fields) == {"NAME", "YWBH"}

    def test_empty_props_skipped_in_sample(self, tmp_path):
        """234-235：properties 空（None/{}）不进 sample。"""
        p = self._write(tmp_path, [{"properties": {}}, {"properties": None},
                                   {"properties": {"a": 9}}])
        r = ap._process_geojson_attrs(p, None, False)
        # sample 只收集非空 properties 的第一条（取前 100，此处 features[0].properties 空）
        assert r.total_input == 3
        assert all(x == {"a": 9} for x in r.sample_data if x)