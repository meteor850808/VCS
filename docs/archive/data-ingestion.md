# 数据入库模块技术文档

> **版本**: v2.7 | **更新**: 2026-07-28 | **修复**: P0 功能缺陷 + 优化: 不留存文件/CSV分类/异常上报  
> **核心文件**: `app/api/v1/data.py`, `app/services/archive_extractor.py`, `app/services/gdb_processor.py`, `app/services/spatial_loader.py`, `app/services/feature_store.py`, `app/services/format_detection.py`

---

## 1. 架构概览

```
用户上传 ──┬── 直接上传 (POST /data/upload)          ──┐
           └── 切片上传 (chunk → merge)                ──┤
                                                         │
                    ┌────────────────────────────────────┘
                    ▼
            ┌──────────────┐
            │  格式检测      │  detect_format() → magic bytes + 扩展名 + schema
            │  _detect_...  │  → 填充 StoredFile 字段
            └──────┬───────┘
                   │
          ┌────────┴────────┐
          │  是否压缩包?      │
          ├──── Yes ────────┤──── No ────────→ 直接加载要素到分表
          │                 │
          ▼                 │
   ┌──────────────┐         │
   │ extract_      │         │
   │ archive()     │  ZIP/TAR 内存解压
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐
   │ classify_     │  按扩展名+目录结构分类
   │ extracted_    │
   │ files()       │
   └──────┬───────┘
          │
    ┌─────┼──────────┬─────────────┬───────────┐
    ▼     ▼          ▼             ▼           ▼
  GDB组  SHP组    矢量文件      表格文件     忽略
    │     │          │             │           │
    ▼     ▼          ▼             ▼           ▼
 Fiona  Fiona    bytes→临时    仅存储       记录
 读取   +伴生     文件→Fiona   StoredFile   extraction
 →GeoJSON 写临时    读取                   _details
  存储   目录读取
    │     │          │
    ▼     ▼          ▼
 write_feature() → point/line/polygon_features 分表
    │
    ▼
 db.commit() → 一次事务提交所有 StoredFile + 要素记录
```

---

## 2. 入库入口

### 2.1 直接上传 `POST /data/upload`

| 参数 | 类型 | 说明 |
|------|------|------|
| `file` | UploadFile | 原始文件（二进制流） |
| `description` | Form(str) | 文件描述（可选） |
| `standard_id` | Form(str) | 关联标准 ID（可选） |
| `layer_group_id` | Form(str) | 关联图层组 ID（可选） |
| `auto_create_standard` | Form(str) | 是否自动创建标准（可选） |
| `layer_group_name` | Form(str) | 新建标准时的图层组名（可选） |
| `table_name` | Form(str) | 新建标准时的数据表名（可选） |

**大小限制**: 单文件 ≤ 100MB (`max_single_file_size_mb`), 总请求 ≤ 200MB (`max_data_store_size_mb`)

### 2.2 切片上传 `POST /data/upload/chunk` + `POST /data/upload/merge`

用于超大文件分片上传：

1. `POST /data/upload/check` — 秒传检查（SHA-256 哈希比对）
2. `POST /data/upload/chunk` — 逐片上传（FormData: upload_id + chunk_index + total_chunks + file）
3. `POST /data/upload/merge` — 合并切片并执行完整入库流程

**切片存储**: `./data/uploads/chunks/{upload_id}/{index:06d}.chunk`

---

## 3. 入库处理流程

### 3.1 Step 1: 文件读取与校验

```
读取全部字节 → SHA-256 哈希 → 大小校验 → 文件名校验 → 去重检查
```

- **去重**: 如果 `enable_file_dedup=True`，相同 SHA-256 的文件直接返回已有记录
- **存储**: 原始二进制存入 `stored_files.file_data` (PostgreSQL BYTEA 列)

### 3.2 Step 2: 格式检测 (`_detect_and_populate`)

```
detect_format(临时文件路径) → FormatResult
  ├── magic bytes 检测（SHP header / GPKG SQLite header / MDB Jet header）
  ├── 扩展名推断（fallback）
  ├── 编码检测（chardet）
  ├── CRS 提取（SHP .prj / GeoJSON crs / KML default WGS84）
  ├── 几何类型检测（GeoJSON 采样 / SHP header / KML XML / GPKG 元数据）
  └── 字段 schema 解析（GeoJSON properties / SHP .dbf / CSV 表头 / XLSX / KML ExtendedData）
```

填充 StoredFile 字段:

| 字段 | 说明 |
|------|------|
| `data_type` | `geometry` / `table` / `archive` / `unknown` |
| `file_format` | `geojson` / `shp` / `gpkg` / `zip` / `csv` ... |
| `geometry_type` | `Point` / `LineString` / `Polygon` / `3d` / `table` |
| `detected_crs` | CRS 信息字符串 |
| `layer_count` | 图层数 |
| `feature_count` | 要素数量 |
| `field_schema` | JSONB 字段结构数组 |

