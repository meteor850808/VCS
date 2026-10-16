# CAD 功能增强设计规格书

> **日期**: 2026-09-11
> **版本**: v2.18.0 计划
> **路径**: Approach A — ezdxf 轻量增强 + 预览生成
> **状态**: Draft

---

## 1. 背景与目标

### 1.1 现状

VCS 空间质检项目当前对 DWG/DXF 的处理能力有限：

| 能力 | 现状 | 问题 |
|------|------|------|
| DWG 读取 | LibreDWG `dwg2dxf` 转 DXF → Fiona 解析 | 仅几何，丢失文本/标注/块引用 |
| DXF 解析 | GDAL/Fiona DXF driver | 实体覆盖有限，无属性深度提取 |
| 可视化 | Leaflet 渲染 GeoJSON | 无 CAD 原生渲染，无图层颜色/线型 |
| 预览 | 无 | 无缩略图，无 SVG/PNG 预览 |
| 实体检查 | 无 | 无法浏览图层树、实体列表、属性面板 |
| 转换管道 | DWG 不能通过转换任务 | API 宣传 DWG 输入但 ogr2ogr 无法读 DWG |

### 1.2 目标

引入 `ezdxf`（MIT 协议，纯 Python）作为 DXF 智能层，增强 LibreDWG 集成，提供：

1. **CAD 实体深度解析** — 完整实体类型覆盖（LINE, ARC, CIRCLE, SPLINE, HATCH, TEXT, MTEXT, DIMENSION, INSERT, LEADER, TABLE 等）
2. **图层智能** — 图层树提取、颜色映射、可见性控制、冻结/锁定状态
3. **CAD 预览生成** — SVG/PNG 缩略图自动生成，文件列表页展示
4. **实体检查 API** — 按图层/类型浏览实体，属性面板数据
5. **前端 CAD 增强** — CAD 图层颜色样式、DWG 徽章、预览缩略图、实体检查面板
6. **转换管道修复** — DWG 文件可通过转换任务处理

### 1.3 非目标（本次不做）

- 3D 实体建模/编辑
- Web CAD 绘图工具
- DWG 原生写入（仅读取）
- OpenCADStudio 集成（后续评估）

---

## 2. 技术方案

### 2.1 新增依赖

```
ezdxf>=1.4.0        # DXF 读写/解析/渲染（MIT，纯 Python）
Pillow>=10.0.0      # ezdxf drawing add-on 渲染后端（已有？需确认）
```

**注意**: ezdxf 的 `drawing` add-on 需要 Pillow 作为渲染后端。如果项目已有 Pillow 则无新增；如无则需添加。

### 2.2 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Backend                       │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ ezdxf        │  │ LibreDWG     │  │ ezdxf       │  │
│  │ (DXF 解析)   │  │ (DWG→DXF)    │  │ drawing     │  │
│  │              │  │              │  │ (SVG/PNG)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │           │
│  ┌──────┴─────────────────┴──────────────────┴───────┐  │
│  │              cad_parser.py (新服务)                │  │
│  │  - parse_dxf_entities(doc) → EntityTree            │  │
│  │  - extract_layer_tree(doc) → LayerNode[]           │  │
│  │  - generate_cad_preview(path) → SVG/PNG bytes     │  │
│  │  - extract_cad_metadata(doc) → CADMetadata        │  │
│  └──────────────────────┬────────────────────────────┘  │
│                         │                               │
│  ┌──────────────────────┴────────────────────────────┐  │
│  │           API Layer (新端点)                       │  │
│  │  GET /data/{id}/cad-info    → CAD 元数据概览       │  │
│  │  GET /data/{id}/cad-layers  → 图层树 + 颜色        │  │
│  │  GET /data/{id}/cad-entities → 实体列表(分页)      │  │
│  │  GET /data/{id}/cad-preview → SVG/PNG 缩略图      │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │         spatial_loader.py 增强                     │  │
│  │  - ezdxf 实体提取替换 Fiona DXF driver            │  │
│  │  - 文本/标注/块引用 → properties JSON              │  │
│  │  - 图层颜色/线型 → stored_file_layers 增强         │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │         format_detection.py 增强                   │  │
│  │  - DXF schema: TEXT/MTEXT 实体文本提取              │  │
│  │  - DWG/DXF geometry_type 检测                      │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                        │
              REST API / WebSocket
                        │
