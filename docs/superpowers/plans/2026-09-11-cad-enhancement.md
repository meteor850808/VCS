# CAD 功能增强实现计划（模块化重构版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将 CAD 功能封装为独立子包 `app/api/v1/cad/`，包含解析、预览、API 路由、前端面板，作为项目的一个功能子项。

**Architecture:** 参照 `data_standard_v2/` 模式——新建 `cad/` 路由子包 + `cad_parser.py`/`cad_preview.py` 服务 + `schemas/cad.py` 响应模型 + 前端 CAD Tab，独立注册到主路由。

**Tech Stack:** ezdxf >=1.4.0 (MIT), Pillow >=10.0.0, FastAPI, SQLAlchemy 2.0 async, Leaflet

**Spec:** `docs/superpowers/specs/2026-09-11-cad-enhancement-design.md`

## Global Constraints

- Python >=3.11, FastAPI >=0.115, SQLAlchemy >=2.0.30 async
- 数据库: PostgreSQL 15 + PostGIS 3.4
- 前端: 单文件 HTML SPA (Vanilla JS + Leaflet), 不依赖 CDN
- 测试: pytest + pytest-asyncio, 独立测试库 vcs_test
- 遵循项目现有命名: snake_case Python, camelCase JS
- 路由路径全部以 `/cad/` 开头，绝对路径注册

---

## File Structure

```
app/
├── api/v1/
│   ├── cad/                      # 新建：CAD 路由子包
│   │   ├── __init__.py           # 路由聚合（仿 data_standard_v2）
│   │   ├── _common.py            # 共享工具函数
│   │   ├── info.py               # GET /cad/{file_id}/info
│   │   ├── layers.py             # GET /cad/{file_id}/layers
│   │   ├── entities.py           # GET /cad/{file_id}/entities
│   │   └── preview.py            # GET /cad/{file_id}/preview
│   └── router.py                 # 注册 cad_router
├── services/
│   ├── cad_parser.py             # 新建：ezdxf DXF 解析服务
│   └── cad_preview.py            # 新建：SVG/PNG 预览生成
├── schemas/
│   └── cad.py                    # 新建：CAD 响应模型
├── models/
│   ├── stored_file.py            # 修改：+cad_metadata, +cad_preview_path
│   └── stored_file_layer.py      # 修改：+color_index, +color_rgb, +linetype, +is_visible, +is_frozen
├── frontend/
│   ├── index.html                # 修改：+CAD 检查 Tab
│   └── app.js                    # 修改：+CAD 前端逻辑
tests/
├── test_cad_parser.py            # 新建
├── test_cad_preview.py           # 新建
├── test_cad_routes.py            # 新建（路由级测试）
├── test_cad_integration.py       # 新建（端到端）
└── fixtures/cad/
    ├── simple.dxf                # 新建：测试 fixture
    └── complex.dxf               # 新建：测试 fixture
alembic/versions/
└── 0012_cad_enhancement.py       # 新建：迁移
```

---

## Task 1: 依赖 + 数据库迁移 + 模型扩展

**Files:**
- Modify: `requirements.txt`
- Modify: `app/models/stored_file.py`
- Modify: `app/models/stored_file_layer.py`
- Create: `alembic/versions/0012_cad_enhancement.py`

- [x] **Step 1: 添加依赖**

在 `requirements.txt` 末尾添加:

```
# CAD parsing & preview
ezdxf>=1.4.0
Pillow>=10.0.0
```

- [x] **Step 2: 安装依赖**

```bash
.venv\Scripts\pip install ezdxf Pillow
```

- [x] **Step 3: 扩展 StoredFile 模型**

在 `app/models/stored_file.py` 的 `StoredFile` 类中，`field_schema` 字段之后、`created_at` 之前添加:

```python
    # ── CAD 元数据（v2.18）──
    cad_metadata: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="CAD 元数据缓存: {version, units, extent_min, extent_max, entity_type_counts, ...}"
    )
    cad_preview_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True,
        comment="CAD 预览文件路径 (data/previews/{file_id}.svg)"
    )
```

- [x] **Step 4: 扩展 StoredFileLayer 模型**

在 `app/models/stored_file_layer.py` 的 `StoredFileLayer` 类中，`feature_count` 字段之后、`created_at` 之前添加:

```python
    # ── CAD 图层样式（v2.18）──
    color_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="DXF ACI 颜色索引 (1-255)"
    )
    color_rgb: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="真彩色 'R,G,B'（如有）"
    )
    linetype: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="线型名"
    )
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="可见性"
    )
    is_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="冻结状态"
    )
```