### 3.3 Step 3: 标准关联

三种模式：

| 模式 | 参数 | 行为 |
|------|------|------|
| 不关联 | (无) | 仅存储，不关联标准 |
| 关联已有 | `standard_id` + `layer_group_id` | 设置关联，验证几何类型一致性 |
| 新建标准 | `auto_create_standard=true` + `standard_id` | 自动创建 图层组 → 标准表 → 字段定义 |

### 3.4 Step 4: 压缩包解压与分类

**4.1 解压** (`archive_extractor.extract_archive`)

- ZIP: `zipfile.ZipFile(io.BytesIO(content))` 内存解压
- TAR/GZ: `tarfile.open(fileobj=io.BytesIO(content))` 内存解压
- 安全检查: 路径穿越（`..`/`/`开头）、文件大小限制、总量限制

**4.2 分类** (`archive_extractor.classify_extracted_files`)

按以下规则分类到 `ClassifiedFiles`:

| 分类 | 条件 | 存储 | 要素加载 |
|------|------|------|---------|
| `gdb_groups` | 路径包含 `.gdb/` 目录 | 每个 layer → 独立 StoredFile (GeoJSON) | `_load_all_features_to_spatial_async` |
| `shp_groups` | `.shp` + `.dbf/.shx/.prj` 等伴生文件 | 仅 `.shp` → StoredFile | `_load_shp_group_features_async` (写临时目录 + Fiona) |
| `vectors` | `.geojson/.gpkg/.kml/.gml/.dxf` | 每个文件 → StoredFile | `_load_extracted_features_async` (写临时文件 + Fiona) |
| `tables` | `.csv/.xlsx/.xls` | 每个文件 → StoredFile | **不加载要素**（仅存储） |
| `ignored` | 其他所有文件 | 不存储 | 不加载 |

**4.3 特征加载**

所有特征加载任务通过 `asyncio.gather()` 并发执行，在同一个 async session 上添加要素记录。

---

## 4. 要素存储架构

### 4.1 分表设计

按几何类型拆为三张物理表，提升 PostGIS 空间查询性能：

| 表名 | 几何类型 | PostGIS 类型 |
|------|---------|-------------|
| `point_features` | Point, MultiPoint | POINT / MULTIPOINT |
| `line_features` | LineString, MultiLineString | LINESTRING / MULTILINESTRING |
| `polygon_features` | Polygon, MultiPolygon | POLYGON / MULTIPOLYGON |

### 4.2 字段结构

三张表共用相同的列定义（`_FeatureBase` mixin）：

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | VARCHAR(36) | UUID 主键 |
| `stored_file_id` | VARCHAR(36) FK | 关联 stored_files.id (CASCADE) |
| `api_key_id` | VARCHAR(36) FK | 关联 api_keys.id |
| `layer_name` | VARCHAR(256) | 图层名（如 "city"） |
| `source_name` | VARCHAR(512) | 来源文件名（如 "city.shp"） |
| `feature_index` | INT | 要素序号（0-based） |
| `geometry` | PostGIS GEOMETRY(SRID=4326) | WKT 格式写入，带 GIST 空间索引 |
| `geometry_type` | VARCHAR(16) | 原始几何类型（如 "Point"） |
| `properties` | JSON | 属性键值对 |
| `created_at` | TIMESTAMPTZ | 创建时间 |

### 4.3 要素写入路径

```
extract_features_from_bytes / extract_features_from_file
    │
    ├── GeoJSON → json.load() → 逐 Feature 提取 geometry + properties
    │
    └── 其他格式 → fiona.open() → 逐 feature 提取 geometry + properties
         │
         ▼
    write_feature(db, stored_file_id, api_key_id, filename, feat)
         │
         ├── geojson_to_wkt(geom) → WKT 字符串
         │
         └── _model_for_type(geom_type) → PointFeature / LineFeature / PolygonFeature
              │
              └── db.add(模型实例) → 等待 commit
```

**重要**: `write_feature()` 只执行 `db.add()`，不提交。所有要素在同一事务中由 `db.commit()` 统一提交。

---

## 5. 数据流图

### 5.1 非压缩包上传

```
上传 test.geojson (Point × 1)
  │
  ├── StoredFile 记录 → stored_files (feature_count=1, geometry_type="Point")
  │
  └── _load_all_features_to_spatial_async()
       │
       └── extract_features_from_bytes() → 解析 JSON → 1 个 Feature dict
            │
            └── write_feature() → point_features (geometry=POINT(121.47 31.23))
```

### 5.2 ZIP 含 SHP 组

