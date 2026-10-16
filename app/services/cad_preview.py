# -*- coding: utf-8 -*-
"""CAD 预览生成 — 使用 ezdxf drawing add-on 生成 SVG/PNG 缩略图。

依赖: ezdxf (pip install ezdxf), Pillow (pip install Pillow)
渲染失败时优雅降级为占位图，不中断调用方。
"""
import io
import logging

logger = logging.getLogger(__name__)


class CADPreviewGenerator:
    """CAD 文件预览生成器。"""

    def __init__(self, max_entities: int = 10000):
        self.max_entities = max_entities

    def generate_svg(self, file_path: str, width: int = 400,
                     height: int = 300) -> bytes | None:
        """生成 SVG 预览；文件打不开返回 None，渲染失败返回占位 SVG。"""
        try:
            from ezdxf.addons.drawing import Frontend, RenderContext, layout
            from ezdxf.addons.drawing.svg import SVGBackend

            doc = self._open(file_path)
            if doc is None:
                return None

            backend = SVGBackend()
            # ezdxf ≥1.3: draw_layout(finalize=True) 收尾记录器，再以 Page 取字符串
            Frontend(RenderContext(doc), backend).draw_layout(
                doc.modelspace(), finalize=True)
            page = layout.Page(width, height)
            s = backend.get_string(page)
            raw = s.encode("utf-8") if isinstance(s, str) else s
            # 按模型包围盒做 viewBox fit：避免真实图形(~几百单位)被塞进
            # ezdxf 默认 1000000x750000 超大页面的左下角，导致"预览只见
            # 无意义线/点"。算出 bbox 后重写 <svg viewBox> 为居中+等比缩放。
            fitted = self._fit_svg_viewbox(raw, doc, width, height)
            return fitted if fitted is not None else raw
        except Exception as e:
            logger.warning("SVG 预览失败 %s: %s", file_path, e)
            return self._fallback_svg(width, height)

    def _fit_svg_viewbox(self, raw: bytes, doc, width: int,
                         height: int) -> bytes | None:
        """按模型包围盒重写 SVG 根 viewBox（居中 + 等比 fit）。

        返回重写后的 SVG；无法计算 bbox / viewBox 已合理 / 转换失败时
        返回 None（调用方沿用原始字符串）。
        """
        try:
            import re
            import ezdxf.bbox as ez_bbox

            ext = ez_bbox.extents(doc.modelspace())
            if not ext.has_data:
                return None
            minx, maxx = ext.extmin[0], ext.extmax[0]
            miny, maxy = ext.extmin[1], ext.extmax[1]
            data_w = maxx - minx or 1.0
            data_h = maxy - miny or 1.0
            margin = 0.04  # 4% 边距
            avail_w = width * (1 - 2 * margin)
            avail_h = height * (1 - 2 * margin)
            scale = min(avail_w / data_w, avail_h / data_h)
            vw = data_w * scale
            vh = data_h * scale

            def _fix(m: "re.Match") -> str:
                attrs = m.group(1)
                attrs = re.sub(
                    r'viewBox="[^"]*"',
                    'viewBox="%.6g %.6g %.6g %.6g"' % (
                        minx - margin * data_w, miny - margin * data_h,
                        vw, vh),
                    attrs,
                )
                if "preserveAspectRatio" not in attrs:
                    attrs += ' preserveAspectRatio="xMidYMid meet"'
                return "<svg " + attrs + ">"

            text = raw.decode("utf-8", errors="replace")
            text = re.sub(r"<svg\b([^>]*)>", _fix, text, count=1)
            return text.encode("utf-8")
        except Exception:
            return None

    def generate_png(self, file_path: str, width: int = 400,
                     height: int = 300) -> bytes | None:
        """生成 PNG 缩略图；文件打不开返回 None，渲染失败返回占位 PNG。

        使用 Pillow 直接绘制 DXF 实体（不依赖 matplotlib）。
        """
        try:
            doc = self._open(file_path)
            if doc is None:
                return None
            img = self._render_pillow(doc, width, height)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.warning("PNG 预览失败 %s: %s", file_path, e)
            return self._fallback_png(width, height)

    def generate_thumbnail(self, file_path: str) -> bytes | None:
        """生成 200x150 缩略图（PNG）。"""
        return self.generate_png(file_path, 200, 150)

    # ── 内部 ──

    def _open(self, file_path: str):
        """打开 DXF，失败返回 None。"""
        import ezdxf
        try:
            return ezdxf.readfile(file_path)
        except Exception as e:
            logger.warning("DXF 打开失败 %s: %s", file_path, e)
            return None

    def _render_pillow(self, doc, width: int, height: int):
        """用 Pillow 从 ezdxf 文档直接绘制 PNG（简单实体渲染）。"""
        from PIL import Image, ImageDraw
        import math

        msp = doc.modelspace()
        img = Image.new("RGB", (width, height), "#f0f0f0")
        draw = ImageDraw.Draw(img)

        # 1) 收集所有坐标，算 bounding box
        points: list[tuple[float, float]] = []
        for ent in msp:
            if hasattr(ent.dxf, "start"):
                points.append(tuple(ent.dxf.start)[:2])
            if hasattr(ent.dxf, "end"):
                points.append(tuple(ent.dxf.end)[:2])
            if hasattr(ent.dxf, "insert"):
                points.append(tuple(ent.dxf.insert)[:2])
            if hasattr(ent.dxf, "center"):
                points.append(tuple(ent.dxf.center)[:2])

        if not points:
            return img

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        ext_x = max_x - min_x or 1.0
        ext_y = max_y - min_y or 1.0
        margin = 10

        scale_x = (width - 2 * margin) / ext_x
        scale_y = (height - 2 * margin) / ext_y
        scale = min(scale_x, scale_y)
        offset_x = margin + (width - 2 * margin - ext_x * scale) / 2
        offset_y = margin + (height - 2 * margin - ext_y * scale) / 2

        def tx(x: float) -> float:
            return offset_x + (x - min_x) * scale

        def ty(y: float) -> float:
            return offset_y + (max_y - y) * scale  # Y 轴翻转

        # 2) 绘制实体
        _LINE_COLORS = [
            (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 165, 0), (128, 0, 128), (0, 128, 128), (128, 128, 128),
        ]

        def _layer_color(layer_name: str) -> tuple[int, int, int]:
            for i, layer in enumerate(doc.layers):
                if layer.dxf.name == layer_name:
                    ci = layer.dxf.get("color", 7)
                    return _LINE_COLORS[ci % len(_LINE_COLORS)] if ci < len(_LINE_COLORS) else (0, 0, 0)
            return (0, 0, 0)

        # ezdxf C 加速 Vec3 不支持 slice（center[:2] 报 "an integer is required"），
        # 统一用 .x/.y 属性取二维坐标
        def _xy(v) -> tuple[float, float]:
            return (float(v.x), float(v.y))

        for ent in msp:
            color = _layer_color(ent.dxf.layer)
            etype = ent.dxftype()

            if etype == "LINE":
                sp = ent.dxf.start
                ep = ent.dxf.end
                draw.line([tx(sp.x), ty(sp.y), tx(ep.x), ty(ep.y)],
                          fill=color, width=1)

            elif etype == "LWPOLYLINE":
                pts = [(tx(p[0]), ty(p[1])) for p in ent.get_points("xy")]
                if len(pts) >= 2:
                    draw.line(pts, fill=color, width=1)

            elif etype == "CIRCLE":
                cx, cy = _xy(ent.dxf.center)
                r = ent.dxf.radius
                draw.ellipse([tx(cx - r), ty(cy + r), tx(cx + r), ty(cy - r)],
                             outline=color, width=1)

            elif etype == "ARC":
                cx, cy = _xy(ent.dxf.center)
                r = ent.dxf.radius
                sa = math.radians(ent.dxf.start_angle)
                ea = math.radians(ent.dxf.end_angle)
                steps = max(int(abs(ea - sa) / math.radians(5)), 10)
                arc_pts = []
                for i in range(steps + 1):
                    a = sa + (ea - sa) * i / steps
                    arc_pts.append((tx(cx + r * math.cos(a)), ty(cy + r * math.sin(a))))
                if len(arc_pts) >= 2:
                    draw.line(arc_pts, fill=color, width=1)

            elif etype in ("TEXT", "MTEXT"):
                try:
                    t = ent.dxf.text[:30]
                    pos = _xy(ent.dxf.insert)
                    draw.text((tx(pos[0]), ty(pos[1])), t, fill=color)
                except Exception:
                    pass

            elif etype == "ELLIPSE":
                try:
                    cx, cy = _xy(ent.dxf.center)
                    ma = (ent.dxf.major_axis.x, ent.dxf.major_axis.y)
                    ratio = ent.dxf.ratio
                    r1 = math.sqrt(ma[0] ** 2 + ma[1] ** 2)
                    r2 = r1 * ratio
                    draw.ellipse([tx(cx - r1), ty(cy + r2), tx(cx + r1), ty(cy - r2)],
                                 outline=color, width=1)
                except Exception:
                    pass

        return img

    def _fallback_svg(self, width: int, height: int) -> bytes:
        """渲染失败占位 SVG。"""
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<rect width="100%" height="100%" fill="#f0f0f0"/>'
            f'<text x="50%" y="50%" text-anchor="middle" fill="#999" font-size="14">'
            f'Preview unavailable</text></svg>'
        )
        return svg.encode("utf-8")

    def _fallback_png(self, width: int, height: int) -> bytes:
        """渲染失败占位 PNG；Pillow 不可用时返回 1x1 透明 PNG。"""
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (width, height), "#f0f0f0")
            draw = ImageDraw.Draw(img)
            draw.text((width // 2, height // 2), "Preview\nunavailable",
                      fill="#999", anchor="mm")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return self._MINIMAL_PNG

    # 1x1 透明 PNG
    _MINIMAL_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8\x4c\x00\x00\x00\x00IEND\xaeB`\x82"
    )