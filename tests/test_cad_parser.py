# -*- coding: utf-8 -*-
"""cad_parser 单元测试。"""
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cad"


class TestCADParserDataModels:
    """测试数据模型。"""

    def test_layer_info_fields(self):
        from app.services.cad_parser import LayerInfo
        layer = LayerInfo("Wall", 1, (255, 0, 0), "Continuous", True, False,
                          False, 10, "line")
        assert layer.name == "Wall"
        assert layer.color_index == 1
        assert layer.color_rgb == (255, 0, 0)
        assert layer.entity_count == 10
        assert layer.geometry_type == "line"

    def test_entity_info_fields(self):
        from app.services.cad_parser import EntityInfo
        entity = EntityInfo("1A2B", "LINE", "Wall", 1,
                            (0.0, 0.0), (100.0, 0.0), 100.0)
        assert entity.handle == "1A2B"
        assert entity.entity_type == "LINE"
        assert entity.length == 100.0

    def test_cad_metadata_fields(self):
        from app.services.cad_parser import CADMetadata
        meta = CADMetadata("R2018", "Unitless", (0, 0), (100, 80), 5,
                           {"LINE": 4, "TEXT": 1}, 3, True, False, False, False)
        assert meta.version == "R2018"
        assert meta.total_entities == 5
        assert meta.has_text is True

    def test_aci_to_rgb(self):
        from app.services.cad_parser import aci_to_rgb
        assert aci_to_rgb(1) == (255, 0, 0)
        assert aci_to_rgb(7) == (255, 255, 255)
        assert aci_to_rgb(0) is None


class TestCADParserParse:
    """测试解析功能。"""

    def test_parse_simple_dxf(self):
        from app.services.cad_parser import CADParser
        result = CADParser().parse_file(str(FIXTURES / "simple.dxf"))
        assert result is not None
        assert result.metadata.total_entities > 0
        assert result.metadata.layer_count == 3

    def test_extract_layer_tree(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "simple.dxf"))
        layers = parser.extract_layer_tree(doc)
        names = {l.name for l in layers}
        assert "Wall" in names
        assert "Door" in names
        assert "Window" in names
        # Wall 层应有 4 条线 + 1 文本
        wall = [l for l in layers if l.name == "Wall"][0]
        assert wall.entity_count == 5
        assert wall.color_index == 1

    def test_extract_entities(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "simple.dxf"))
        page = parser.extract_entities(doc, offset=0, limit=100)
        assert page.total > 0
        assert len(page.entities) > 0

    def test_extract_entities_by_layer(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "simple.dxf"))
        page = parser.extract_entities(doc, layer_filter="Wall")
        for e in page.entities:
            assert e.layer == "Wall"

    def test_extract_text_content(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "simple.dxf"))
        texts = parser.extract_text_content(doc)
        assert len(texts) >= 1
        assert any("Room A" in t.text for t in texts)

    def test_extract_metadata(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "simple.dxf"))
        meta = parser.extract_metadata(doc)
        assert meta.version == "R2018"
        assert meta.has_text is True
        assert meta.has_dimensions is False
        assert meta.has_hatches is False

    def test_parse_nonexistent_file(self):
        from app.services.cad_parser import CADParser
        assert CADParser().parse_file("/nonexistent/file.dxf") is None

    def test_parse_complex_dxf(self):
        from app.services.cad_parser import CADParser
        result = CADParser().parse_file(str(FIXTURES / "complex.dxf"))
        assert result is not None
        assert result.metadata.total_entities > 50
        assert result.metadata.has_text is True
        assert result.metadata.layer_count == 5

    def test_extract_entities_pagination(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "complex.dxf"))
        page1 = parser.extract_entities(doc, offset=0, limit=5)
        page2 = parser.extract_entities(doc, offset=5, limit=5)
        assert page1.total == page2.total
        assert len(page1.entities) == 5
        assert page1.entities[0].handle != page2.entities[0].handle

    def test_extract_entities_type_filter(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "complex.dxf"))
        page = parser.extract_entities(doc, entity_type_filter="LINE")
        for e in page.entities:
            assert e.entity_type == "LINE"
        assert page.total >= 50  # 50 MEP lines + 1 Architecture line

    def test_extract_layer_tree_complex(self):
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = parser._open_doc(str(FIXTURES / "complex.dxf"))
        layers = parser.extract_layer_tree(doc)
        assert len(layers) == 5
        mep = [l for l in layers if l.name == "MEP"][0]
        assert mep.entity_count == 50
        assert mep.geometry_type == "line"