```
上传 city.zip
  │
  ├── StoredFile 记录 (ZIP) → stored_files (is_archive=True, feature_count=0)
  │
  ├── classify: city.shp + city.dbf + city.shx + city.prj → shp_groups
  │
  ├── StoredFile 记录 (SHP) → stored_files (feature_count=3, geometry_type="Point")
  │
  └── _load_shp_group_features_async()
       │
       ├── 写临时目录: /tmp/shp_xxx/{city.shp, city.dbf, city.shx, city.prj}
       ├── fiona.open(shp_path, layer="city") → 3 个 Feature
       ├── write_feature() × 3 → point_features
       ├── 更新 sf.feature_count = 3, sf.geometry_type = "Point"
       └── 清理临时目录
```

### 5.3 ZIP 含 GDB 目录

```
上传 data.zip (内含 city.gdb/a00000001.gdbtable 等)
  │
  ├── StoredFile 记录 (ZIP) → stored_files (is_archive=True)
  │
  ├── classify: city.gdb/** → gdb_groups["city.gdb"] = [GDB文件列表]
  │
  ├── extract_gdb_layer_to_stored_files()
  │    │
  │    ├── 写临时目录: /tmp/vcs_gdb_xxx/city.gdb/*
  │    ├── fiona.listlayers(gdb_path) → ["Buildings", "Roads", ...]
  │    ├── fiona.open(gdb_path, layer="Buildings") → GeoJSON bytes
  │    └── 每个 layer → StoredFile (GeoJSON格式, file_data=geojson_bytes)
  │
  ├── 每个 GeoJSON StoredFile:
  │    ├── _detect_and_populate() → 检测 geometry_type / feature_count / field_schema
  │    ├── db.add(stored_file)
  │    └── _load_all_features_to_spatial_async()
  │         └── extract_features_from_bytes(geojson_bytes) → write_feature() × N
  │
  └── db.commit()
```

---

## 6. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_data_store_size_mb` | 200 | 单次请求总大小上限 (MB) |
| `max_single_file_size_mb` | 100 | 单文件 BYTEA 存储上限 (MB) |
| `max_features_per_upload` | 2000 | 单文件最大要素写入数 |
| `enable_file_dedup` | True | SHA-256 去重开关 |
| `file_chunk_size_mb` | 10 | 切片上传每片大小 (MB) |
| `upload_dir` | `./data/uploads` | 切片临时目录根路径 |

---

## 7. API 端点汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/data/upload` | 直接上传文件 |
| GET | `/data/upload/check` | 秒传检查 (SHA-256) |
| POST | `/data/upload/chunk` | 上传单个切片 |
| GET | `/data/upload/chunk-status` | 查询切片进度 |
| POST | `/data/upload/merge` | 合并切片并入库 |
| DELETE | `/data/upload/chunk` | 取消上传，清理切片 |
| GET | `/data` | 分页查询文件列表 |
| GET | `/data/{file_id}` | 文件详情 |
| GET | `/data/{file_id}/features` | 查询文件空间要素 |
| GET | `/data/{file_id}/structure` | 预览字段结构 |
| PATCH | `/data/{file_id}` | 编辑元数据 |
| DELETE | `/data/{file_id}` | 删除文件及关联要素 |
| POST | `/data/{file_id}/reindex` | 重新索引空间要素（⚠️ file_data=None 时返回 410） |
| POST | `/data/{file_id}/quality-check` | 质量检查 |
| POST | `/data/batch-classify` | 批量关联数据标准 |
| GET | `/data/{file_id}/download` | 下载文件（⚠️ file_data=None 时返回 410） |

---

## 8. 已知问题与技术债务

### P0 — 功能缺陷 ✅ 已修复 (2026-07-28)

| # | 问题 | 修复位置 | 修复方式 |
|---|------|---------|---------|
| 1 | **ZIP 上传后 archive_record.feature_count=0** | `data.py` upload + merge_chunks | `asyncio.gather()` 后查询 `source_archive` 子文件，聚合 `feature_count` 到父记录 |
| 2 | **merge_chunks 表格文件缺少 extraction_details** | `data.py` merge_chunks ⑥ | 补充 `extraction_details.append({"file_id":..., "status":"table_only"})` |
| 3 | **merge_chunks GDB extraction_details 缺少 file_id** | `data.py` merge_chunks ③ | 补充 `"file_id": gdb_sf.id` 字段 |

### P1 — 可靠性问题

### P2 — 设计问题 ✅ 已优化 (2026-07-28)

