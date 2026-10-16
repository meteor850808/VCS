# -*- coding: utf-8 -*-
"""真实 DWG 全链路回归测试。

用一份真实 LibreDWG 产物 DWG（tests/fixtures/cad/real_sample.dwg）跑完整链路：
  real *.dwg -> LibreDWG dwg2dxf -> DXF -> CADParser.parse_file -> CADPreviewGenerator 出 SVG/PNG

这覆盖了以往只有合成 .dxf fixture 时覆盖不到的“真实 DWG → 转换 → 渲染”回归缺口
（历史根因：展示页面对 DWG 空白，因为 LibreDWG 转换链路从未用真实 DWG 验证过）。
"""
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

# 允许直接运行（python -m pytest 或 python tests/xx.py 均可）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL_DWG = ROOT / "tests" / "fixtures" / "cad" / "real_sample.dwg"

pytestmark = pytest.mark.skipif(
    not REAL_DWG.exists(),
    reason="real_sample.dwg fixture 不存在（含专有真实数据，可选拷贝）",
)


@pytest.fixture(scope="module")
def real_dwg():
    return str(REAL_DWG)


@pytest.fixture(scope="module")
def real_dxf(real_dwg):
    """真实 DWG -> LibreDWG dwg2dxf -> DXF（返回 dxf 路径字符串）。"""
    from app.services.dwg_converter import dwg_to_dxf, cleanup_converted_dxf

    dxf = dwg_to_dxf(real_dwg)
    assert dxf is not None, "真实 DWG 未转换出 DXF（LibreDWG 链路失败）"
    p = Path(dxf)
    assert p.exists() and p.stat().st_size > 0
    yield str(p)
    cleanup_converted_dxf(str(p))


class TestRealDWGChain:
    """真实 DWG 端到端：转换 -> 解析 -> 渲染。"""

    def test_dwg_to_dxf_produces_nonempty(self, real_dxf):
        assert Path(real_dxf).stat().st_size > 100_000, "DXF 产物过小，疑似实体丢失"

    def test_parser_parses_converted_dxf(self, real_dxf):
        from app.services.cad_parser import CADParser

        doc = CADParser().parse_file(real_dxf)
        assert doc is not None

    def test_preview_svg_nonempty(self, real_dxf):
        from app.services.cad_preview import CADPreviewGenerator

        svg = CADPreviewGenerator().generate_svg(real_dxf)
        assert svg is not None and len(svg) > 0

    def test_preview_png_is_real_png(self, real_dxf):
        from app.services.cad_preview import CADPreviewGenerator

        png = CADPreviewGenerator().generate_png(real_dxf)
        assert png is not None
        assert png[:4] == b"\x89PNG", "产物不是合法 PNG"
