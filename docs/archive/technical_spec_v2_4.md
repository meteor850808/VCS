# VCS v2.4 数据上传模块重建技术方案

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          前端 (index.html)                               │
│  上传配置面板: 三模式(不关联/关联/新增) + 标准→图层层联 + 字段预览        │
│  数据管理页:   6类统计卡片 + 几何子类型筛选 + 文件列表(类型标签)          │
│  标准管理页:   列表项编辑/删除按钮 + 编辑弹窗 + 层组/表/字段/字典管理     │
└───────────────┬─────────────────────────────────────────┬───────────────┘
                │ REST API                                │
┌───────────────▼─────────────────────────────────────────▼───────────────┐
│                        FastAPI Router                                    │
│  data.py + data_standard_v2.py                                          │
│  ├── POST /data/upload              上传 + 格式检测 + 标准分类           │
│  ├── POST /data/upload/chunk        接收单个切片                         │
│  ├── POST /data/upload/merge        合并切片并入库                       │
│  ├── DELETE /data/upload/chunk      取消上传，清理临时切片               │
│  ├── GET /data/stats                统计(含 by_geometry_type)            │
│  ├── GET /data                      列表(支持 geometry_type 过滤)        │
│  ├── PUT /data-standards-v2/{id}    更新标准(名称/描述/分类/状态)        │
│  └── DELETE /data-standards-v2/{id} 删除标准                             │
└───────────────┬─────────────────────────────────────────┬───────────────┘
                │                                         │
┌───────────────▼─────────────────────────────────────────▼───────────────┐
│                     核心服务层                                           │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐     │
│  │ format_      │    │ structure_       │    │ feature_store     │     │
│  │ detection.py │    │ analyzer.py      │    │ .py               │     │
│  │ 格式检测      │    │ 自动建标准表     │    │ 空间要素入库       │     │
│  │ + 几何类型    │    │ 层组→表→字段     │    │ point/line/polygon │     │
│  └──────┬───────┘    └──────┬───────────┘    └──────┬────────────┘     │
│         │                   │                        │                  │
│  ┌──────▼───────┐    ┌──────▼───────────┐    ┌──────▼────────────┐     │
│  │ FormatResult │    │ auto_create_     │    │ PostGIS           │     │
│  │ data_type    │    │ standard_from_   │    │ point_features    │     │
│  │ geometry_type│    │ schema()         │    │ line_features     │     │
│  │ field_schema │    │                  │    │ polygon_features  │     │
│  └──────────────┘    └──────────────────┘    └───────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 核心数据流

### 上传 + 标准分类

```
用户: 选择文件 + 上传模式
  │
  ▼
┌─ Phase 0: 文件接收 ─────────────────────────────────────────────┐
│  分块读取 → SHA-256 哈希 → 内容拼接                              │
│  文件大小限制: max_data_store_size_mb / max_single_file_size_mb  │
│  哈希去重: enable_file_dedup                                     │
└───────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 1: 格式检测 ─────────────────────────────────────────────┐
│  detect_format(file_path, filename)                              │
│  → FormatResult {                                               │
│      format_name, data_type, geometry_type,                     │
│      detected_crs, layer_count, feature_count,                  │
│      field_schema_json                                          │
│    }                                                            │
│                                                                 │
│  BOM 处理: encoding='utf-8-sig' / strip() BOM                  │
│  几何类型: detect_geometry_type() → normalize_geometry_type()   │
│    GeoJSON: 采样前100要素 → dominant type                       │
│    SHP: .shp 文件头 32 字节 → code 1/3/5/8/11/13/21/31        │
│    KML: DOM 解析 Document/Placemark                              │
│    GPKG: SQL 查询 gpkg_geometry_columns                         │
│    OBJ: 固定 "3d"                                               │
│    CSV/XLSX: 固定 "table"                                       │
└───────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 2: 标准分类 ─────────────────────────────────────────────┐
│  三种模式:                                                       │
│                                                                 │
│  模式A: 不关联标准 (standard_id=None)                           │
│    → 直接入库，data_standard_name=null                           │
│                                                                 │
│  模式B: 关联已有标准 (standard_id=已有)                         │
│    → 设置 standard_id + standard_table_id                       │
│    → 冗余写入 data_standard_name                                │
│                                                                 │
│  模式C: 新增标准 (auto_create_standard=true)                    │
│    ├── 有 standard_id: 在已有标准下新增图层组                    │
│    └── 无 standard_id: 新建 DataStandard + 层组 + 表 + 字段     │
│                                                                 │
│  GEOM_MAP: point→Point, line→LineString, polygon→Polygon,       │
│            3d→3d, table→None                                    │
│                                                                 │
│  auto_create_standard_from_schema():                            │
│    StandardLayerGroup(name) → StandardTable(name, geometry_type) │
│    → StandardField(name, data_type, length, precision, ...)     │
└───────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 3: 空间要素入库 ────────────────────────────────────────┐
│  非压缩包:                                                       │
│    _load_all_features_to_spatial_async()                         │
│    → GeoJSON/SHP/KML/GPKG → PostGIS 三表                       │
│                                                                 │
│  压缩包:                                                         │
│    extract_archive() → 逐文件入库                                │
│    extract_gdb_to_stored_files() → ESRI GDB 解析                │
│    每个子文件: _detect_and_populate() + _load_extracted_features │
└───────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌─ Phase 4: 存储 ─────────────────────────────────────────────────┐
│  StoredFile 写入 DB                                             │
│  压缩包子文件: 逐条写入 stored_files                              │
│  返回 StoredFileUploadResponse                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 大文件切片上传

```
前端: 文件 SHA-256 → 分片 (chunk_size=5MB)
  │
  ├─ chunk 0: POST /data/upload/chunk {upload_id, chunk_index=0, total_chunks}
  ├─ chunk 1: POST /data/upload/chunk {upload_id, chunk_index=1, total_chunks}
  ├─ ...
  └─ chunk N: POST /data/upload/chunk {upload_id, chunk_index=N, total_chunks}
  │
  ▼