| # | 问题 | 修复方式 |
|---|------|---------|
| 4 | **VECTOR_EXTENSIONS 包含 .csv/.xlsx** | 从 VECTOR_EXTENSIONS 移除 `.csv/.xlsx`；`_load_all_features_to_spatial_async` 跳过列表增加 `.csv/.xlsx/.xls` |
| 5 | **archive_record.file_data 存储完整 ZIP 二进制（双倍存储浪费）** | **全部 file_data 设 None** — 上传文件仅做提取，不留存原始字节。download/reindex 端点返回 410 Gone |
| 9 | **格式检测写临时文件再删除** | 保留现状（`_detect_and_populate` 和 `extract_features_from_bytes` 仍需临时文件） |

### P1 — 可靠性问题 ✅ 部分已优化 (2026-07-28)

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| 4 | **asyncio.gather 静默吞异常** | ✅ 已优化 | `return_exceptions=True` + 收集异常结果，汇总到 `warning` 字段通知用户 |
| 5 | **单事务提交，部分失败导致全量丢失** | ⚠️ 保留 | 回滚风险仍在，但 file_data=None 大幅减小 BYTEA 回滚开销 |
| 6 | **MDB 格式无实际读取器** | ⚠️ 保留 | 需要 pyodbc + MS Access 驱动，暂不处理 |

### P2 — 设计问题（保留）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 10 | **_model_for_type 默认回退到 PolygonFeature** | `feature_store.py:30` | 未知几何类型（如 "GeometryCollection"）会错误地存入 polygon_features 表 |

---

## 9. 关键数据模型关系

```
api_keys (1) ──────< (N) stored_files
                          │
                          ├── source_archive = NULL        ← 主文件
                          ├── source_archive = "xxx.zip"   ← 从 ZIP 解压的子文件
                          │
                          ├── is_archive = True             ← ZIP/TAR 包本身
                          │     └── file_data = None 【优化#5】不留存原始字节
                          │
                          └── is_archive = False            ← 解压后的单个文件
                                └── file_data = None 【优化#5】不留存原始字节
                                       │
                                       ├── stored_file_id ───> point_features
                                       ├── stored_file_id ───> line_features
                                       └── stored_file_id ───> polygon_features

data_standards (1) ──< (N) stored_files     ← standard_id 外键
standard_tables (1) ─< (N) stored_files     ← standard_table_id 外键

【设计原则】上传文件仅做提取，不留存原始字节。file_data 列始终为 NULL，
StoredFile 记录仅用于元数据追踪（标准关联、几何类型、要素数量等）。
```

---

## 10. 临时文件管理

### 创建点

| 场景 | 临时路径 | 模块 | 生命周期 |
|------|---------|------|---------|
| SHP 伴生文件写入 | `/tmp/shp_xxx/` | `_load_shp_group_features_async` | 函数 finally 块 shutil.rmtree |
| GDB 文件写入 | `/tmp/vcs_gdb_xxx/` | `extract_gdb_layer_to_stored_files` | 函数 finally 块 shutil.rmtree |
| 格式检测临时文件 | `/tmp/tmp_xxx.geojson` | `_detect_and_populate` | try/finally os.unlink |
| 要素提取临时文件 | `/tmp/tmp_xxx.ext` | `extract_features_from_bytes` | try/finally os.unlink |
| 切片上传 | `./data/uploads/chunks/{upload_id}/` | `merge_chunks` | merge 完成后 shutil.rmtree |

### 安全清理机制

`app/core/temp_cleanup.py`:
- `TEMP_DIRS: set[Path]` — 全局注册集合
- `atexit.register(_cleanup_temp_dirs)` — 进程退出清理
- `signal.SIGTERM/SIGINT` handler — 异常退出清理
- SHP 组处理: 创建时 `TEMP_DIRS.add()`, finally 块 `TEMP_DIRS.discard()` + `shutil.rmtree()`

---

## 11. 依赖库

| 库 | 版本 | 用途 |
|----|------|------|
| Fiona | 1.10.1 | SHP/GPKG/GDB/KML/DXF 等格式读取（通过 GDAL 驱动） |
| Shapely | - | GeoJSON geometry → WKT 转换 |
| chardet | - | 文件编码检测 |
| SQLAlchemy 2.0 | async | ORM + 异步 DB 操作 |
| GeoAlchemy2 | - | PostGIS Geometry 列类型 |
| PostgreSQL 16 + PostGIS | - | 空间数据存储与查询 |

### Fiona 可用驱动

```
DXF, CSV, OpenFileGDB, ESRIJSON, ESRI Shapefile, FlatGeobuf, GeoJSON,
GeoJSONSeq, GPKG, GML, OGR_GMT, GPX, Idrisi, MapInfo File, DGN,
PCIDSK, OGR_PDS, S57, SQLite, TopoJSON
```

> **注意**: MDB (Personal GDB) 不在 Fiona 支持列表中。读取 MDB 需要 pyodbc + MS Access 驱动，当前未实现。
