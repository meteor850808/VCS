# -*- coding: utf-8 -*-
"""cad_preview 单元测试。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "cad"


class MockVec3:
    """最小 Vec3 近似（.x/.y/.z 属性 + 可迭代）。"""
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class TestCADPreview:
    """测试预览生成。"""

    def test_generate_svg(self):
        from app.services.cad_preview import CADPreviewGenerator
        svg = CADPreviewGenerator().generate_svg(str(FIXTURES / "simple.dxf"))
        assert svg is not None
        assert b"<svg" in svg

    def test_generate_png(self):
        from app.services.cad_preview import CADPreviewGenerator
        png = CADPreviewGenerator().generate_png(str(FIXTURES / "simple.dxf"))
        assert png is not None
        assert png[:4] == b"\x89PNG"

    def test_generate_thumbnail(self):
        from app.services.cad_preview import CADPreviewGenerator
        thumb = CADPreviewGenerator().generate_thumbnail(str(FIXTURES / "simple.dxf"))
        assert thumb is not None
        assert len(thumb) > 0

    def test_nonexistent_file_svg(self):
        from app.services.cad_preview import CADPreviewGenerator
        assert CADPreviewGenerator().generate_svg("/no/such/file.dxf") is None

    def test_nonexistent_file_png(self):
        from app.services.cad_preview import CADPreviewGenerator
        assert CADPreviewGenerator().generate_png("/no/such/file.dxf") is None

    def test_complex_svg(self):
        from app.services.cad_preview import CADPreviewGenerator
        svg = CADPreviewGenerator().generate_svg(str(FIXTURES / "complex.dxf"))
        assert svg is not None
        assert b"<svg" in svg

    def test_complex_png(self):
        from app.services.cad_preview import CADPreviewGenerator
        png = CADPreviewGenerator().generate_png(str(FIXTURES / "complex.dxf"))
        assert png is not None
        assert png[:4] == b"\x89PNG"


class TestCADPreviewFallback:
    """补测 cad_preview 异常降级与 edge 分支（P7 覆盖回跌收口）。"""

    def test_generate_svg_render_failure_fallback(self):
        """内部渲染抛异常→告警→占位 SVG。"""
        from unittest.mock import MagicMock, patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        # 让 _open 成功但 ezdxf drawing 抛异常
        doc = MagicMock()
        with patch.object(gen, "_open", return_value=doc), \
             patch("ezdxf.addons.drawing.Frontend",
                   side_effect=RuntimeError("render boom")):
            svg = gen.generate_svg("/tmp/x.dxf")
        assert svg is not None
        assert b"Preview unavailable" in svg

    def test_generate_png_render_failure_fallback(self):
        """_render_pillow 抛异常→占位 PNG。"""
        from unittest.mock import MagicMock, patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        doc = MagicMock()
        with patch.object(gen, "_open", return_value=doc), \
             patch.object(gen, "_render_pillow",
                          side_effect=RuntimeError("pillow boom")):
            png = gen.generate_png("/tmp/x.dxf")
        assert png is not None
        assert png[:4] == b"\x89PNG"

    def test_fit_svg_viewbox_no_bbox_data(self):
        """bbox 无数据（extents.has_data=False）→ None，沿用原始 SVG。"""
        from unittest.mock import MagicMock, patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        doc = MagicMock()
        doc.modelspace.return_value = []
        with patch("ezdxf.bbox.extents") as ext_cls:
            ext_cls.return_value.has_data = False
            assert gen._fit_svg_viewbox(b"<svg>", doc, 400, 300) is None

    def test_fit_svg_viewbox_exception_returns_none(self):
        """bbox 计算抛异常→None 兜底。"""
        from unittest.mock import MagicMock, patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        doc = MagicMock()
        with patch("ezdxf.bbox.extents",
                   side_effect=RuntimeError("bbox boom")):
            assert gen._fit_svg_viewbox(b"<svg>", doc, 400, 300) is None

    def test_fit_svg_viewbox_success_rewrites_viewbox(self):
        """bbox 有数据→重写 viewBox + preserveAspectRatio。"""
        from unittest.mock import MagicMock, patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        doc = MagicMock()

        fake_ext = MagicMock()
        fake_ext.has_data = True
        # 模拟 bbox 对象: extmin/extmax 是 Vec3-like，含 3 分量
        fake_ext.extmin = (0.0, 0.0, 0.0)
        fake_ext.extmax = (100.0, 50.0, 0.0)
        with patch("ezdxf.bbox.extents", return_value=fake_ext):
            raw = b'<svg viewBox="0 0 1000000 750000"><rect/></svg>'
            out = gen._fit_svg_viewbox(raw, doc, 400, 300)
        assert out is not None
        assert b"viewBox=" in out
        assert b"preserveAspectRatio" in out

    def test_layer_color_index_out_of_palette(self):
        """图层颜色索引超出 8 色板→(0,0,0)。需要构造 color≥8 的图层。"""
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        layer = MagicMock()
        layer.dxf.name = "EXOTIC"
        layer.dxf.get.return_value = 12  # 超出 8 色
        doc = MagicMock()
        doc.layers = [layer]

        # 构造一个 LINE 实体（SimpleNamespace 确保 hasattr 准确）
        ent = SimpleNamespace(
            dxf=SimpleNamespace(layer="EXOTIC",
                                start=MockVec3(0, 0),
                                end=MockVec3(10, 10)),
        )
        ent.dxftype = lambda: "LINE"
        doc = MagicMock()
        doc.modelspace.return_value = [ent]

        img = gen._render_pillow(doc, 100, 80)
        assert img is not None

    def test_render_pillow_no_points_returns_img(self):
        """modelspace 实体无坐标点→直接返回空白 img（不报错）。"""
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        ent = SimpleNamespace(dxf=SimpleNamespace())
        ent.dxftype = lambda: "POINT"  # 无 start/end/insert/center
        doc = MagicMock()
        doc.modelspace.return_value = [ent]
        img = gen._render_pillow(doc, 100, 80)
        assert img is not None

    def test_render_pillow_text_exception_swallowed(self):
        """TEXT/MTEXT draw.text 抛异常被吞（except→pass）。"""
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        layer = MagicMock()
        layer.dxf.name = "T"
        layer.dxf.get.return_value = 7
        doc = MagicMock()
        doc.layers = [layer]

        ent = SimpleNamespace(
            dxf=SimpleNamespace(layer="T", text="LABEL",
                                insert=MockVec3(1, 1)),
        )
        ent.dxftype = lambda: "TEXT"
        doc2 = MagicMock()
        doc2.modelspace.return_value = [ent]

        with patch("PIL.ImageDraw.ImageDraw.text",
                   side_effect=RuntimeError("font fail")):
            img = gen._render_pillow(doc2, 100, 80)
        assert img is not None

    def test_render_pillow_ellipse_exception_swallowed(self):
        """ELLIPSE 计算/绘制异常被吞。"""
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        layer = MagicMock()
        layer.dxf.name = "E"
        layer.dxf.get.return_value = 7
        doc = MagicMock()
        doc.layers = [layer]

        ent = SimpleNamespace(
            dxf=SimpleNamespace(layer="E", center=MockVec3(5, 5),
                                major_axis=MockVec3(10, 0), ratio=0.5),
        )
        ent.dxftype = lambda: "ELLIPSE"
        doc2 = MagicMock()
        doc2.modelspace.return_value = [ent]

        with patch("PIL.ImageDraw.ImageDraw.ellipse",
                   side_effect=RuntimeError("no ellipse")):
            img = gen._render_pillow(doc2, 100, 80)
        assert img is not None

    def test_fallback_png_unavailable_pillow(self):
        """Pillow import/使用失败→返回 _MINIMAL_PNG 1x1。"""
        from unittest.mock import patch
        from app.services.cad_preview import CADPreviewGenerator
        gen = CADPreviewGenerator()
        with patch("PIL.Image.new", side_effect=RuntimeError("no PIL")):
            assert gen._fallback_png(100, 80) == gen._MINIMAL_PNG

    def test_fallback_svg(self):
        """占位 SVG 内容正确。"""
        from app.services.cad_preview import CADPreviewGenerator
        svg = CADPreviewGenerator()._fallback_svg(400, 300)
        assert b"Preview unavailable" in svg
        assert b'width="400"' in svg

    def test_fallback_png(self):
        """正常 Pillow 下返回网络 PNG。"""
        from app.services.cad_preview import CADPreviewGenerator
        png = CADPreviewGenerator()._fallback_png(200, 150)
        assert png[:4] == b"\x89PNG"