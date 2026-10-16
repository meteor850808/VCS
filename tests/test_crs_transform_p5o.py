"""P5o: crs_transform.py 服务级全覆盖（52% → 100%）。

覆盖目标：41-78 resolve_crs（空串/EPSG 解析失败/纯数字/别名/WKT 回退/pyproj 异常/
GEOGCS 前缀回退）、81-90 extract_crs_from_prj（读文件/空 WKT/异常兜底）、
93-102 crs_to_srid、105-118 transform_coords（happy/pyproj 异常回退原坐标）。
"""

from pathlib import Path

import pytest

import app.services.crs_transform as ct
from app.services.crs_transform import (
    CRSInfo, crs_to_srid, extract_crs_from_prj, resolve_crs,
    transform_coords, get_supported_crs_list,
)


class TestResolveCrs:

    def test_empty_returns_default(self):
        assert resolve_crs("") == CRSInfo(epsg=4326)
        assert resolve_crs("  ").epsg == 4326

    def test_alias_lookup(self):
        assert resolve_crs("国家2000").epsg == 4490
        assert resolve_crs("CGCS2000").epsg == 4490   # 大小写不敏感
        assert resolve_crs("wgs84").epsg == 4326
        assert resolve_crs("web墨卡托").epsg == 3857

    def test_epsg_prefix(self):
        assert resolve_crs("EPSG:4326").epsg == 4326
        assert resolve_crs("epsg:4490").epsg == 4490

    def test_epsg_invalid_falls_through(self):
        """57-58：EPSG: 后非数字 → 继续往下，最终 4326。"""
        info = resolve_crs("EPSG:notanum")
        # 非数字且非 GEOGCS → 回退默认
        assert info.epsg == 4326

    def test_epsg_blank_code(self):
        info = resolve_crs("EPSG:")
        assert info.epsg == 4326

    def test_numeric(self):
        assert resolve_crs("4490").epsg == 4490
        assert resolve_crs("4326").epsg == 4326
        # 不在别名表的纯数字 → 走 isdigit 分支（62 行）
        assert resolve_crs("4646").epsg == 4646

    def test_pyproj_wkt_with_epsg(self, monkeypatch):
        """68-70：pyproj 解析成功且 to_epsg 非 None → 返回 epsg+proj4。"""
        class _FakeCRS:
            @staticmethod
            def from_user_input(s):
                return _FakeCRS()

            def to_epsg(self):
                return 4547

            def to_proj4(self):
                return "+proj=longlat +datum=CGCS2000"

        import pyproj
        monkeypatch.setattr(pyproj, "CRS", _FakeCRS)
        info = resolve_crs("some wkt text")  # 非 EPSG:/数字 → 走 pyproj
        assert info.epsg == 4547 and info.proj4 == "+proj=longlat +datum=CGCS2000"

    def test_pyproj_to_epsg_none_returns_wkt(self, monkeypatch):
        """70：pyproj 解析但 to_epsg() 为 None → 返回 wkt+proj4。"""
        class _FakeCRS:
            @staticmethod
            def from_user_input(s):
                return _FakeCRS()

            def to_epsg(self):
                return None

            def to_proj4(self):
                return "+fake"

            def to_wkt(self):
                return "PROJCS[\"fake\"]"

        import pyproj
        monkeypatch.setattr(pyproj, "CRS", _FakeCRS)
        info = resolve_crs("some wkt string")
        assert info.wkt == 'PROJCS["fake"]' and info.proj4 == "+fake"

    def test_pyproj_exception_falls_back(self, monkeypatch):
        """72-73：pyproj from_user_input 抛错 → 走 GEOGCS/默认回退。"""
        import pyproj

        class _Boom:
            @staticmethod
            def from_user_input(s):
                raise RuntimeError("no crs")

        monkeypatch.setattr(pyproj, "CRS", _Boom)
        # 以 GEOGCS 开头 → 保留为 WKT
        assert resolve_crs("GEOGCS[\"x\"]").wkt == 'GEOGCS["x"]'
        # 其他 → 默认 4326
        assert resolve_crs("garbage").epsg == 4326

    def test_geogcs_prefix_fallback_without_pyproj(self, monkeypatch):
        """76-77：直接以 GEOGCS 开头（非 EPSG/数字）→ WKT。"""
        import pyproj

        class _Noop:
            @staticmethod
            def from_user_input(s):
                raise RuntimeError("no")

        monkeypatch.setattr(pyproj, "CRS", _Noop)
        info = resolve_crs("COMPD_CS[\"composite\"]")
        assert info.wkt == 'COMPD_CS["composite"]'

    def test_unknown_falls_default_4326(self):
        assert resolve_crs("random text").epsg == 4326


class TestExtractCrsFromPrj:

    def test_happy(self, tmp_path):
        p = tmp_path / "a.prj"
        p.write_text('GEOGCS["WGS 84",DATUM...]', encoding="utf-8")
        # 会尝试 pyproj → 失败 → GEOGCS 前缀回退 WKT
        info = extract_crs_from_prj(str(p))
        assert info is not None and "GEOGCS" in (info.wkt or "")

    def test_empty_wkt_returns_none(self, tmp_path):
        p = tmp_path / "e.prj"
        p.write_text("   ", encoding="utf-8")
        assert extract_crs_from_prj(str(p)) is None

    def test_read_error_returns_none(self, tmp_path):
        assert extract_crs_from_prj(str(tmp_path / "missing.prj")) is None

    def test_read_exception(self, tmp_path, monkeypatch):
        """89-90：open 抛错（如权限/编码）→ None。`open` 是 builtins，须 patch 之。"""
        import builtins
        p = tmp_path / "x.prj"
        p.write_text("abc", encoding="utf-8")
        monkeypatch.setattr(builtins, "open",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
        assert extract_crs_from_prj(str(p)) is None


class TestCrsToSrid:

    def test_none_default(self):
        assert crs_to_srid(None) == 4326
        assert crs_to_srid("") == 4326

    def test_alias(self):
        assert crs_to_srid("国家2000") == 4490

    def test_resolvable_epsg(self):
        assert crs_to_srid("EPSG:4547") == 4547

    def test_unresolvable_falls_back_4326(self):
        assert crs_to_srid("garbage") == 4326


class TestTransformCoords:

    def test_happy(self):
        x, y = transform_coords(116.3, 39.9, "EPSG:4326", "EPSG:3857")
        # 3857 是投影米制，坐标应远离原始经纬度
        assert abs(x - 116.3) > 1.0 and abs(y - 39.9) > 1.0

    def test_exception_returns_original(self, monkeypatch):
        """116-118：pyproj 变换抛错 → 返回原坐标 + warning。"""
        import pyproj

        class _BadTransformer:
            @staticmethod
            def from_crs(*a, **k):
                raise ValueError("bad crs")

        monkeypatch.setattr(pyproj, "Transformer", _BadTransformer)
        x, y = transform_coords(1.0, 2.0, "bad", "bad")
        assert (x, y) == (1.0, 2.0)


class TestSupportedCrsList:

    def test_contains_key_epsg(self):
        lst = get_supported_crs_list()
        assert "EPSG:4326" in lst and "EPSG:4490" in lst
        assert len(lst) >= 8