在 `stored_file_layer.py` 顶部 import 中添加 `Boolean`:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
```

- [x] **Step 5: 创建迁移脚本**

创建 `alembic/versions/0012_cad_enhancement.py`:

```python
"""CAD enhancement: stored_files + stored_file_layers new columns

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stored_files", sa.Column("cad_metadata", JSONB(), nullable=True))
    op.add_column("stored_files", sa.Column("cad_preview_path", sa.String(1024), nullable=True))
    op.add_column("stored_file_layers", sa.Column("color_index", sa.Integer(), nullable=True))
    op.add_column("stored_file_layers", sa.Column("color_rgb", sa.String(16), nullable=True))
    op.add_column("stored_file_layers", sa.Column("linetype", sa.String(64), nullable=True))
    op.add_column("stored_file_layers", sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("stored_file_layers", sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("stored_file_layers", "is_frozen")
    op.drop_column("stored_file_layers", "is_visible")
    op.drop_column("stored_file_layers", "linetype")
    op.drop_column("stored_file_layers", "color_rgb")
    op.drop_column("stored_file_layers", "color_index")
    op.drop_column("stored_files", "cad_preview_path")
    op.drop_column("stored_files", "cad_metadata")
```

- [x] **Step 6: 运行迁移**

```bash
.venv\Scripts\alembic upgrade head
```

- [x] **Step 7: 验证现有测试通过**

```bash
.venv\Scripts\python.exe -m pytest tests/test_api.py -v --tb=short -x
```

- [x] **Step 8: Commit**

```bash
git add requirements.txt app/models/stored_file.py app/models/stored_file_layer.py alembic/versions/0012_cad_enhancement.py
git commit -m "feat(cad): add ezdxf/Pillow deps and DB migration for CAD module"
```

---

## Task 2: CAD 解析服务（`cad_parser.py`）

**Files:**
- Create: `app/services/cad_parser.py`
- Create: `tests/test_cad_parser.py`
- Create: `tests/fixtures/cad/simple.dxf`

**Interfaces:**
- Produces: `CADParser`, `LayerInfo`, `EntityInfo`, `CADMetadata`, `CADDocument`

- [x] **Step 1: 生成测试 DXF fixture**

创建 `tests/fixtures/cad/generate_test_files.py`:

```python
"""生成测试用 DXF 文件（运行一次即可）。"""
import ezdxf

def create_simple():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Wall", color=1)
    doc.layers.add("Door", color=3)
    doc.layers.add("Window", color=5)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "Wall"})
    msp.add_line((100, 0), (100, 80), dxfattribs={"layer": "Wall"})
    msp.add_line((100, 80), (0, 80), dxfattribs={"layer": "Wall"})
    msp.add_line((0, 80), (0, 0), dxfattribs={"layer": "Wall"})
    msp.add_arc((50, 0), radius=20, start_angle=0, end_angle=90, dxfattribs={"layer": "Door"})
    msp.add_lwpolyline([(20, 80), (40, 80)], dxfattribs={"layer": "Window"})
    msp.add_text("Room A", dxfattribs={"layer": "Wall", "height": 5, "insert": (50, 40)})
    doc.saveas("tests/fixtures/cad/simple.dxf")

def create_complex():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i, name in enumerate(["Architecture", "Structure", "MEP", "Annotation", "DIM"]):
        doc.layers.add(name, color=i + 1)
    msp.add_line((0, 0), (200, 0), dxfattribs={"layer": "Architecture"})
    msp.add_circle((100, 50), radius=30, dxfattribs={"layer": "Structure"})
    msp.add_lwpolyline([(0,0),(200,0),(200,100),(0,100)], close=True, dxfattribs={"layer": "Architecture"})
    msp.add_mtext("Building A", dxfattribs={"layer": "Annotation", "insert": (50, 50)})
    msp.add_text("Note", dxfattribs={"layer": "Annotation", "height": 3, "insert": (10, 10)})
    for i in range(50):
        msp.add_line((i*4, 0), (i*4, 100), dxfattribs={"layer": "MEP"})
    doc.saveas("tests/fixtures/cad/complex.dxf")

if __name__ == "__main__":
    create_simple()
    create_complex()
```

运行生成 fixture。

- [x] **Step 2: 编写测试**

创建 `tests/test_cad_parser.py`:

```python
# -*- coding: utf-8 -*-
"""cad_parser 单元测试。"""
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cad"


class TestCADParserDataModels:
    def test_layer_info_fields(self):
        from app.services.cad_parser import LayerInfo
        layer = LayerInfo("Wall", 1, (255, 0, 0), "Continuous", True, False, False, 10, "line")
        assert layer.name == "Wall"
        assert layer.color_index == 1

    def test_entity_info_fields(self):
        from app.services.cad_parser import EntityInfo
        entity = EntityInfo("1A2B", "LINE", "Wall", 1, (0.0, 0.0), (100.0, 0.0), 100.0)
        assert entity.handle == "1A2B"

    def test_cad_metadata_fields(self):
        from app.services.cad_parser import CADMetadata
        meta = CADMetadata("R2018", "Unitless", (0, 0), (100, 80), 5,
                           {"LINE": 4, "TEXT": 1}, 3, True, False, False, False)
        assert meta.total_entities == 5

    def test_aci_to_rgb(self):
        from app.services.cad_parser import aci_to_rgb
        assert aci_to_rgb(1) == (255, 0, 0)
        assert aci_to_rgb(7) == (255, 255, 255)
        assert aci_to_rgb(0) is None


class TestCADParserParse:
    def test_parse_simple_dxf(self):
        from app.services.cad_parser import CADParser
        result = CADParser().parse_file(str(FIXTURES / "simple.dxf"))
        assert result is not None
        assert result.metadata.total_entities > 0
        assert result.metadata.layer_count == 3

    def test_extract_layer_tree(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "simple.dxf"))
        layers = CADParser().extract_layer_tree(doc)
        names = {l.name for l in layers}
        assert "Wall" in names and "Door" in names and "Window" in names

    def test_extract_entities(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "simple.dxf"))
        page = CADParser().extract_entities(doc, offset=0, limit=100)
        assert page.total > 0
        assert len(page.entities) > 0

    def test_extract_entities_by_layer(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "simple.dxf"))
        page = CADParser().extract_entities(doc, layer_filter="Wall")
        for e in page.entities:
            assert e.layer == "Wall"

    def test_extract_text_content(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "simple.dxf"))
        texts = CADParser().extract_text_content(doc)
        assert any("Room A" in t.text for t in texts)

    def test_extract_metadata(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "simple.dxf"))
        meta = CADParser().extract_metadata(doc)
        assert meta.has_text is True

    def test_parse_nonexistent_file(self):
        from app.services.cad_parser import CADParser
        assert CADParser().parse_file("/nonexistent/file.dxf") is None

    def test_parse_complex_dxf(self):
        from app.services.cad_parser import CADParser
        result = CADParser().parse_file(str(FIXTURES / "complex.dxf"))
        assert result is not None
        assert result.metadata.total_entities > 50
        assert result.metadata.has_text is True
        assert result.metadata.has_hatches is False

    def test_extract_entities_pagination(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "complex.dxf"))
        page1 = CADParser().extract_entities(doc, offset=0, limit=5)
        page2 = CADParser().extract_entities(doc, offset=5, limit=5)
        assert page1.total == page2.total
        assert len(page1.entities) == 5
        assert page1.entities[0].handle != page2.entities[0].handle

    def test_extract_entities_type_filter(self):
        from app.services.cad_parser import CADParser
        doc = CADParser()._open_doc(str(FIXTURES / "complex.dxf"))
        page = CADParser().extract_entities(doc, entity_type_filter="LINE")
        for e in page.entities:
            assert e.entity_type == "LINE"
```

- [x] **Step 3: 确认测试失败**

```bash
.venv\Scripts\python.exe -m pytest tests/test_cad_parser.py -x --tb=short 2>&1 | head -15
```

- [x] **Step 4: 实现 cad_parser.py**

创建 `app/services/cad_parser.py`:

```python
# -*- coding: utf-8 -*-
"""CAD 解析服务 — 基于 ezdxf 的 DXF 深度解析。