┌─────────────────────────────────────────────────────────┐
│                    Web Frontend                         │
│  ┌────────────────────┐  ┌──────────────────────────┐  │
│  │ 文件列表增强        │  │ 实体检查面板              │  │
│  │ - CAD 缩略图预览    │  │ - 图层树 (checkbox/颜色)  │  │
│  │ - DWG 徽章          │  │ - 实体列表 (类型/图层)    │  │
│  │ - 图层数 badge      │  │ - 属性面板 (选中实体)     │  │
│  └────────────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.3 核心模块设计

#### 2.3.1 `app/services/cad_parser.py` — CAD 解析服务（新文件）

**职责**: 基于 ezdxf 的 DXF 深度解析，提供结构化 CAD 数据。

```python
class CADParser:
    """基于 ezdxf 的 CAD 解析器"""
    
    def parse_file(self, file_path: str) -> CADDocument:
        """解析 DXF/DWG 文件（DWG 先转 DXF）"""
        
    def extract_layer_tree(self, doc) -> list[LayerInfo]:
        """提取图层树：名称、颜色、线型、可见性、冻结/锁定状态"""
        
    def extract_entities(self, doc, layer_filter=None, 
                         entity_type_filter=None,
                         offset=0, limit=100) -> EntityPage:
        """提取实体列表（分页），支持图层/类型筛选"""
        
    def extract_entity_detail(self, doc, handle: str) -> EntityDetail:
        """提取单个实体完整属性"""
        
    def extract_text_content(self, doc) -> list[TextEntry]:
        """提取所有文本内容（TEXT/MTEXT/DIMENSION/LEADER 中的文字）"""
        
    def extract_metadata(self, doc) -> CADMetadata:
        """提取 CAD 元数据：版本、单位、范围、实体统计"""
```

**数据模型**:

```python
class LayerInfo(BaseModel):
    name: str
    color_index: int          # DXF ACI 颜色索引 (1-255)
    color_rgb: tuple[int,int,int] | None  # 真彩色 (如有)
    linetype: str             # 线型名
    is_visible: bool
    is_frozen: bool
    is_locked: bool
    entity_count: int         # 该层实体数
    geometry_type: str        # 点/线/面/混合

class EntityInfo(BaseModel):
    handle: str               # DXF 句柄
    entity_type: str          # LINE, ARC, CIRCLE, LWPOLYLINE...
    layer: str
    color_index: int
    start_point: tuple | None
    end_point: tuple | None
    # 类型特定属性
    text_content: str | None  # TEXT/MTEXT 的文字内容
    length: float | None      # 线段长度
    area: float | None        # 面积 (闭合实体)
    
class CADMetadata(BaseModel):
    version: str              # R2018, R2013...
    units: str                # Millimeters, Meters...
    extent_min: tuple[float,float]
    extent_max: tuple[float,float]
    total_entities: int
    entity_type_counts: dict[str, int]
    layer_count: int
    has_text: bool
    has_dimensions: bool
    has_blocks: bool
    has_hatches: bool
```

#### 2.3.2 `app/services/cad_preview.py` — CAD 预览生成（新文件）

**职责**: 使用 ezdxf `drawing` add-on 生成 SVG/PNG 缩略图。

```python
class CADPreviewGenerator:
    """CAD 文件预览生成器"""
    
    def generate_svg(self, file_path: str, 
                     width: int = 400, height: int = 300) -> bytes:
        """生成 SVG 预览（用于前端内联显示）"""
        
    def generate_png(self, file_path: str,
                     width: int = 400, height: int = 300) -> bytes:
        """生成 PNG 缩略图（用于文件列表）"""
        
    def generate_thumbnail(self, file_path: str) -> bytes:
        """生成 200x150 缩略图（数据库存储）"""
```