class TestCADParserBranchCoverage:
    """覆盖 cad_parser 剩余分支（P7 覆盖回跌收口）。"""

    def test_aci_to_rgb_midrange_formula(self):
        from app.services.cad_parser import aci_to_rgb
        # 10..249 走公式分支：min(r,255) 兜底
        c10 = aci_to_rgb(10)
        assert c10 == (0, 0, 0)
        c249 = aci_to_rgb(249)
        assert c249[0] <= 255 and c249[1] <= 255 and c249[2] <= 255
        c_big = aci_to_rgb(59)
        assert c_big[0] == 80 and c_big[2] == 225
        # 越界返回 None
        assert aci_to_rgb(250) is None
        assert aci_to_rgb(1000) is None

    def _make_entity(self, dtype, layer="W", start=None, end=None,
                     insert=None, text=None, raise_text=False):
        from unittest.mock import MagicMock
        ent = MagicMock()
        ent.dxftype.return_value = dtype
        ent.dxf.layer = layer
        ent.dxf.get.side_effect = lambda k, d=None: d
        if start is not None:
            ent.dxf.start = start
        else:
            del ent.dxf.start
        if end is not None:
            ent.dxf.end = end
        else:
            del ent.dxf.end
        if insert is not None:
            ent.dxf.insert = insert
        if raise_text:
            ent.get_text.side_effect = RuntimeError("bad text")
        return ent

    def test_extract_metadata_detects_dim_insert_hatch(self):
        """DIMENSION/INSERT/HATCH 实体应分别置位 has_dimensions/has_blocks/has_hatches。"""
        from unittest.mock import MagicMock
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = MagicMock()
        doc.modelspace.return_value = [
            self._make_entity("DIMENSION", insert=(1.0, 2.0)),
            self._make_entity("INSERT", insert=(3.0, 4.0)),
            self._make_entity("HATCH"),
            self._make_entity("LINE", start=(0, 0), end=(10, 10)),
        ]
        doc.dxfversion = "AC1032"
        doc.units.return_value = {"insunits": "mm"}
        doc.header.get.return_value = (0, 0, 0)
        doc.layers = []
        meta = parser.extract_metadata(doc)
        assert meta.has_dimensions is True
        assert meta.has_blocks is True
        assert meta.has_hatches is True
        assert meta.total_entities == 4

    def test_extract_metadata_extent_fallback(self):
        """header 读取抛异常时 extent 回退 (0,0)/(100,100)。"""
        from unittest.mock import MagicMock
        from app.services.cad_parser import CADParser
        parser = CADParser()
        doc = MagicMock()
        doc.modelspace.return_value = [
            self._make_entity("LINE", start=(0, 0), end=(10, 10)),
        ]
        doc.dxfversion = "AC1032"
        doc.units.return_value = {"insunits": "mm"}
        doc.header.get.side_effect = RuntimeError("bad header")
        doc.layers = []
        meta = parser.extract_metadata(doc)
        assert meta.extent_min == (0.0, 0.0)
        assert meta.extent_max == (100.0, 100.0)

    def test_entity_to_info_dimension_text_exception(self):
        """DIMENSION 实体 get_text 抛异常应吞掉，text_content=None，不抛。"""
        from unittest.mock import MagicMock
        from app.services.cad_parser import CADParser
        parser = CADParser()
        ent = self._make_entity("DIMENSION", insert=(1.0, 2.0),
                                raise_text=True)
        info = parser._entity_to_info(ent)
        assert info is not None
        assert info.text_content is None

    def test_entity_to_info_exception_returns_none(self):
        """实体属性访问异常应返回 None（_entity_to_info 整段兜底）。"""
        from unittest.mock import MagicMock
        from app.services.cad_parser import CADParser
        parser = CADParser()
        ent = MagicMock()
        ent.dxftype.side_effect = RuntimeError("corrupt entity")
        assert parser._entity_to_info(ent) is None