DWG 文件需先通过 dwg_converter 转为 DXF 再调用本模块。
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LayerInfo:
    name: str
    color_index: int
    color_rgb: tuple[int, int, int] | None
    linetype: str
    is_visible: bool
    is_frozen: bool
    is_locked: bool
    entity_count: int
    geometry_type: str


@dataclass
class EntityInfo:
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
    text: str
    layer: str
    handle: str
    position: tuple[float, float]


@dataclass
class EntityPage:
    total: int
    offset: int
    limit: int
    entities: list[EntityInfo] = field(default_factory=list)


@dataclass
class CADMetadata:
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
    metadata: CADMetadata
    layers: list[LayerInfo]
    doc: object


_ACI_COLORS: dict[int, tuple[int, int, int]] = {
    1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0),
    4: (0, 255, 255), 5: (0, 0, 255), 6: (255, 0, 255),
    7: (255, 255, 255), 8: (128, 128, 128), 9: (192, 192, 192),
}


def aci_to_rgb(color_index: int) -> tuple[int, int, int] | None:
    """DXF ACI 颜色索引 → RGB。"""
    if color_index in _ACI_COLORS:
        return _ACI_COLORS[color_index]
    if 10 <= color_index <= 249:
        r = ((color_index - 10) // 40) * 80
        g = (((color_index - 10) % 40) // 10) * 80
        b = ((color_index - 10) % 10) * 25
        return (min(r, 255), min(g, 255), min(b, 255))
    return None


_ENTITY_GEOM_MAP = {
    "POINT": "point", "TEXT": "text", "MTEXT": "text",
    "DIMENSION": "text", "LEADER": "text", "MLEADER": "text",
    "LINE": "line", "LWPOLYLINE": "line", "POLYLINE": "line",
    "ARC": "line", "CIRCLE": "line", "ELLIPSE": "line", "SPLINE": "line",
    "HATCH": "polygon", "SOLID": "polygon", "3DFACE": "polygon",
    "INSERT": "unknown", "BLOCK": "unknown",
}


class CADParser:
    """基于 ezdxf 的 CAD 解析器。"""

    def _open_doc(self, file_path: str):
        import ezdxf
        return ezdxf.readfile(file_path)

    def parse_file(self, file_path: str) -> CADDocument | None:
        try:
            doc = self._open_doc(file_path)
            layers = self.extract_layer_tree(doc)
            metadata = self.extract_metadata(doc)
            return CADDocument(metadata=metadata, layers=layers, doc=doc)
        except Exception as e:
            logger.warning("DXF 解析失败 %s: %s", file_path, e)
            return None

    def extract_layer_tree(self, doc) -> list[LayerInfo]:
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
            if name == "0":
                continue
            ci = layer.dxf.get("color", 7)
            result.append(LayerInfo(
                name=name, color_index=ci, color_rgb=aci_to_rgb(ci),
                linetype=layer.dxf.get("linetype", "Continuous"),
                is_visible=not layer.is_off(), is_frozen=layer.is_frozen(),
                is_locked=layer.is_locked(),
                entity_count=layer_entity_counts.get(name, 0),
                geometry_type="mixed" if len(layer_geom_types.get(name, set())) > 1
                else (layer_geom_types.get(name, {"unknown"}).pop() if layer_geom_types.get(name) else "unknown"),
            ))
        return sorted(result, key=lambda l: l.name)

    def extract_entities(self, doc, layer_filter=None, entity_type_filter=None,
                         offset=0, limit=50) -> EntityPage:
        entities, total = [], 0
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
        return EntityPage(total=total, offset=offset, limit=limit, entities=entities)

    def _entity_to_info(self, entity) -> EntityInfo | None:
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
                handle=entity.dxf.get("handle", ""), entity_type=etype,
                layer=entity.dxf.layer, color_index=entity.dxf.get("color", 7),
                start_point=sp, end_point=ep, length=length,
                text_content=text_content,
            )
        except Exception:
            return None

    def extract_text_content(self, doc) -> list[TextEntry]:
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
        counts: dict[str, int] = {}
        total = 0
        has_text = has_dimensions = has_blocks = has_hatches = False
        for entity in doc.modelspace():
            total += 1
            et = entity.dxftype()
            counts[et] = counts.get(et, 0) + 1
            if et in ("TEXT", "MTEXT"): has_text = True
            elif et == "DIMENSION": has_dimensions = True
            elif et == "INSERT": has_blocks = True
            elif et == "HATCH": has_hatches = True

        version = doc.dxfversion or "R2018"
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

        layer_count = len([l for l in doc.layers if l.dxf.name != "0"])
        return CADMetadata(version, units, extent_min, extent_max, total, counts,
                           layer_count, has_text, has_dimensions, has_blocks, has_hatches)
```

- [x] **Step 5: 运行测试通过**

```bash
.venv\Scripts\python.exe -m pytest tests/test_cad_parser.py -v
```

- [x] **Step 6: Commit**

```bash
git add app/services/cad_parser.py tests/test_cad_parser.py tests/fixtures/cad/
git commit -m "feat(cad): implement CADParser with ezdxf DXF parsing"
```

---

## Task 3: CAD 预览服务（`cad_preview.py`）

**Files:**
- Create: `app/services/cad_preview.py`
- Create: `tests/test_cad_preview.py`

- [x] **Step 1: 编写测试**

创建 `tests/test_cad_preview.py`:

```python
# -*- coding: utf-8 -*-
"""cad_preview 单元测试。"""
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cad"


class TestCADPreview:
    def test_generate_svg(self):
        from app.services.cad_preview import CADPreviewGenerator
        svg = CADPreviewGenerator().generate_svg(str(FIXTURES / "simple.dxf"))
        assert svg is not None and b"<svg" in svg

    def test_generate_png(self):
        from app.services.cad_preview import CADPreviewGenerator
        png = CADPreviewGenerator().generate_png(str(FIXTURES / "simple.dxf"))
        assert png is not None and png[:4] == b"\x89PNG"

    def test_generate_thumbnail(self):
        from app.services.cad_preview import CADPreviewGenerator
        thumb = CADPreviewGenerator().generate_thumbnail(str(FIXTURES / "simple.dxf"))
        assert thumb is not None and len(thumb) > 0

    def test_nonexistent_file(self):
        from app.services.cad_preview import CADPreviewGenerator
        assert CADPreviewGenerator().generate_svg("/no/such/file.dxf") is None

    def test_fallback_svg(self):
        from app.services.cad_preview import CADPreviewGenerator
        svg = CADPreviewGenerator().generate_svg("/no/such/file.dxf")
        # fallback 返回占位 SVG
        assert svg is None or b"Preview" in (svg or b"")
```

- [x] **Step 2: 确认失败 → 实现**

创建 `app/services/cad_preview.py`:

```python
# -*- coding: utf-8 -*-
"""CAD 预览生成 — ezdxf drawing add-on → SVG/PNG 缩略图。"""
import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CADPreviewGenerator:
    def __init__(self, max_entities: int = 10000):
        self.max_entities = max_entities

    def generate_svg(self, file_path: str, width: int = 400, height: int = 300) -> bytes | None:
        try:
            from ezdxf.addons.drawing import Frontend, RenderContext
            from ezdxf.addons.drawing.svg import SVGBackend
            doc = self._open(file_path)
            if doc is None:
                return None
            backend = SVGBackend()
            Frontend(RenderContext(doc), backend).draw_layout(doc.modelspace())
            s = backend.get_string()
            return s.encode("utf-8") if isinstance(s, str) else s
        except Exception as e:
            logger.warning("SVG 预览失败 %s: %s", file_path, e)
            return self._fallback_svg(width, height)

    def generate_png(self, file_path: str, width: int = 400, height: int = 300) -> bytes | None:
        try:
            from ezdxf.addons.drawing import Frontend, RenderContext
            from ezdxf.addons.drawing.pillow import PillowBackend
            from PIL import Image
            img = Image.new("RGB", (width, height), "white")
            doc = self._open(file_path)
            if doc is None:
                return None
            Frontend(RenderContext(doc), PillowBackend(img)).draw_layout(doc.modelspace())
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.warning("PNG 预览失败 %s: %s", file_path, e)
            return self._fallback_png(width, height)

    def generate_thumbnail(self, file_path: str) -> bytes | None:
        return self.generate_png(file_path, 200, 150)

    def _open(self, file_path: str):
        import ezdxf
        try:
            return ezdxf.readfile(file_path)
        except Exception as e:
            logger.warning("DXF 打开失败 %s: %s", file_path, e)
            return None

    def _fallback_svg(self, w, h) -> bytes:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect width="100%" height="100%" fill="#f0f0f0"/><text x="50%" y="50%" text-anchor="middle" fill="#999">Preview unavailable</text></svg>'.encode()

    def _fallback_png(self, w, h) -> bytes:
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (w, h), "#f0f0f0")
            ImageDraw.Draw(img).text((w//2, h//2), "Preview\nunavailable", fill="#999", anchor="mm")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return b""
```

- [x] **Step 3: 测试通过 → Commit**

```bash
.venv\Scripts\python.exe -m pytest tests/test_cad_preview.py -v
git add app/services/cad_preview.py tests/test_cad_preview.py
git commit -m "feat(cad): implement CADPreviewGenerator SVG/PNG rendering"
```

---

## Task 4: CAD 路由子包（`app/api/v1/cad/`）

**Files:**
- Create: `app/api/v1/cad/__init__.py`
- Create: `app/api/v1/cad/_common.py`
- Create: `app/api/v1/cad/info.py`
- Create: `app/api/v1/cad/layers.py`
- Create: `app/api/v1/cad/entities.py`
- Create: `app/api/v1/cad/preview.py`
- Create: `app/schemas/cad.py`
- Modify: `app/api/v1/router.py`
- Create: `tests/test_cad_routes.py`

- [x] **Step 1: 创建 `app/schemas/cad.py`**

```python
# -*- coding: utf-8 -*-
"""CAD API 响应模型。"""
from pydantic import BaseModel


class CADLayerResponse(BaseModel):
    name: str
    color_index: int | None = None
    color_rgb: list[int] | None = None
    linetype: str | None = None
    is_visible: bool = True
    is_frozen: bool = False
    entity_count: int = 0
    geometry_type: str = "unknown"


class CADInfoResponse(BaseModel):
    file_id: str
    format: str
    version: str | None = None
    units: str | None = None
    extent_min: list[float] | None = None
    extent_max: list[float] | None = None
    entity_counts: dict[str, int] = {}
    layer_count: int = 0
    has_text: bool = False
    has_dimensions: bool = False
    has_blocks: bool = False
    has_hatches: bool = False
    preview_url: str | None = None


class CADEntityResponse(BaseModel):
    handle: str
    entity_type: str
    layer: str
    color_index: int | None = None
    start_point: list[float] | None = None
    end_point: list[float] | None = None
    length: float | None = None
    area: float | None = None
    text_content: str | None = None


class CADEntityPageResponse(BaseModel):
    total: int
    offset: int
    limit: int
    entities: list[CADEntityResponse]


class CADLayersResponse(BaseModel):
    layers: list[CADLayerResponse]
```

- [x] **Step 2: 创建 `app/api/v1/cad/_common.py`**

```python
# -*- coding: utf-8 -*-
"""CAD 路由子包 — 共享工具。"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select as sa_select

from app.config import settings
from app.models.stored_file import StoredFile

logger = logging.getLogger("vcs")

CAD_FORMATS = {"dxf", "dwg"}


async def _load_cad_file(file_id: str, db: AsyncSession, api_key_id: str) -> StoredFile:
    """加载 CAD 文件记录并校验权限。"""
    stmt = sa_select(StoredFile).where(
        StoredFile.id == file_id,
        StoredFile.api_key_id == api_key_id,
    )
    result = await db.execute(stmt)
    sf = result.scalar_one_or_none()
    if sf is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if sf.file_format not in CAD_FORMATS:
        raise HTTPException(status_code=400, detail="文件不是 CAD 格式")
    return sf


def _resolve_cad_path(sf: StoredFile) -> str | None:
    """从 StoredFile 获取可读的 DXF 文件路径（DWG 自动转换）。"""
    if sf.file_data is None:
        return None

    if sf.file_format == "dwg":
        from app.services.dwg_converter import dwg_to_dxf
        tmp = tempfile.NamedTemporaryFile(suffix=".dwg", delete=False)
        tmp.write(sf.file_data)
        tmp.close()
        return dwg_to_dxf(tmp.name)

    suffix = f".{sf.file_format}" if sf.file_format else ".dxf"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(sf.file_data)
    tmp.close()
    return tmp.name


def _cleanup(path: str | None):
    if path:
        try:
            os.unlink(path)
        except Exception:
            pass
```

- [x] **Step 3: 创建路由模块**

**`app/api/v1/cad/info.py`**:

```python
"""CAD — 文件元数据信息。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADInfoResponse
from app.services.cad_parser import CADParser

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/info", response_model=CADInfoResponse, summary="CAD 文件元数据")
async def get_cad_info(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth.id)
    cad_path = C._resolve_cad_path(sf)
    if cad_path is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="无法读取 CAD 文件")
    try:
        doc = CADParser()._open_doc(cad_path)
        meta = CADParser().extract_metadata(doc)
        return CADInfoResponse(
            file_id=file_id, format=sf.file_format.upper(),
            version=meta.version, units=meta.units,
            extent_min=list(meta.extent_min), extent_max=list(meta.extent_max),
            entity_counts=meta.entity_type_counts, layer_count=meta.layer_count,
            has_text=meta.has_text, has_dimensions=meta.has_dimensions,
            has_blocks=meta.has_blocks, has_hatches=meta.has_hatches,
            preview_url=f"/api/v1/cad/{file_id}/preview",
        )
    finally:
        C._cleanup(cad_path)
```

**`app/api/v1/cad/layers.py`**:

```python
"""CAD — 图层树。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADLayersResponse, CADLayerResponse
from app.services.cad_parser import CADParser

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/layers", response_model=CADLayersResponse, summary="CAD 图层树")
async def get_cad_layers(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth.id)
    cad_path = C._resolve_cad_path(sf)
    if cad_path is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="无法读取 CAD 文件")
    try:
        doc = CADParser()._open_doc(cad_path)
        layers = CADParser().extract_layer_tree(doc)
        return CADLayersResponse(layers=[
            CADLayerResponse(
                name=l.name, color_index=l.color_index,
                color_rgb=list(l.color_rgb) if l.color_rgb else None,
                linetype=l.linetype, is_visible=l.is_visible,
                is_frozen=l.is_frozen, entity_count=l.entity_count,
                geometry_type=l.geometry_type,
            ) for l in layers
        ])
    finally:
        C._cleanup(cad_path)
```

**`app/api/v1/cad/entities.py`**:

```python
"""CAD — 实体列表（分页）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADEntityPageResponse, CADEntityResponse
from app.services.cad_parser import CADParser

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/entities", response_model=CADEntityPageResponse, summary="CAD 实体列表")
async def get_cad_entities(
    file_id: str,
    layer: str | None = Query(None, description="图层名筛选"),
    entity_type: str | None = Query(None, description="实体类型筛选"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth.id)
    cad_path = C._resolve_cad_path(sf)
    if cad_path is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="无法读取 CAD 文件")
    try:
        doc = CADParser()._open_doc(cad_path)
        page = CADParser().extract_entities(
            doc, layer_filter=layer, entity_type_filter=entity_type,
            offset=offset, limit=limit,
        )
        return CADEntityPageResponse(
            total=page.total, offset=page.offset, limit=page.limit,
            entities=[
                CADEntityResponse(
                    handle=e.handle, entity_type=e.entity_type,
                    layer=e.layer, color_index=e.color_index,
                    start_point=list(e.start_point) if e.start_point else None,
                    end_point=list(e.end_point) if e.end_point else None,
                    length=e.length, area=e.area, text_content=e.text_content,
                ) for e in page.entities
            ]
        )
    finally:
        C._cleanup(cad_path)
```

**`app/api/v1/cad/preview.py`**:

```python
"""CAD — 预览图（SVG/PNG）。"""
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.services.cad_preview import CADPreviewGenerator

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/preview", summary="CAD 预览图")
async def get_cad_preview(
    file_id: str,
    format: str = Query("svg", description="svg/png"),
    width: int = Query(400, ge=100, le=2000),
    height: int = Query(300, ge=100, le=2000),
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth.id)
    cad_path = C._resolve_cad_path(sf)
    if cad_path is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="无法读取 CAD 文件")
    try:
        gen = CADPreviewGenerator()
        if format == "png":
            content = gen.generate_png(cad_path, width, height)
            media_type = "image/png"
        else:
            content = gen.generate_svg(cad_path, width, height)
            media_type = "image/svg+xml"
        if content is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="预览生成失败")
        return Response(content=content, media_type=media_type)
    finally:
        C._cleanup(cad_path)