**关键实现**:
- 使用 `ezdxf.addons.drawing` 的 `Frontend` + `RenderContext` + `SVGOutput`/`PillowBackend`
- 处理大文件：设置实体数量上限（如 10000），超出截断并标注
- 处理异常：渲染失败返回空白占位图
- 缓存：生成结果写入 `data/previews/{file_id}.svg`，避免重复渲染

#### 2.3.3 增强 `spatial_loader.py` — ezdxf 实体提取

**变更**: 在 `_extract_from_dxf` 路径中增加 ezdxf 深度提取分支。

当前流程:
```
DXF → Fiona/GDAL → 几何 + Layer 属性 → 入库
```

增强后流程:
```
DXF → ezdxf 实体遍历 → 几何 + 完整属性(文本/颜色/线型/块引用) → 入库
     ↓
  Fiona/GDAL → 补充几何（ezdxf 不擅长的复杂几何）
```

**策略**: 
- ezdxf 作为**主解析器**，提取实体属性、文本内容、块引用
- Fiona/GDAL 作为**几何补充器**，处理 ezdxf 不直接支持的几何类型
- 合并结果：ezdxf 的丰富属性 + Fiona 的几何精度

**关键函数增强**:

```python
def _extract_from_dxf_enhanced(file_path: str, bytes_data: bytes) -> list[dict]:
    """使用 ezdxf 增强提取 DXF 实体"""
    import ezdxf
    from ezdxf.entities import DXFGraphic
    
    doc = ezdxf.readfile(file_path) if file_path else ezdxf.read_stream(bytes_data)
    msp = doc.modelspace()
    
    features = []
    for entity in msp:
        feature = _ezdxf_entity_to_feature(entity, doc)
        if feature:
            features.append(feature)
    
    return features

def _ezdxf_entity_to_feature(entity: DXFGraphic, doc) -> dict | None:
    """将单个 ezdxf 实体转换为 VCS feature dict"""
    # 核心: 保持与现有 spatial_loader 输出格式兼容
    # 新增: properties 中包含 text_content, color_index, linetype 等
```

#### 2.3.4 增强 `format_detection.py` — CAD 元数据提取

**变更**: 为 DWG/DXF 文件补充 geometry_type 和 field_schema 检测。

```python
def _detect_dxf_schema(file_path: str) -> dict:
    """检测 DXF 文件的字段结构"""
    import ezdxf
    doc = ezdxf.readfile(file_path)
    
    # 提取所有文本内容作为属性
    texts = set()
    for entity in doc.modelspace():
        if entity.dxftype() in ('TEXT', 'MTEXT'):
            texts.add(entity.dxf.text)
    
    return {
        'field_schema': [
            {'name': 'Layer', 'type': 'string'},
            {'name': 'text_content', 'type': 'string'},
            {'name': 'color_index', 'type': 'integer'},
        ],
        'geometry_type': _infer_geometry_type(doc),
    }
```

#### 2.3.5 增强 `dwg_converter.py` — DWG 管道修复

**变更**: 将 DWG→DXF 转换集成到转换任务管道中。

当前问题: `script_generation.py` 不知道 DWG 需要先转 DXF。

修复:
```python
# script_generation.py 增强
def generate_ogr2ogr_script(job) -> str:
    if job.detected_format == 'DWG':
        # 先 DWG→DXF，再处理 DXF
        dxf_path = dwg_to_dxf(job.input_path)
        # 然后用 ogr2ogr 处理 DXF
        return _generate_ogr2ogr_for_dxf(dxf_path, job)
```

### 2.4 API 端点设计

#### `GET /api/v1/data/{file_id}/cad-info`

返回 CAD 文件元数据概览。

