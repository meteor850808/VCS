# -*- coding: utf-8 -*-
"""CAD 解析服务 — 基于 ezdxf 的 DXF 深度解析。

提供 DXF 文件的结构化数据提取：图层树、实体列表、文本内容、元数据。
DWG 文件需先通过 dwg_converter 转为 DXF 再调用本模块。

依赖: ezdxf (pip install ezdxf)
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LayerInfo:
    """图层信息。"""
    name: str
    color_index: int
    color_rgb: tuple[int, int, int] | None
    linetype: str
    is_visible: bool
    is_frozen: bool
    is_locked: bool
    entity_count: int
    geometry_type: str  # point/line/polygon/text/unknown/mixed


@dataclass
class EntityInfo:
    """实体信息。"""
    handle: str
    entity_type: str
    layer: str
    color_index: int
    start_point: tuple[float, float] | None = None
    end_point: tuple[float, float] | None = None
    length: float | None = None
    area: float | None = None
    text_content: str | None = None


@dataclass
class TextEntry:
    """文本条目。"""
    text: str
    layer: str
    handle: str
    position: tuple[float, float]


@dataclass
class EntityPage:
    """分页实体结果。"""
    total: int
    offset: int
    limit: int
    entities: list[EntityInfo] = field(default_factory=list)


@dataclass
class CADMetadata:
    """CAD 文件元数据。"""
    version: str
    units: str
    extent_min: tuple[float, float]
    extent_max: tuple[float, float]
    total_entities: int
    entity_type_counts: dict[str, int]
    layer_count: int
    has_text: bool
    has_dimensions: bool
    has_blocks: bool
    has_hatches: bool


@dataclass
class CADDocument:
    """CAD 解析结果。"""
    metadata: CADMetadata
    layers: list[LayerInfo]
    doc: object  # ezdxf Document，保留引用供后续查询


# DXF ACI 颜色表（索引 1-9 标准色 + 扩展色粗略映射）
_ACI_COLORS: dict[int, tuple[int, int, int]] = {
    1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0),
    4: (0, 255, 255), 5: (0, 0, 255), 6: (255, 0, 255),
    7: (255, 255, 255), 8: (128, 128, 128), 9: (192, 192, 192),
}


def aci_to_rgb(color_index: int) -> tuple[int, int, int] | None:
    """将 DXF ACI 颜色索引转为 RGB。"""
    if color_index in _ACI_COLORS:
        return _ACI_COLORS[color_index]
    if 10 <= color_index <= 249:
        r = ((color_index - 10) // 40) * 80
        g = (((color_index - 10) % 40) // 10) * 80
        b = ((color_index - 10) % 10) * 25
        return (min(r, 255), min(g, 255), min(b, 255))
    return None


# 实体类型 → 几何类型映射
_ENTITY_GEOM_MAP = {
    "POINT": "point",
    "TEXT": "text", "MTEXT": "text",
    "DIMENSION": "text", "LEADER": "text", "MLEADER": "text",
    "LINE": "line", "LWPOLYLINE": "line", "POLYLINE": "line",
    "ARC": "line", "CIRCLE": "line", "ELLIPSE": "line", "SPLINE": "line",
    "HATCH": "polygon", "SOLID": "polygon", "3DFACE": "polygon",
    "INSERT": "unknown", "BLOCK": "unknown",
}


# DXF 版本编码 → 显示名映射
_DXF_VERSION_NAMES = {
    "AC1009": "R12", "AC1012": "R13", "AC1014": "R14",
    "AC1015": "R2000", "AC1018": "R2004", "AC1021": "R2007",
    "AC1024": "R2010", "AC1027": "R2013", "AC1032": "R2018",
}

# 跳过内部/默认图层（ezdxf 添加 TEXT 时会自动创建 Defpoints）
_SKIP_LAYERS = {"0", "Defpoints"}


class CADParser:
    """基于 ezdxf 的 CAD 解析器。"""

    def _open_doc(self, file_path: str):
        """打开 DXF 文件。"""
        import ezdxf
        return ezdxf.readfile(file_path)

    def parse_file(self, file_path: str) -> CADDocument | None:
        """解析 DXF 文件，返回 CADDocument；失败返回 None。"""
        try:
            doc = self._open_doc(file_path)
            layers = self.extract_layer_tree(doc)
            metadata = self.extract_metadata(doc)
            return CADDocument(metadata=metadata, layers=layers, doc=doc)
        except Exception as e:
            logger.warning("DXF 解析失败 %s: %s", file_path, e)
            return None

    def extract_layer_tree(self, doc) -> list[LayerInfo]:
        """提取图层树：名称、颜色、线型、可见性、实体统计。"""
        layer_entity_counts: dict[str, int] = {}
        layer_geom_types: dict[str, set[str]] = {}

        for entity in doc.modelspace():
            ln = entity.dxf.layer
            layer_entity_counts[ln] = layer_entity_counts.get(ln, 0) + 1
            geom = _ENTITY_GEOM_MAP.get(entity.dxftype(), "unknown")
            layer_geom_types.setdefault(ln, set()).add(geom)

        result = []
        for layer in doc.layers:
            name = layer.dxf.name
            if name in _SKIP_LAYERS:  # 跳过默认图层 0 与内部 Defpoints
                continue

            ci = layer.dxf.get("color", 7)
            geom_set = layer_geom_types.get(name, set())
            if len(geom_set) == 0:
                geometry_type = "unknown"
            elif len(geom_set) == 1:
                geometry_type = geom_set.pop()
            else:
                geometry_type = "mixed"

            result.append(LayerInfo(
                name=name, color_index=ci, color_rgb=aci_to_rgb(ci),
                linetype=layer.dxf.get("linetype", "Continuous"),
                is_visible=not layer.is_off(), is_frozen=layer.is_frozen(),
                is_locked=layer.is_locked(),
                entity_count=layer_entity_counts.get(name, 0),
                geometry_type=geometry_type,
            ))

        return sorted(result, key=lambda l: l.name)

    def extract_entities(self, doc, layer_filter: str | None = None,
                         entity_type_filter: str | None = None,
                         offset: int = 0, limit: int = 50) -> EntityPage:
        """提取实体列表（分页），支持图层/类型筛选。"""
        entities = []
        total = 0

        for entity in doc.modelspace():
            if layer_filter and entity.dxf.layer != layer_filter:
                continue
            if entity_type_filter and entity.dxftype() != entity_type_filter:
                continue

            total += 1
            if total <= offset or len(entities) >= limit:
                continue

            info = self._entity_to_info(entity)
            if info:
                entities.append(info)

        return EntityPage(total=total, offset=offset, limit=limit,
                          entities=entities)

    def _entity_to_info(self, entity) -> EntityInfo | None:
        """将 ezdxf 实体转为 EntityInfo。"""
        try:
            etype = entity.dxftype()
            sp, ep = None, None

            if hasattr(entity.dxf, "start"):
                sp = tuple(entity.dxf.start)[:2]
            if hasattr(entity.dxf, "end"):
                ep = tuple(entity.dxf.end)[:2]
            if hasattr(entity.dxf, "insert") and sp is None:
                sp = tuple(entity.dxf.insert)[:2]

            text_content = None
            if etype in ("TEXT", "MTEXT"):
                text_content = entity.dxf.get("text", "")
            elif etype == "DIMENSION":
                try:
                    text_content = entity.get_text()
                except Exception:
                    pass

            length = None
            if sp and ep:
                length = ((ep[0] - sp[0]) ** 2 + (ep[1] - sp[1]) ** 2) ** 0.5

            return EntityInfo(
                handle=entity.dxf.get("handle", ""),
                entity_type=etype,
                layer=entity.dxf.layer,
                color_index=entity.dxf.get("color", 7),
                start_point=sp, end_point=ep, length=length,
                text_content=text_content,
            )
        except Exception:
            return None

    def extract_text_content(self, doc) -> list[TextEntry]:
        """提取所有文本内容（TEXT/MTEXT）。"""
        results = []
        for entity in doc.modelspace():
            if entity.dxftype() in ("TEXT", "MTEXT"):
                results.append(TextEntry(
                    text=entity.dxf.get("text", ""),
                    layer=entity.dxf.layer,
                    handle=entity.dxf.get("handle", ""),
                    position=tuple(entity.dxf.insert)[:2],
                ))
        return results

    def extract_metadata(self, doc) -> CADMetadata:
        """提取 CAD 元数据。"""
        counts: dict[str, int] = {}
        total = 0
        has_text = has_dimensions = has_blocks = has_hatches = False

        for entity in doc.modelspace():
            total += 1
            et = entity.dxftype()
            counts[et] = counts.get(et, 0) + 1
            if et in ("TEXT", "MTEXT"):
                has_text = True
            elif et == "DIMENSION":
                has_dimensions = True
            elif et == "INSERT":
                has_blocks = True
            elif et == "HATCH":
                has_hatches = True

        version = _DXF_VERSION_NAMES.get(doc.dxfversion or "", "R2018")
        try:
            units = doc.units().get("insunits", "Unitless")
        except Exception:
            units = "Unitless"

        try:
            emin = tuple(doc.header.get("$EXTMIN", (0, 0)))[:2]
            emax = tuple(doc.header.get("$EXTMAX", (100, 100)))[:2]
            extent_min = (float(emin[0]), float(emin[1]))
            extent_max = (float(emax[0]), float(emax[1]))
        except Exception:
            extent_min, extent_max = (0.0, 0.0), (100.0, 100.0)

        layer_count = len([l for l in doc.layers if l.dxf.name not in _SKIP_LAYERS])

        return CADMetadata(
            version=version, units=units,
            extent_min=extent_min, extent_max=extent_max,
            total_entities=total, entity_type_counts=counts,
            layer_count=layer_count, has_text=has_text,
            has_dimensions=has_dimensions, has_blocks=has_blocks,
            has_hatches=has_hatches,
        )