POST /data/upload/merge {upload_id}
  → 读取所有切片 → 拼接 → 写入 stored_files → 清理临时目录
```

---

## 数据模型

### StoredFile（v2.4 完整字段）

```python
class StoredFile(Base):
    __tablename__ = "stored_files"
    id: str                    # UUID 主键
    api_key_id: str            # FK → api_keys.id
    original_name: str         # 原始文件名
    content_type: str          # MIME 类型
    file_size: int             # 文件大小 (bytes)
    file_data: bytes | None    # 二进制内容 (BYTEA)
    description: str | None    # 用户描述
    source_archive: str | None # 来源压缩包名
    is_archive: bool           # 是否压缩包
    file_hash: str | None      # SHA-256 哈希
    chunk_index: int           # 分片序号 (0=完整)
    total_chunks: int          # 总分片数
    upload_id: str | None      # 上传会话 ID
    chunk_dir: str | None      # 临时切片目录
    standard_id: str | None    # FK → data_standards.id
    standard_table_id: str | None  # FK → standard_tables.id
    data_standard_name: str | None # 标准名称(冗余)
    data_type: str             # geometry/table/archive/unknown
    geometry_type: str | None  # Point/LineString/Polygon/3d/table
    file_format: str           # geojson/shp/gpkg/...
    detected_crs: str | None   # EPSG:4490 / EPSG:4326
    layer_count: int           # 图层数量
    feature_count: int         # 要素总数
    field_schema: dict | None  # JSONB 字段结构
    created_at: datetime       # 创建时间
    updated_at: datetime | None # 更新时间
```

### FormatResult

```python
@dataclass
class FormatResult:
    format_name: str           # GeoJSON / SHP / KML / ...
    encoding: str              # UTF-8 / GBK / ...
    confidence: float          # 检测置信度 0-1
    data_type: str             # geometry/table/archive/unknown
    geometry_type: str | None  # Point/LineString/Polygon/3d/table
    detected_crs: str | None   # EPSG:4490 / EPSG:4326
    layer_count: int           # 图层数量
    feature_count: int         # 要素总数
    field_schema_json: str | None  # 字段结构 JSON
```

### 几何类型归一化映射

```python
_GEOM_NORMALIZE = {
    "Point": "Point", "MultiPoint": "Point",
    "LineString": "LineString", "MultiLineString": "LineString",
    "Polygon": "Polygon", "MultiPolygon": "Polygon",
    "GeometryCollection": "Polygon",
    "3d": "3d", "TIN": "3d", "PolyhedralSurface": "3d",
}
```

---

## API 详细设计

### POST /data/upload

**请求**:
```
POST /api/v1/data/upload
Content-Type: multipart/form-data
X-API-Key: <key>