```json
{
  "file_id": "xxx",
  "format": "DXF",
  "version": "R2018",
  "units": "Millimeters",
  "extent": {"min": [0, 0], "max": [1000, 800]},
  "entity_counts": {
    "total": 1234,
    "LINE": 456,
    "ARC": 78,
    "CIRCLE": 23,
    "LWPOLYLINE": 120,
    "TEXT": 89,
    "MTEXT": 45,
    "DIMENSION": 34,
    "INSERT": 67,
    "HATCH": 12
  },
  "layer_count": 15,
  "has_text": true,
  "has_dimensions": true,
  "has_blocks": true,
  "has_hatches": true,
  "preview_url": "/api/v1/data/{file_id}/cad-preview"
}
```

#### `GET /api/v1/data/{file_id}/cad-layers`

返回图层树，含颜色和线型信息。

```json
{
  "layers": [
    {
      "name": "Wall",
      "color_index": 1,
      "color_rgb": [255, 0, 0],
      "linetype": "Continuous",
      "is_visible": true,
      "is_frozen": false,
      "is_locked": false,
      "entity_count": 234,
      "geometry_type": "line"
    }
  ]
}
```

#### `GET /api/v1/data/{file_id}/cad-entities`

分页返回实体列表，支持图层/类型筛选。

**参数**:
- `layer`: 图层名筛选
- `entity_type`: 实体类型筛选
- `offset`: 分页偏移
- `limit`: 每页数量 (默认 50, 最大 500)

```json
{
  "total": 1234,
  "offset": 0,
  "limit": 50,
  "entities": [
    {
      "handle": "1A2B",
      "entity_type": "LINE",
      "layer": "Wall",
      "color_index": 1,
      "start_point": [100.0, 200.0],
      "end_point": [300.0, 200.0],
      "length": 200.0
    }
  ]
}
```

#### `GET /api/v1/data/{file_id}/cad-preview`

返回 SVG 或 PNG 预览图。

**参数**:
- `format`: `svg` (默认) 或 `png`
- `width`: 宽度 (默认 400)
- `height`: 高度 (默认 300)

**响应**: 直接返回文件内容 (Content-Type: image/svg+xml 或 image/png)

### 2.5 数据库变更

#### `stored_files` 表新增列

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| cad_metadata | JSONB | NULLABLE | CAD 元数据缓存 (版本/单位/实体统计) |
| cad_preview_path | String(1024) | NULLABLE | 预览文件路径 |

> 迁移脚本: `alembic/versions/0012_cad_enhancement.py`

#### `stored_file_layers` 表新增列

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| color_index | Integer | NULLABLE | DXF ACI 颜色索引 |
| color_rgb | String(16) | NULLABLE | 真彩色 "R,G,B" |
| linetype | String(64) | NULLABLE | 线型名 |
| is_visible | Boolean | DEFAULT True | 可见性 |
| is_frozen | Boolean | DEFAULT False | 冻结状态 |

### 2.6 前端变更

#### 文件列表增强
- CAD 文件显示 SVG 缩略图（hover 放大预览）
- DWG 文件添加蓝色 `DWG` 徽章（与现有 DXF 徽章并列）
- 图层数量 badge 显示在文件卡片右下角

#### 新增 CAD 检查面板
在文件详情页新增 "CAD 检查" Tab：

```
┌─────────────────────────────────────────────────────┐
│ [属性] [空间] [质检] [CAD 检查]                      │
├─────────────────────────────────────────────────────┤
│ ┌──────────────┐ ┌────────────────────────────────┐ │
│ │ 图层树        │ │ 实体列表                        │ │
│ │ ☑ Wall (234) │ │ ┌──────┬───────┬──────┬──────┐ │
│ │ ☑ Door (89)  │ │ │ 类型  │ 图层   │ 句柄  │ 长度  │ │
│ │ ☑ Window(67) │ │ ├──────┼───────┼──────┼──────┤ │
│ │ ☐ Dim (34)   │ │ │ LINE │ Wall  │ 1A2B │ 200  │ │
│ │ ☐ Text (45)  │ │ │ ARC  │ Door  │ 3C4D │ 157  │ │
│ │ ...          │ │ │ ...  │ ...   │ ...  │ ...  │ │
│ └──────────────┘ │ └──────┴───────┴──────┴──────┘ │
│                   │                    [下一页]      │
│ ┌──────────────┐ │                                 │
│ │ 实体详情      │ │                                 │
│ │ 句柄: 1A2B   │ │                                 │
│ │ 类型: LINE   │ │                                 │
│ │ 图层: Wall   │ │                                 │
│ │ 起点: (100,200)│                                │
│ │ 终点: (300,200)│                                │
│ │ 长度: 200.0  │ │                                 │
│ └──────────────┘ │                                 │
└─────────────────┴─────────────────────────────────┘
```

