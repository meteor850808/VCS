"""P6e-1: archive_extractor.py + esri_shapebin.py → 100% 收官。

- archive_extractor: ShpGroup.companion / is_archive 快捷路径 / 不支持格式 /
  中文 ZIP 文件名修复（已有中文、GBK 乱码还原、编码失败）/ 恶意路径 /
  大小超限 / 隐藏文件 / TAR 全分支（含 extractfile=None）
- esri_shapebin: NULL/POINTZ/POINTM/截断头/多外环MultiPolygon/CW短环/
  shapebin_to_geojson 封装 / __main__ 模块入口（fake pyodbc）
"""

import io
import runpy
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import archive_extractor as ae
from app.services import esri_shapebin as es


# ── helpers ───────────────────────────────────────────────────────────────────

def _ef(name: str, data: bytes = b"x") -> ae.ExtractedFile:
    return ae.ExtractedFile(original_name=name, content=data, file_size=len(data))


def make_zip(files: dict[str, bytes], dirs: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in (dirs or []):
            zf.writestr(zipfile.ZipInfo(d + "/"), b"")
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def shapebin(shape_type: int, payload: bytes) -> bytes:
    return struct.pack("<I", shape_type) + payload


# ── archive_extractor ─────────────────────────────────────────────────────────

class TestShpGroup:

    def test_has_companion(self):
        g = ae.ShpGroup(companions={".dbf": _ef("a.dbf")})
        assert g.has_companion is True
        assert ae.ShpGroup().has_companion is False

    def test_companion_contents(self):
        g = ae.ShpGroup(shp=_ef("a.shp"), companions={".dbf": _ef("a.dbf")})
        out = g.companion_contents
        assert out[".shp"] == b"x" and out[".dbf"] == b"x"


class TestIsArchive:

    @pytest.mark.parametrize("name", ["a.tar.gz", "b.tgz"])
    def test_tar_gz_shortcut(self, name):
        assert ae.is_archive(name) is True

    def test_zip_ext(self):
        assert ae.is_archive("data.ZIP") is True
        assert ae.is_archive("data.txt") is False


class TestExtractArchiveDispatch:

    def test_unsupported_format(self):
        """104-105：不支持的压缩格式。"""
        r = ae.extract_archive(b"xx", "a.rar", 100, 1000)
        assert r.errors == ["不支持的压缩格式: a.rar (仅支持 .zip / .tar / .tar.gz)"]

    def test_zip_dispatch(self):
        r = ae.extract_archive(make_zip({"a.txt": b"hi"}), "x.zip", 100, 1000)
        assert [f.original_name for f in r.files] == ["a.txt"]

    def test_tar_dispatch(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            data = b"hello"
            info = tarfile.TarInfo("t.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        r = ae.extract_archive(buf.getvalue(), "x.tar", 100, 1000)
        assert [f.original_name for f in r.files] == ["t.txt"]


class TestFixZipFilename:

    def test_empty_name(self):
        """207：空文件名。"""
        assert ae._fix_zip_filename(SimpleNamespace(filename="")) == ""

    def test_already_chinese(self):
        """210：已含中文（UTF-8 flag 正确）。"""
        assert ae._fix_zip_filename(SimpleNamespace(filename="中文.txt")) == "中文.txt"

    def test_gbk_roundtrip(self):
        """211-214：cp437 乱码 → gbk 还原。"""
        raw = "测试.txt".encode("gbk").decode("cp437")
        assert ae._fix_zip_filename(SimpleNamespace(filename=raw)) == "测试.txt"

    def test_encode_fail(self):
        """216：cp437 无法编码 → 保留原名。"""
        assert ae._fix_zip_filename(SimpleNamespace(filename="한글.txt")) == "한글.txt"


class TestExtractZip:

    def test_directory_entry_skipped(self):
        """226：目录条目跳过。"""
        r = ae.ExtractResult()
        ae._extract_zip(make_zip({"a.txt": b"1"}, dirs=["folder"]), r, 100, 1000)
        assert [f.original_name for f in r.files] == ["a.txt"]

    def test_gbk_filename_in_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zi = zipfile.ZipInfo("测试.txt".encode("gbk").decode("cp437"))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, b"data")
        r = ae.ExtractResult()
        ae._extract_zip(buf.getvalue(), r, 100, 1000)
        assert [f.original_name for f in r.files] == ["测试.txt"]

    def test_bad_zip(self):
        """229-230：BadZipFile。"""
        r = ae.ExtractResult()
        ae._extract_zip(b"not a zip at all", r, 100, 1000)
        assert len(r.errors) == 1 and "ZIP 解压失败" in r.errors[0]


class TestExtractTar:

    def _tar_bytes(self, members: list[tuple[str, bytes | None]]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, data in members:
                if data is not None:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
                else:
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE
                    tf.addfile(info)
        return buf.getvalue()

    def test_full_tar(self):
        """235-242：isfile + extractfile 正常。"""
        r = ae.ExtractResult()
        ae._extract_tar(self._tar_bytes([("a.txt", b"A"), ("b.txt", b"B")]),
                        r, 100, 1000)
        assert sorted(f.original_name for f in r.files) == ["a.txt", "b.txt"]

    def test_directory_member_skipped(self):
        """238：非 isfile 成员跳过。"""
        r = ae.ExtractResult()
        ae._extract_tar(self._tar_bytes([("dir", None), ("f.txt", b"F")]),
                        r, 100, 1000)
        assert [f.original_name for f in r.files] == ["f.txt"]

    def test_extractfile_none(self):
        """239-241：extractfile 返回 None → continue。"""

        class FakeMember:
            def __init__(self, name):
                self.name = name

            def isfile(self):
                return True

        class FakeTF:
            def __init__(self):
                self._members = [FakeMember("a"), FakeMember("b")]

            def getmembers(self):
                return self._members

            def extractfile(self, m):
                return None if m.name == "a" else io.BytesIO(b"B")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake = FakeTF()
        orig = tarfile.open
        tarfile.open = lambda *a, **k: fake
        try:
            r = ae.ExtractResult()
            ae._extract_tar(b"", r, 100, 1000)
            assert [f.original_name for f in r.files] == ["b"]
            assert r.total_size == 1
        finally:
            tarfile.open = orig

    def test_bad_tar(self):
        """243-244：TarError。"""
        r = ae.ExtractResult()
        ae._extract_tar(b"garbage-not-tar", r, 100, 1000)
        assert len(r.errors) == 1 and "TAR 解压失败" in r.errors[0]


class TestAddExtracted:

    def test_dangerous_path(self):
        """253-255：危险路径。"""
        r = ae.ExtractResult()
        ae._add_extracted(b"x", "../etc/passwd", r, 100, 1000)
        assert r.errors and "危险路径" in r.errors[0]
        ae._add_extracted(b"x", "/abs/path", r, 100, 1000)
        assert len(r.errors) == 2

    def test_file_too_big(self):
        """256-258：单文件超限。"""
        r = ae.ExtractResult()
        ae._add_extracted(b"x" * 200, "big.txt", r, max_file_size=100, max_total_size=1000)
        assert r.errors[0].startswith("跳过过大文件")
        assert r.files == []

    def test_total_too_big(self):
        """259-261：总量超限。"""
        r = ae.ExtractResult()
        ae._add_extracted(b"x" * 60, "a.txt", r, 100, 100)
        ae._add_extracted(b"x" * 60, "b.txt", r, 100, 100)
        assert r.files and len(r.errors) == 1
        assert r.errors[0].startswith("解压总量超限")

    @pytest.mark.parametrize("base", ["._x", ".DS_Store", "Thumbs.db"])
    def test_hidden_files_skipped(self, base):
        """264-265：隐藏/系统文件跳过。"""
        r = ae.ExtractResult()
        ae._add_extracted(b"x", f"dir/{base}", r, 100, 1000)
        assert r.files == []

    def test_normal_add(self):
        r = ae.ExtractResult()
        ae._add_extracted(b"abc", "dir/a.txt", r, 100, 1000)
        assert r.files[0].original_name == "dir/a.txt"
        assert r.total_size == 3


# ── esri_shapebin ─────────────────────────────────────────────────────────────

def _line_payload(n_parts: int, n_points: int, parts: list[int],
                  points: list[tuple[float, float]]) -> bytes:
    """PolyLine/Polygon 的 bbox+parts+points 结构。"""
    out = b"\x00" * 32  # bbox
    out += struct.pack("<ii", n_parts, n_points)
    out += b"".join(struct.pack("<i", p) for p in parts)
    out += b"".join(struct.pack("<dd", x, y) for x, y in points)
    return out


class TestDecodeEsriShapebin:

    def test_null_shape(self):
        """97：SHPT_NULL(0) 到达校验 → None。"""
        assert es.decode_esri_shapebin(shapebin(0, b"\x00" * 8)) is None

    def test_short_or_empty(self):
        assert es.decode_esri_shapebin(b"") is None
        assert es.decode_esri_shapebin(b"\x00" * 4) is None

    def test_invalid_type(self):
        assert es.decode_esri_shapebin(shapebin(0xDEAD, b"\x00" * 8)) is None

    def test_pointz_unsupported(self):
        """112-113：POINTZ(11)/POINTM(21) 通过校验但无解码分支 → None。"""
        assert es.decode_esri_shapebin(shapebin(11, b"\x00" * 16)) is None
        assert es.decode_esri_shapebin(shapebin(21, b"\x00" * 16)) is None

    def test_z_m_variants_line(self):
        """104-107：ARCZ/ARCM→LineString；POLYGONZ/POLYGONM→Polygon。"""
        payload = _line_payload(1, 3, [0], [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        for t in (13, 23):
            g = es.decode_esri_shapebin(shapebin(t, payload))
            assert g == {"type": "LineString",
                         "coordinates": [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]}
        for t in (15, 25):
            g = es.decode_esri_shapebin(shapebin(t, payload))
            assert g == {"type": "Polygon",
                         "coordinates": [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]]}

    def test_multipoint_truncated(self):
        """128-129：multipoint 头截断。"""
        blob = shapebin(8, b"\x00" * 30)  # bbox 不足 32 + 4
        assert es.decode_esri_shapebin(blob) is None

    def test_multipoint_ok(self):
        payload = b"\x00" * 32 + struct.pack("<i", 1) + struct.pack("<dd", 1.5, 2.5)
        g = es.decode_esri_shapebin(shapebin(8, payload))
        assert g == {"type": "MultiPoint", "coordinates": [[1.5, 2.5]]}

    def test_short_ring_cw(self):
        assert es._ring_is_cw([[0.0, 0.0], [1.0, 1.0]]) is True

    def test_polygon_all_short_rings(self):
        """194-196：所有 ring 不足 3 点 → None。"""
        payload = _line_payload(2, 4, [0, 2], [(0, 0), (1, 1), (2, 2), (3, 3)])
        assert es.decode_esri_shapebin(shapebin(5, payload)) is None

    def test_polygon_multi_outer(self):
        """215-221：两外环（CW）→ MultiPolygon。"""
        ring1 = [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0], [0.0, 0.0]]
        ring2 = [[20.0, 20.0], [20.0, 30.0], [30.0, 30.0], [30.0, 20.0], [20.0, 20.0]]
        pts = [tuple(p) for p in ring1] + [tuple(p) for p in ring2]
        payload = _line_payload(2, len(pts), [0, len(ring1)], pts)
        g = es.decode_esri_shapebin(shapebin(5, payload))
        assert g == {"type": "MultiPolygon", "coordinates": [[ring1], [ring2]]}

    def test_linestring_no_points(self):
        """230-231：无点 → None。"""
        payload = _line_payload(1, 0, [0], [])
        assert es.decode_esri_shapebin(shapebin(3, payload)) is None

    def test_shpbin_to_geojson_wrapper(self):
        """239：便捷封装。"""
        payload = struct.pack("<dd", 7.0, 8.0)
        g = es.shapebin_to_geojson(shapebin(1, payload))
        assert g == {"type": "Point", "coordinates": [7.0, 8.0]}


class TestShapebinMainBlock:

    def test_main_block(self, tmp_path, monkeypatch):
        """243-263：__main__ 模块入口（fake pyodbc 驱动）。"""

        class FakeCursor:
            def tables(self, tableType="TABLE"):
                return [SimpleNamespace(table_name=t) for t in
                        ("LANDSURVEY_DEMARCATE_RANGE",
                         "LANDSURVEY_DETAIL_PARCEL",
                         "LANDSURVEY_PLANNING_RANGE")]

            def execute(self, sql, *args):
                # 261-262：其中一张表读取失败 → except 分支
                if "DETAIL_PARCEL" in sql:
                    raise RuntimeError("boom")

            def fetchall(self):
                # 合法 Point shapebin + 无符号
                blob = shapebin(1, struct.pack("<dd", 1.0, 2.0))
                return [(blob,)]

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        class FakePyodbc:
            Error = RuntimeError

            @staticmethod
            def connect(conn_str):
                return FakeConn()

        monkeypatch.setitem(sys.modules, "pyodbc", FakePyodbc())
        monkeypatch.setattr(sys, "argv", ["prog", "C:/fake.mdb"])
        runpy.run_path(str(Path(es.__file__).parent / "esri_shapebin.py"),
                       run_name="__main__")