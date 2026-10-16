# VCS v2.4 数据上传模块重建 + 数据类型细分修订记录

## 版本信息
- **版本**: v2.4
- **日期**: 2026-07-22
- **类型**: 核心模块重建 + 数据标准集成 + 大文件切片 + 数据类型细分
- **影响范围**: 上传模块、数据标准管理前端、格式检测、数据库模型、Schema、迁移脚本

---

## 修订目标

1. **按标准分类入库**: 上传时可关联已有数据标准（standard_id）或新建标准（auto_create_standard），实现数据自动归类
2. **自动创建标准表**: 根据上传文件的字段结构（field_schema），自动创建层组→标准表→标准字段层级
3. **临时数据模式**: 支持文件先入库后关联标准，不强制上传时绑定标准
4. **多格式支持**: GeoJSON / SHP / KML / GPKG / OBJ / MDB / GDB / CSV / XLSX，压缩包自动解压
5. **图形/表单数据分离**: `data_type` 字段区分 geometry / table / archive / unknown
6. **大文件切片上传**: upload_id + chunk_index + total_chunks 支持断点续传
7. **数据类型细分**: 将"图形"拆分为 Point / LineString / Polygon / 3d / table 五种子类型
8. **标准管理可维护**: 标准列表项增加编辑/删除操作按钮

---

## 详细变更

### 1. 数据库模型扩展

#### 1.1 StoredFile 新增字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `data_type` | VARCHAR(20) | 数据类型: geometry/table/archive/unknown |
| `geometry_type` | VARCHAR(32) | 几何细分: Point/LineString/Polygon/MultiPolygon/3d/table |
| `file_format` | VARCHAR(32) | 文件格式: geojson/shp/gpkg/kml/obj/csv/xlsx/... |
| `detected_crs` | VARCHAR(64) | 检测到的坐标系: EPSG:4490 / EPSG:4326 |
| `layer_count` | INTEGER | 图层数量 |
| `feature_count` | BIGINT | 要素总数 |
| `field_schema` | JSONB | 字段结构 `[{name,type,nullable,sample}]` |
| `upload_id` | VARCHAR(64) | 上传会话 ID（SHA-256），合并前追踪 |
| `chunk_dir` | VARCHAR(1024) | 临时切片目录路径 |
| `standard_id` | FK → data_standards.id | 关联数据标准 |
| `standard_table_id` | FK → standard_tables.id | 关联标准表 |
| `data_standard_name` | VARCHAR(256) | 关联标准名称（冗余，便于列表展示） |

#### 1.2 新增索引
| 索引名 | 列 | 说明 |
|--------|------|------|
| `ix_stored_files_api_created` | api_key_id, created_at | 列表查询优化 |
| `ix_stored_files_data_type` | data_type | 数据类型筛选 |
| `ix_stored_files_geometry_type` | geometry_type | 几何子类型筛选 |
| `ix_stored_files_file_format` | file_format | 格式筛选 |
| `ix_stored_files_upload_id` | upload_id | 切片上传追踪 |

### 2. 格式检测模块重写

#### 2.1 BOM 处理修复
- **问题**: UTF-8 BOM (`\xEF\xBB\xBF`) 导致 JSON 解析失败
- **修复**: `_parse_geojson_schema` / CRS 提取改用 `encoding='utf-8-sig'`；`_detect_by_magic` 文本检测前先 `strip()` BOM

#### 2.2 几何类型检测（新增）
- **文件**: `app/services/format_detection.py`
- `detect_geometry_type(file_path, fmt)` — 按格式分派检测
- `normalize_geometry_type(raw)` — 归一化为五大类
- 支持格式: GeoJSON（采样前 100 要素）、SHP（头部读取）、KML（DOM 解析）、GPKG（SQL 查询）、OBJ（固定 3d）、CSV/XLSX（固定 table）

#### 2.3 FormatResult 新增字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `geometry_type` | str \| None | 归一化后的几何子类型 |
| `data_type` | str | 数据类型分类 |

### 3. 上传 API 扩展

#### 3.1 新增参数
| 参数 | 类型 | 说明 |
|------|------|------|
| `standard_id` | Form(str) | 关联已有标准 ID |
| `standard_table_id` | Form(str) | 关联标准表 ID |
| `standard_name` | Form(str) | 新增标准时的名称 |
| `layer_group_id` | Form(str) | 关联已有图层组 ID |
| `layer_group_name` | Form(str) | 新增图层组名称 |
| `geometry_type` | Form(str) | 数据类型: point/line/polygon/3d/table |

#### 3.2 标准分类逻辑
```
上传文件
  ├── auto_create_standard=true + 无 standard_id
  │     → 新建 DataStandard + 层组 + 标准表 + 字段
  ├── auto_create_standard=true + 有 standard_id
  │     → 在已有标准下新增图层组 + 标准表 + 字段
  ├── auto_create_standard=false + 有 layer_group_id
  │     → 仅设置关联，不创建新结构
  └── 无标准参数
        → 临时数据，不关联标准
```

#### 3.3 几何类型映射
```python
GEOM_MAP = {
    "point": "Point",
    "line": "LineString",
    "polygon": "Polygon",
    "3d": "3d",
    "table": None,
}
```

### 4. 数据管理前端升级

