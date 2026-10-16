# -*- coding: utf-8 -*-
"""cad_cache 快照缓存单元测试（P7 覆盖回跌收口：cad_cache 70%→100%）。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCacheCadSnapshot:
    """cache_cad_snapshot 异步入口。"""

    @pytest.mark.asyncio
    async def test_empty_content_short_circuit(self):
        from app.services.cad_cache import cache_cad_snapshot
        # 预置哨兵值：空内容短路应保持哨兵不变（不触碰 sf）
        sf = MagicMock(cad_metadata="sentinel", cad_preview_path="sentinel")
        await cache_cad_snapshot(sf, b"", "empty.dxf")
        assert sf.cad_metadata == "sentinel"
        assert sf.cad_preview_path == "sentinel"

    @pytest.mark.asyncio
    async def test_build_exception_logged(self):
        from app.services import cad_cache
        sf = MagicMock(spec=["id"])
        sf.id = "11111111-1111-1111-1111-111111111111"
        with patch.object(
            cad_cache, "_build_snapshot", side_effect=RuntimeError("boom")
        ):
            # 不应抛异常，仅记告警
            await cad_cache.cache_cad_snapshot(sf, b"data", "a.dxf")
        assert not hasattr(sf, "cad_metadata")

    @pytest.mark.asyncio
    async def test_none_result_leaves_attributes(self):
        from app.services import cad_cache
        sf = MagicMock(spec=["id"])
        sf.id = "22222222-2222-2222-2222-222222222222"
        with patch.object(cad_cache, "_build_snapshot", return_value=None):
            await cad_cache.cache_cad_snapshot(sf, b"data", "a.dxf")
        assert not hasattr(sf, "cad_metadata")

    @pytest.mark.asyncio
    async def test_success_populates_orm_fields(self):
        from app.services import cad_cache
        sf = MagicMock(spec=["id"])
        sf.id = "33333333-3333-3333-3333-333333333333"
        meta = {"info": {"version": "R2018"}}
        with patch.object(
            cad_cache, "_build_snapshot", return_value=(meta, "/tmp/x.svg")
        ):
            await cad_cache.cache_cad_snapshot(sf, b"data", "a.dxf")
        assert sf.cad_metadata == meta
        assert sf.cad_preview_path == "/tmp/x.svg"


class TestBuildSnapshot:
    """_build_snapshot 线程池内构建。"""

    def test_dxf_success(self, tmp_path):
        """真实 DXF 字节走 ezdxf 全链路，产出 meta_dict + 预览（目录存在时）。"""
        from app.services.cad_cache import _build_snapshot
        dxf = (
            "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n"
            "0\nLINE\n8\nWALL\n10\n0\n20\n0\n11\n10\n21\n10\n"
            "0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        with patch.object(__import__("app.services.cad_cache", fromlist=["x"]),
                          "_generate_previews", return_value=None) as gp:
            result = _build_snapshot(dxf, "test.dxf", "abc")
        gp.assert_called_once()
        assert result is not None
        meta_dict, preview = result
        assert preview is None
        assert meta_dict["info"]["version"] in ("R12", "R2000")
        assert meta_dict["entities_total"] >= 1

    def test_unknown_ext_treated_as_dxf(self, tmp_path):
        """非 .dxf/.dwg 后缀按 .dxf 走（suffix='' 场景）。"""
        from app.services.cad_cache import _build_snapshot
        dxf = (
            "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        with patch("app.services.dwg_converter.dwg_to_dxf") as dwg_mock:
            result = _build_snapshot(dxf, "test.bin", "abc")
        assert result is not None
        # 非 dwg 后缀不触发 dwg 转换
        dwg_mock.assert_not_called()

    def test_dwg_success_path(self):
        """.dwg 触发 dwg_to_dxf 转换，成功后用 dxf 解析。"""
        from app.services import cad_cache
        dxf = (
            "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        dxf_path = MagicMock()
        with patch("tempfile.NamedTemporaryFile") as ntf, \
             patch("app.services.dwg_converter.dwg_to_dxf",
                   return_value=dxf_path) as conv, \
             patch("app.services.cad_parser.CADParser._open_doc") as open_doc:
            # NamedTemporaryFile context manager 返回对象 .name
            ntf.return_value.__enter__.return_value.name = "tmp.dwg"
            open_doc.return_value = MagicMock()
            conv.return_value = "/tmp/out.dxf"
            result = cad_cache._build_snapshot(b"DWG", "a.dwg", "abc")
        assert result is not None

    def test_dwg_conversion_failure_returns_none(self):
        """DWG→DXF 转换失败（None）应返回 None 并记告警。"""
        from app.services import cad_cache
        with patch("tempfile.NamedTemporaryFile") as ntf, \
             patch("app.services.dwg_converter.dwg_to_dxf",
                   return_value=None) as conv:
            ntf.return_value.__enter__.return_value.name = "tmp.dwg"
            assert cad_cache._build_snapshot(b"DWG", "a.dwg", "abc") is None
            conv.assert_called_once()

    def test_parser_exception_returns_none(self):
        """解析异常整段兜底返回 None。"""
        from app.services import cad_cache
        with patch("app.services.cad_parser.CADParser._open_doc",
                      side_effect=RuntimeError("corrupt")), \
             patch("tempfile.NamedTemporaryFile") as ntf:
            ntf.return_value.__enter__.return_value.name = "tmp.dxf"
            assert cad_cache._build_snapshot(b"bad", "a.dxf", "abc") is None

    def test_finally_unlinks_temp_files(self):
        """finally 清理临时文件（cad_path 与 extra_tmp），os.unlink 不抛。"""
        from app.services import cad_cache
        from unittest.mock import Mock
        unlinked = []
        fake_unlink = Mock(side_effect=lambda p: unlinked.append(p))
        with patch("tempfile.NamedTemporaryFile") as ntf, \
             patch("app.services.cad_parser.CADParser._open_doc",
                   side_effect=RuntimeError("x")), \
             patch("app.services.cad_cache.os.unlink", fake_unlink):
            ntf.return_value.__enter__.return_value.name = "tmp.dxf"
            cad_cache._build_snapshot(b"bad", "a.dxf", "abc")
        assert unlinked, "finally 应 unlink 临时文件"


class TestGeneratePreviews:
    """_generate_previews 预览生成。"""

    def test_dir_mkdir_failure_returns_none(self):
        from app.services import cad_cache
        with patch.object(cad_cache.Path, "mkdir",
                          side_effect=OSError("no perm")):
            assert cad_cache._generate_previews("/tmp/x.dxf", "abc") is None

    def test_generate_success(self, tmp_path):
        """生成 SVG+PNG 均成功时返回 SVG 路径（存在才返回）。"""
        from app.services import cad_cache
        with patch.object(cad_cache, "settings") as settings, \
             patch("app.services.cad_preview.CADPreviewGenerator") as GenCls:
            settings.upload_dir = str(tmp_path / "uploads")
            gen = GenCls.return_value
            gen.generate_svg.return_value = b"<svg/>"
            gen.generate_png.return_value = b"PNG"
            result = cad_cache._generate_previews("/tmp/x.dxf", "abc")
        assert result is not None
        # 预览目录 = upload_dir 的 parent 下 previews
        assert Path(result).name == "abc.svg"

    def test_generate_exception_swallowed(self, tmp_path):
        """生成抛异常时记警告，svg 不存在返回 None。"""
        from app.services import cad_cache
        with patch.object(cad_cache, "settings") as settings, \
             patch("app.services.cad_preview.CADPreviewGenerator") as GenCls:
            settings.upload_dir = str(tmp_path / "uploads")
            gen = GenCls.return_value
            gen.generate_svg.side_effect = RuntimeError("render fail")
            result = cad_cache._generate_previews("/tmp/x.dxf", "abc")
        assert result is None