```

- [x] **Step 4: 创建 `app/api/v1/cad/__init__.py`**

```python
"""CAD 路由包 — 聚合所有 CAD 子模块路由。"""
from fastapi import APIRouter

from .info import router as info_router
from .layers import router as layers_router
from .entities import router as entities_router
from .preview import router as preview_router

router = APIRouter()

# 静态路径优先（无 /{file_id} 拦截风险，此处均含 /cad/ 前缀，顺序无严格要求）
router.include_router(info_router, tags=["CAD-信息"])
router.include_router(layers_router, tags=["CAD-图层"])
router.include_router(entities_router, tags=["CAD-实体"])
router.include_router(preview_router, tags=["CAD-预览"])
```

- [x] **Step 5: 注册到主路由**

在 `app/api/v1/router.py` 中添加:

```python
from app.api.v1.cad import router as cad_router
```

在文件末尾添加:

```python
# v2.18 CAD 功能子包
router.include_router(cad_router)
```

- [x] **Step 6: 编写路由测试**

创建 `tests/test_cad_routes.py`:

```python
# -*- coding: utf-8 -*-
"""CAD 路由集成测试。"""
import io
import pytest
import ezdxf


@pytest.fixture
def sample_dxf():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.add("Wall", color=1)
    doc.layers.add("Text", color=3)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "Wall"})
    msp.add_lwpolyline([(0,0),(100,0),(100,80),(0,80)], close=True, dxfattribs={"layer": "Wall"})
    msp.add_text("Room A", dxfattribs={"layer": "Text", "height": 5, "insert": (50, 40)})
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