#### 4.1 上传配置面板重写
- 三个互斥 Radio 选项：
  1. **不关联标准** — 直接上传
  2. **关联已有标准** — 选标准→选图层→显示字段结构
  3. **新增标准** — 选已有标准或新建标准集→输入图层名称→选数据类型→预览字段

#### 4.2 数据类型筛选
- 统计卡片: 📍点 / 〰️线 / ⬜面 / 🧊三维 / 📊表单 / 📦压缩包（共 6 类）
- 筛选下拉: 细分到几何子类型（Point / LineString / Polygon / 3d / table）
- 文件列表: 每行显示具体类型标签

#### 4.3 统计 API 扩展
- `GET /data/stats` 新增 `by_geometry_type` 分布统计

### 5. 数据标准管理修复

#### 5.1 标准列表操作按钮
- 每个标准项右侧显示 ✏️ 编辑 / 🗑️ 删除 按钮
- `onclick="event.stopPropagation()"` 防止误触选择
- 按钮默认半透明，hover 变亮

#### 5.2 编辑弹窗（新增）
- `v2EditStd()` 弹出 modal 对话框
- 包含: 名称输入、描述文本域、分类下拉（national/industry/custom）、状态下拉（草稿/已发布/已归档）
- `v2SaveStd()` 调用 `PUT /api/v1/data-standards-v2/{id}` 保存

#### 5.3 标准管理页面路由修复
- `goPage('standards')` 中调用的 `loadStds()` 改为 `v2LoadStandards()`

### 6. Bug 修复

#### 6.1 端口冲突诊断（ERROR.txt）
- **根因**: 端口 45678 运行了错误项目（`空间API服务`），缺少 v2 路由 → 401/500 错误
- **修复**: 杀掉 PID 42892，端口 8000 启动正确项目（`空间质检`）

#### 6.2 API Key 吊销
- **根因**: 浏览器 localStorage 中 `test_key_1783996146430` 已吊销（`is_active=False`）
- **修复**: 重新激活该 Key

#### 6.3 quality_reports 列缺失
- **根因**: `quality_reports` 表缺少 10 个列（五维评分/拓扑/standard_table_id/updated_at）
- **修复**: ALTER TABLE 补列 + 迁移 `0003c_fix_quality_reports_cols.py`

#### 6.4 standard_id 列缺失
- **根因**: stored_files 表缺少 `standard_id` / `standard_table_id` 列
- **修复**: 迁移 `0003b_fix_standard_id.py` 补充

#### 6.5 resolved_standard_name 变量名
- **根因**: upload 端点中变量名不一致导致 NameError
- **修复**: 统一为 `resolved_standard_name`

---

## 文件变更清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `alembic/versions/0003b_fix_standard_id.py` | 补 stored_files standard_id/standard_table_id 列 |
| `alembic/versions/0003c_fix_quality_reports_cols.py` | 补 quality_reports 10 个缺失列 |
| `alembic/versions/0003d_add_geometry_type.py` | 添加 geometry_type 列 + 索引 |
| `docs/revision_v2_4.md` | 本文档 |
| `docs/technical_spec_v2_4.md` | 技术方案 |
| `docs/test_manual_v2_4.md` | 测试手册 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/models/stored_file.py` | 新增 geometry_type + data_type + field_schema + upload_id 等 11 个字段 |
| `app/schemas/storage.py` | StoredFileBrief / StoredFileUploadResponse 新增 geometry_type + data_standard_name |
| `app/services/format_detection.py` | BOM 修复 + detect_geometry_type() + normalize_geometry_type() |
| `app/api/v1/data.py` | upload 端点扩展标准分类参数 + list_files 支持 geometry_type 过滤 + stats 含 by_geometry_type |
| `app/frontend/index.html` | 上传配置面板重写 + 数据类型筛选 + 标准列表操作按钮 + 编辑弹窗 |

---

## 依赖变更

无新增依赖。已有:
- `shapely` — OBJ 3d 几何检测
- `pandas` — CSV/XLSX 字段结构提取
- `fiona/GDAL` — SHP/GPKG/KML 几何类型读取

---

## 数据库迁移

### 0003b_fix_standard_id.py
```sql
ALTER TABLE stored_files ADD COLUMN IF NOT EXISTS standard_id VARCHAR(36)
  REFERENCES data_standards(id) ON DELETE SET NULL;
ALTER TABLE stored_files ADD COLUMN IF NOT EXISTS standard_table_id VARCHAR(36)
  REFERENCES standard_tables(id) ON DELETE SET NULL;
```

### 0003c_fix_quality_reports_cols.py
```sql
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS standard_table_id VARCHAR(36);
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS attribute_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS topology_summary JSONB;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS topology_config JSONB;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_completeness FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_accuracy FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_consistency FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_uniqueness FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_topology FLOAT DEFAULT 0;
ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
```

### 0003d_add_geometry_type.py
```sql
ALTER TABLE stored_files ADD COLUMN IF NOT EXISTS geometry_type VARCHAR(32);
CREATE INDEX IF NOT EXISTS ix_stored_files_geometry_type ON stored_files(geometry_type);
-- 回填已有文件的 geometry_type（基于 file_format 推断）
UPDATE stored_files SET geometry_type = 'Point' WHERE file_format = 'shp' AND geometry_type IS NULL;
UPDATE stored_files SET geometry_type = 'table' WHERE file_format IN ('csv','xlsx') AND geometry_type IS NULL;
```