file: (binary)
description: "测试数据"
standard_id: "uuid"                    # 可选
standard_table_id: "uuid"              # 可选
standard_name: "自定义标准"             # 可选
layer_group_id: "uuid"                 # 可选
layer_group_name: "建筑图层"            # 可选
geometry_type: "point"                 # 可选: point/line/polygon/3d/table
auto_create_standard: true             # 可选
```

**响应**:
```json
{
  "id": "uuid",
  "original_name": "test.geojson",
  "file_size": 123456,
  "file_format": "GeoJSON",
  "data_type": "geometry",
  "geometry_type": "Polygon",
  "detected_crs": "EPSG:4490",
  "layer_count": 1,
  "feature_count": 150,
  "extracted_count": 0,
  "data_standard_name": "自定义标准",
  "message": "上传成功"
}
```

### GET /data

**新增查询参数**:
```
GET /api/v1/data?geometry_type=Point&data_type=geometry&page=1&page_size=20
```

### GET /data/stats

**响应新增字段**:
```json
{
  "total": 39,
  "by_type": {"geometry": 26, "archive": 12, "table": 1},
  "by_geometry_type": {"Point": 10, "Polygon": 2, "table": 1, "LineString": 3, "3d": 5},
  "by_format": {"GeoJSON": 5, "SHP": 8, "ZIP": 12, "OBJ": 5, "CSV": 1}
}
```

### PUT /data-standards-v2/{standard_id}

**请求**:
```json
{
  "name": "新标准名称",
  "description": "描述",
  "category": "custom",
  "status": "published"
}
```

**响应**: `DataStandardBrief`

---

## 前端架构

### 上传配置面板（三模式 Radio）

```
┌─ 上传配置 ──────────────────────────────────────────────────────┐
│                                                                 │
│  ○ 不关联标准  ● 关联已有标准  ○ 新增标准                       │
│                                                                 │
│  [关联已有标准 子面板]                                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 标准: [下拉选择 ▼]  图层组: [下拉选择 ▼]                │   │
│  │                                                         │   │
│  │ 字段结构:                                                │   │
│  │ ┌─────────┬──────────┬────────┬────────┐               │   │
│  │ │ 字段名   │ 类型     │ 可空   │ 示例   │               │   │
│  │ ├─────────┼──────────┼────────┼────────┤               │   │
│  │ │ id      │ integer  │ ✗      │ 1      │               │   │
│  │ │ name    │ string   │ ✓      │ 张三   │               │   │
│  │ └─────────┴──────────┴────────┴────────┘               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  [新增标准 子面板]                                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 标准集: [已有标准 ▼] 或 [新建标准]                       │   │
│  │ 标准名称: [________]  图层名称: [________]              │   │
│  │ 数据类型: 📍点 〰️线 ⬜面 🧊三维 📊表单                  │   │
│  │                                                         │   │
│  │ 预览字段:                                                │   │
│  │ name(string) nullable✓ | id(integer) nullable✗ | ...   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 数据管理页（几何子类型筛选）

```
┌─ 统计卡片 ──────────────────────────────────────────────────────┐
│ 📍 点    〰️ 线    ⬜ 面    🧊 三维    📊 表单    📦 压缩包     │
│  10       3        2        5         1          12            │
└─────────────────────────────────────────────────────────────────┘

┌─ 筛选栏 ────────────────────────────────────────────────────────┐
│ 类型: [全部 ▼] [📍 点 · Point] [〰️ 线 · LineString] ...       │
└─────────────────────────────────────────────────────────────────┘

┌─ 文件列表 ──────────────────────────────────────────────────────┐
│ checkbox │ 文件名          │ 类型 │ 格式 │ CRS    │ 大小 │ 时间 │
│ ☐       │ building.geojson│ ⬜面  │ GeoJSON│4490  │ 12KB │ 7-22 │
│ ☐       │ roads.shp       │ 〰️线  │ SHP   │4326  │ 45KB │ 7-22 │
└─────────────────────────────────────────────────────────────────┘
```

### 标准管理页（操作按钮）

```
┌─ 标准列表 ──────────────────────────────────────────────────────┐
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ ZoneStandard                          ✏️ 🗑️            │   │
│ │ custom  ⚪ 草稿  v1                                        │   │
│ │ 测试标准描述                                              │   │
│ └─────────────────────────────────────────────────────────┘   │
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ 建筑数据标准                        ✏️ 🗑️            │   │
│ │ national  🟢 已发布  v3                                   │   │
│ └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 性能设计

### 格式检测
- **BOM 处理**: `utf-8-sig` 编码自动跳过 BOM，避免 JSON 解析失败
- **几何类型**: GeoJSON 仅采样前 100 要素，避免大文件全量解析
- **SHP 头部**: 仅读取 100 字节文件头，O(1) 复杂度

### 标准分类
- **冗余存储**: `data_standard_name` 冗余字段避免列表查询 JOIN
- **延迟创建**: `auto_create_standard` 仅在有 field_schema 时触发

### 数据筛选
- **复合索引**: `(api_key_id, created_at)` / `(geometry_type)` / `(data_type)`
- **前端缓存**: 统计数据缓存 30 秒

---

## 安全考虑

1. **租户隔离**: 所有查询通过 `api_key_id` 过滤
2. **文件大小限制**: 双层限制（总大小 + 单文件大小）
3. **哈希去重**: SHA-256 哈希避免重复存储
4. **SQL 注入防护**: 全部使用 SQLAlchemy ORM / 参数化查询
5. **输入验证**: `validate_filename()` 防止路径穿越