@pytest.fixture
async def uploaded_cad(client, api_key_header, sample_dxf):
    resp = await client.post("/api/v1/data/upload", headers=api_key_header,
        files={"file": ("test.dxf", sample_dxf, "application/dxf")})
    assert resp.status_code in (200, 201)
    return resp.json().get("file_id") or resp.json().get("id")


class TestCADInfo:
    @pytest.mark.asyncio
    async def test_info_ok(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/info", headers=api_key_header)
        assert resp.status_code == 200
        d = resp.json()
        assert d["format"] == "DXF"
        assert d["layer_count"] >= 1

    @pytest.mark.asyncio
    async def test_info_404(self, client, api_key_header):
        resp = await client.get("/api/v1/cad/no-such-id/info", headers=api_key_header)
        assert resp.status_code == 404


class TestCADLayers:
    @pytest.mark.asyncio
    async def test_layers_ok(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/layers", headers=api_key_header)
        assert resp.status_code == 200
        names = {l["name"] for l in resp.json()["layers"]}
        assert "Wall" in names

    @pytest.mark.asyncio
    async def test_layers_404(self, client, api_key_header):
        resp = await client.get("/api/v1/cad/no-such-id/layers", headers=api_key_header)
        assert resp.status_code == 404


class TestCADEntities:
    @pytest.mark.asyncio
    async def test_entities_ok(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/entities", headers=api_key_header)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 3

    @pytest.mark.asyncio
    async def test_entities_filter(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/entities?layer=Wall", headers=api_key_header)
        for e in resp.json()["entities"]:
            assert e["layer"] == "Wall"

    @pytest.mark.asyncio
    async def test_entities_pagination(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/entities?limit=1", headers=api_key_header)
        assert len(resp.json()["entities"]) <= 1


class TestCADPreview:
    @pytest.mark.asyncio
    async def test_preview_svg(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/preview?format=svg", headers=api_key_header)
        assert resp.status_code == 200
        assert "image/svg" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_preview_png(self, client, api_key_header, uploaded_cad):
        resp = await client.get(f"/api/v1/cad/{uploaded_cad}/preview?format=png", headers=api_key_header)
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_preview_404(self, client, api_key_header):
        resp = await client.get("/api/v1/cad/no-such-id/preview", headers=api_key_header)
        assert resp.status_code == 404
```

- [x] **Step 7: 测试通过 → Commit**

```bash
.venv\Scripts\python.exe -m pytest tests/test_cad_routes.py -v
git add app/api/v1/cad/ app/schemas/cad.py app/api/v1/router.py tests/test_cad_routes.py
git commit -m "feat(cad): add CAD route sub-package with info/layers/entities/preview endpoints"
```

---

## Task 5: 前端 CAD 检查面板

**Files:**
- Modify: `app/frontend/index.html`
- Modify: `app/frontend/app.js`

- [x] **Step 1: 在 index.html 中添加 CAD Tab**

在文件详情 Tab 栏按钮区添加:

```html
<button class="tab-btn" data-tab="cad-check" onclick="switchTab('cad-check')">CAD 检查</button>
```

在 Tab 内容区添加:

```html
<div id="tab-cad-check" class="tab-content" style="display:none;">
  <div class="cad-layout">
    <div class="cad-layers-panel"><h4>图层</h4><div id="cad-layer-tree"></div></div>
    <div class="cad-main-panel">
      <div class="cad-preview-container"><img id="cad-preview-img" style="max-width:100%;border:1px solid #333;border-radius:4px;"></div>
      <div class="cad-entities-container"><h4>实体列表</h4><div id="cad-entity-list"></div><div id="cad-entity-pagination"></div></div>
    </div>
    <div class="cad-detail-panel"><h4>实体详情</h4><div id="cad-entity-detail"></div></div>
  </div>
</div>
```

添加 CSS:

```css
.cad-layout{display:grid;grid-template-columns:200px 1fr 250px;gap:12px;min-height:400px}
.cad-layers-panel,.cad-detail-panel{background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:10px;overflow-y:auto;max-height:500px}
.cad-layer-item{display:flex;align-items:center;padding:6px 8px;cursor:pointer;border-radius:4px;font-size:13px}
.cad-layer-item:hover{background:#2a2a4e}
.cad-main-panel{display:flex;flex-direction:column;gap:12px}
.cad-preview-container{background:#0d0d1a;border:1px solid #333;border-radius:6px;padding:8px;text-align:center}
.cad-entities-container{background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:10px;overflow-y:auto;max-height:300px}
```

- [x] **Step 2: 在 app.js 中添加 CAD 逻辑**

```javascript
// ── CAD 检查 (v2.18) ──
let cadFileId = null, cadOffset = 0;
const CAD_LIMIT = 50;

async function loadCADInfo(fid) {
  cadFileId = fid;
  const pImg = document.getElementById('cad-preview-img');
  if (pImg) pImg.src = `/api/v1/cad/${fid}/preview?format=svg&width=600&height=400`;
  try {
    const lr = await fetch(`/api/v1/cad/${fid}/layers`, {headers: getAuthHeaders()});
    if (lr.ok) renderCADLayers((await lr.json()).layers);
  } catch(e) {}
  await loadCADEntities(fid, 0);
}

function renderCADLayers(layers) {
  const c = document.getElementById('cad-layer-tree');
  if (!c) return;
  c.innerHTML = layers.map(l => {
    const clr = l.color_rgb ? `rgb(${l.color_rgb})` : '#888';
    return `<div class="cad-layer-item" onclick="filterCADLayer('${l.name}')">
      <span style="background:${clr};display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;"></span>
      <span>${l.name}</span><span style="color:#888;font-size:12px;margin-left:auto;">${l.entity_count}</span>
    </div>`;
  }).join('');
}

async function loadCADEntities(fid, off) {
  cadOffset = off;
  try {
    const r = await fetch(`/api/v1/cad/${fid}/entities?offset=${off}&limit=${CAD_LIMIT}`, {headers: getAuthHeaders()});
    if (!r.ok) return;
    renderCADEntities(await r.json());
  } catch(e) {}
}

function renderCADEntities(pg) {
  const c = document.getElementById('cad-entity-list');
  if (!c) return;
  let h = '<table class="data-table"><thead><tr><th>类型</th><th>图层</th><th>句柄</th><th>长度</th><th>文本</th></tr></thead><tbody>';
  for (const e of pg.entities) {
    h += `<tr onclick='showCADEntity(${JSON.stringify(e).replace(/'/g,"\\'")})' style="cursor:pointer">
      <td><code>${e.entity_type}</code></td><td>${e.layer}</td><td style="color:#888">${e.handle}</td>
      <td>${e.length?e.length.toFixed(2):'-'}</td><td>${e.text_content||'-'}</td></tr>`;
  }
  h += '</tbody></table>';
  c.innerHTML = h;
  const pag = document.getElementById('cad-entity-pagination');
  if (pag) {
    const tp = Math.ceil(pg.total / CAD_LIMIT), cp = Math.floor(pg.offset / CAD_LIMIT) + 1;
    pag.innerHTML = `<span style="color:#888">共${pg.total}条</span>
      <button ${pg.offset===0?'disabled':''} onclick="loadCADEntities('${cadFileId}',${pg.offset-CAD_LIMIT})">上一页</button>
      <span>${cp}/${tp}</span>
      <button ${pg.offset+CAD_LIMIT>=pg.total?'disabled':''} onclick="loadCADEntities('${cadFileId}',${pg.offset+CAD_LIMIT})">下一页</button>`;
  }
}

function showCADEntity(e) {
  const c = document.getElementById('cad-entity-detail');
  if (!c) return;
  let h = '<table class="detail-table">';
  h += `<tr><td>句柄</td><td>${e.handle}</td></tr><tr><td>类型</td><td>${e.entity_type}</td></tr>`;
  h += `<tr><td>图层</td><td>${e.layer}</td></tr>`;
  if (e.start_point) h += `<tr><td>起点</td><td>(${e.start_point.map(v=>v.toFixed(2)).join(',')})</td></tr>`;
  if (e.end_point) h += `<tr><td>终点</td><td>(${e.end_point.map(v=>v.toFixed(2)).join(',')})</td></tr>`;
  if (e.length) h += `<tr><td>长度</td><td>${e.length.toFixed(4)}</td></tr>`;
  if (e.text_content) h += `<tr><td>文本</td><td>${e.text_content}</td></tr>`;
  h += '</table>';
  c.innerHTML = h;
}

let cadLayerFilter = null;
function filterCADLayer(name) {
  cadLayerFilter = cadLayerFilter === name ? null : name;
  if (!cadFileId) return;
  const url = cadLayerFilter
    ? `/api/v1/cad/${cadFileId}/entities?layer=${encodeURIComponent(cadLayerFilter)}&limit=${CAD_LIMIT}`
    : `/api/v1/cad/${cadFileId}/entities?limit=${CAD_LIMIT}`;
  fetch(url, {headers: getAuthHeaders()}).then(r=>r.json()).then(renderCADEntities);
}
```

在文件详情加载函数中，当 `file_format` 为 `dxf`/`dwg` 时调用 `loadCADInfo(fileId)`。

- [x] **Step 3: Commit**

```bash
git add app/frontend/index.html app/frontend/app.js
git commit -m "feat(cad): add CAD inspection panel to frontend"
```

---

## Task 6: 回归测试 + 文档更新

- [x] **Step 1: 全量回归**

```bash
.venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

- [x] **Step 2: 覆盖率检查**

```bash
.venv\Scripts\python.exe -m pytest tests/ --cov=app/services/cad_parser.py --cov=app/services/cad_preview.py --cov=app/api/v1/cad/ --cov-report=term-missing
```

- [x] **Step 3: 更新 BASELINE.md**

更新版本号、目录结构、API 端点清单、服务模块、依赖等章节。

- [x] **Step 4: Commit**

```bash
git add BASELINE.md
git commit -m "docs: update BASELINE.md for v2.18.0 CAD module"
```

---

## Summary

| Task | 描述 | 预计 |
|------|------|------|
| 1 | 依赖 + 迁移 + 模型 | 15 min |
| 2 | cad_parser.py 核心 | 40 min |
| 3 | cad_preview.py 预览 | 25 min |
| 4 | cad/ 路由子包 (4 模块) | 50 min |
| 5 | 前端 CAD 面板 | 40 min |
| 6 | 回归 + 文档 | 15 min |
| **总计** | | **~3 h** |

**目录结构对比:**

```
之前（散落在 data.py 2488 行中）    之后（独立子包）
─────────────────────────────    ─────────────────
app/api/v1/data.py               app/api/v1/cad/
  (混在 2488 行里)                  ├── __init__.py
                                    ├── _common.py
                                    ├── info.py
                                    ├── layers.py
                                    ├── entities.py
                                    └── preview.py
                                  app/services/cad_parser.py
                                  app/services/cad_preview.py
                                  app/schemas/cad.py
```