### 2.7 测试策略

| 测试类型 | 覆盖内容 |
|----------|----------|
| 单元测试 `test_cad_parser.py` | CADParser 全方法、数据模型序列化、异常处理 |
| 单元测试 `test_cad_preview.py` | SVG/PNG 生成、缓存、大文件截断、异常降级 |
| 单元测试 `test_format_detection_cad.py` | DXF schema 检测、geometry_type 推断 |
| 集成测试 `test_cad_api.py` | 4 个新 API 端点全分支 |
| E2E 测试 `tests/e2e/cad_preview.py` | 前端 CAD 检查面板交互 |

**测试数据**: 需准备 3-5 个测试 DXF 文件：
1. 简单 DXF (LINE + TEXT, <100 实体)
2. 复杂 DXF (多图层 + 标注 + 块引用 + 填充)
3. 大文件 DXF (>10000 实体，测试截断)
4. 损坏 DXF (测试异常处理)
5. DWG 文件 (测试 DWG→DXF→ezdxf 管道)

---

## 3. 实现计划

### Phase 1: 基础层 (ezdxf 集成 + 核心解析)
1. 添加 ezdxf 依赖到 requirements.txt
2. 实现 `cad_parser.py` 核心解析服务
3. 实现 `cad_preview.py` 预览生成
4. 编写单元测试
5. 数据库迁移 (stored_files + stored_file_layers 新列)

### Phase 2: API 层 (端点 + 增强)
6. 实现 4 个新 API 端点
7. 增强 `spatial_loader.py` ezdxf 实体提取
8. 增强 `format_detection.py` CAD 元数据检测
9. 修复 DWG 转换管道
10. 编写集成测试

### Phase 3: 前端 (UI 增强)
11. 文件列表 CAD 缩略图 + DWG 徽章
12. CAD 检查面板 (图层树 + 实体列表 + 属性面板)
13. E2E 测试

### Phase 4: 优化 + 文档
14. 性能优化 (大文件流式解析)
15. 缓存策略 (预览文件缓存)
16. 文档更新 (BASELINE.md, API docs)

---

## 4. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| ezdxf 不支持 DWG | DWG 需先转 DXF | 已有 LibreDWG 管道，保持现有方案 |
| 大文件解析慢 | API 超时 | 分页 + 流式 + 缓存 + 异步预览生成 |
| ezdxf 渲染不完美 | 预览失真 | 降级到几何边界框 + 标注 |
| Pillow 依赖冲突 | 部署问题 | 确认现有 Pillow 版本兼容性 |
| DXF 版本兼容性 | 旧版 DXF 解析失败 | ezdxf 支持 R12-R2018，覆盖主要版本 |

---

## 5. 成功标准

| 指标 | 目标 |
|------|------|
| DXF 实体类型覆盖 | >20 种（ezdxf 原生支持 40+） |
| 图层信息完整度 | 颜色/线型/可见性 100% |
| 预览生成时间 | <2s (1000 实体), <10s (10000 实体) |
| API 响应时间 | <500ms (分页查询) |
| 测试覆盖率 | 新代码 >90% |
| 前端交互 | 图层切换、实体选择、属性查看可用 |

---

## 6. 后续演进

- **Phase 2 (Approach B)**: Three.js CAD 查看器，客户端渲染
- **Phase 3 (Approach C)**: OpenCADStudio headless 集成
- **长期**: CAD 编辑功能、块编辑、标注编辑
