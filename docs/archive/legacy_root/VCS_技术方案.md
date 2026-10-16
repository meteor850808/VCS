# VCS 矢量转换服务 — 技术方案

> 文档版本：v2.3 | 编制日期：2026-06-18 | 状态：持续迭代

---

## 1. 项目概述

### 1.1 建设目标

建设一套**矢量数据全生命周期管理中间件**，覆盖矢量文件的格式转换、坐标系变换、质量检查、数据存储与分析能力。以微服务架构提供 REST API + WebSocket 实时推送 + SPA 前端，支撑 GIS 业务系统的数据接入与治理需求。

### 1.2 适用范围

- 国土/规划/测绘行业的矢量数据交换
- 多源异构 GIS 数据格式统一入库
- 数据质量自动化检查与报告
- 空间数据的长期存储、版本管理与增量同步

### 1.3 核心指标

| 指标 | 目标值 |
|------|--------|
| 单文件上传限制 | 500 MB |
| 批量转换最大文件数 | 50 个 |
| 支持的输入格式 | 12 种（SHP/GeoJSON/KML/KMZ/DXF/DWG/DGN/GML/GPKG/TIFF/MDB/CSV） |
| 支持的输出格式 | 6 种（GeoJSON/SHP/GPKG/KML/GML/DXF） |
| 坐标系支持 | EPSG 数据库 + 中文别名映射 |
| 质量检查维度 | 5 维（完整性/准确性/一致性/唯一性/及时性） |
| API 端点总数 | 42+（9 个路由模块） |
| 并发 Worker | 水平可扩展（Docker Compose replicas） |
| 数据库 | PostgreSQL + PostGIS |
| 99% 请求延迟 | < 500ms（不含文件传输） |

---

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          客户端层                                    │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  SPA 页面 │  │ API 测试台 │  │ 第三方系统 │  │ Swagger / Redoc  │   │
│  │ (index)   │  │ (test)    │  │ (HTTP)   │  │ (API 文档)       │   │
│  └─────┬─────┘  └─────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
│        │              │             │                  │             │
│        └──────────────┴─────────────┴──────────────────┘             │
│                          HTTP / WebSocket                            │
└─────────────────────────────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────────────────────────────────────────┐
│                         API 网关层 (FastAPI)                          │
│  ┌─────────┐ ┌────────┐ ┌────────┐ ┌───────┐ ┌───────┐ ┌────────┐  │
│  │ CORS    │ │ 安全头  │ │ 限流   │ │ 追踪  │ │ GZip  │ │ 路由   │  │
│  │ 中间件  │ │ 中间件  │ │ slowapi│ │ trace │ │ 压缩  │ │ 分发   │  │
│  └─────────┘ └────────┘ └────────┘ └───────┘ └───────┘ └────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────────────────────────────────────────┐
│                          业务逻辑层                                    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     API 路由模块 (9个)                         │   │
│  │  ┌─────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐  │   │
│  │  │ 格式转换 │ │ 批量   │ │ 质量控制│ │ 数据存储 │ │ 数据标准  │  │   │
│  │  │ convert │ │ batch  │ │ quality │ │ data   │ │ standards │  │   │
│  │  └─────────┘ └────────┘ └────────┘ └────────┘ └──────────┘  │   │
│  │  ┌─────────┐ ┌────────┐ ┌────────────┐ ┌──────────┐         │   │
│  │  │ 空间处理 │ │ 管理   │ │ 质检规则   │ │ WebSocket │         │   │
│  │  │ spatial │ │ admin  │ │ quality_   │ │ ws       │         │   │
│  │  │         │ │        │ │ rules      │ │          │         │   │
│  │  └─────────┘ └────────┘ └────────────┘ └──────────┘         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     服务层 (18+ 服务)                          │   │
│  │  ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────┐  │   │
│  │  │ 格式检测   │ │ 属性处理  │ │ CRS 变换  │ │ 几何修复    │  │   │
│  │  │ detection │ │ attribute│ │ crs_      │ │ geometry_   │  │   │
│  │  │           │ │ _process │ │ transform  │ │ repair      │  │   │
│  │  └────────────┘ └──────────┘ └───────────┘ └─────────────┘  │   │
│  │  ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────┐  │   │
│  │  │ 质量检查   │ │ 规则引擎  │ │ 评分模型  │ │ 字段画像    │  │   │
│  │  │ checker   │ │ engine   │ │ scoring   │ │ profiler    │  │   │
│  │  └────────────┘ └──────────┘ └───────────┘ └─────────────┘  │   │
│  │  ┌────────────┐ ┌──────────┐ ┌───────────┐                   │   │
│  │  │ Feature    │ │ 空间加载 │ │ 增量同步  │ ← 扩展模块       │   │
│  │  │ Store      │ │ loader   │ │ sync      │                   │   │
│  │  └────────────┘ └──────────┘ └───────────┘                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────────────────────────────────────────┐
│                         数据持久化层                                    │
│                                                                      │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐      │
│  │     PostgreSQL + PostGIS │  │         Redis 3+             │      │
│  │                         │  │                              │      │
│  │  ┌───────────────────┐  │  │  ┌────────────────────────┐ │      │
│  │  │ 业务数据            │  │  │  │ 消息队列 (Celery)     │ │      │
│  │  │ - api_keys         │  │  │  │ Pub/Sub (WS 进度)     │ │      │
│  │  │ - conversion_jobs  │  │  │  │ 缓存 (API Key/热数据) │ │      │
│  │  │ - batch_jobs       │  │  │  └────────────────────────┘ │      │
│  │  │ - quality_reports  │  │  └──────────────────────────────┘      │
│  │  │ - quality_rules    │  │                                       │
│  │  │ - quality_results  │  │                                       │
│  │  │ - quality_violations│  │                                       │
│  │  │ - stored_files     │  │                                       │
│  │  ├───────────────────┤  │                                       │
│  │  │ 空间数据 (PostGIS)  │  │                                       │
│  │  │ - point_features  │  │                                       │
│  │  │ - line_features   │  │                                       │
│  │  │ - polygon_features│  │                                       │
│  │  ├───────────────────┤  │                                       │
│  │  │ 元数据              │  │                                       │
│  │  │ - data_standards  │  │                                       │
│  │  │ - daily_snapshots │  │                                       │
│  │  └───────────────────┘  │                                       │
│  └─────────────────────────┘  └──────────────────────────────┘      │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                 异步任务层 (Celery Worker)                     │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐  │   │
│  │  │ 转换任务      │ │ 批量协调      │ │ 定时任务            │  │   │
│  │  │ tasks.py     │ │ batch_tasks  │ │ beat_schedule       │  │   │
│  │  │ (5 步流水线)  │ │ .py         │ │ (清理/快照)          │  │   │
│  │  └──────────────┘ └──────────────┘ └──────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 分层设计

| 层次 | 职责 | 关键技术 |
|------|------|----------|
| **客户端层** | 用户交互、API 测试、第三方集成 | Vanilla JS SPA、Leaflet 1.9、Chart.js |
| **API 网关层** | 请求路由、鉴权、限流、追踪、压缩 | FastAPI Middleware、slowapi、structlog |
| **业务逻辑层** | 领域服务编排、规则引擎、数据管道 | FastAPI Depends、Service Protocol 接口 |
| **数据持久化层** | 关系存储、空间索引、缓存、消息队列 | SQLAlchemy 2.0 async、PostGIS GIST、Redis |
| **异步任务层** | 耗时任务、批量协调、定时调度 | Celery 5、Redis Pub/Sub |

### 2.3 请求生命周期

```
HTTP Request → CORS → SecurityHeaders → TraceID → GZip → 
limiter → Auth(deps) → Route Handler → Service Layer → 
DB/Redis/Celery → Response → JSON(Response Model)
```

```
WebSocket 生命周期：
Client Connect → 握手消息鉴权(10s超时) → Redis PubSub Subscribe →
心跳(30s) → 消息推送 → 断开清理
```

---

## 3. 核心模块设计

### 3.1 格式转换模块

#### 架构

```
upload(dst_crs, output_format, ...)
    │
    ├─ 1. validate_filename()       ── 安全校验
    ├─ 2. 分块流式写入磁盘          ── 1MB chunk
    ├─ 3. 异步提交 Celery 任务      ── job_id 返回
    │
    └─ Celery Worker 流水线
         ├─ Step 1: format_detection    ── Fiona + chardet
         ├─ Step 2: crs_transform       ── pyproj.Transformer
         ├─ Step 3: attribute_process   ── 编码转码 + 字段过滤
         ├─ Step 4: geometry_repair     ── buffer(0) + make_valid
         ├─ Step 5: script_generation   ── ogr2ogr / Fiona 脚本
         ├─ Step 6: sandbox_exec        ── 语法预检查
         └─ Step 7: full_exec           ── 全量执行
```

#### 格式检测策略

```
优先级: Magic Bytes > 扩展名 > 内容试探
  ① Magic Bytes 识别: GeoJSON({}), SHP(\x00\x00\x27\x0a), 
     KML(<kml), GPKG(SQLite header), TIFF(II/MM)
  ② 扩展名验证: 白名单 ALLOWED_EXTENSIONS (18 种)
  ③ 编码检测: chardet 全量扫描 → 基于置信度选择
  ④ CRS 检测: Fiona crs → EPSG 数据库匹配 → WKT 解析
```

#### 脚本生成

```python
# 双引擎策略
if script_type == "ogr2ogr":
    # 适用于大数据量、有 GDAL 环境
    ogr2ogr -f "GeoJSON" output.geojson input.shp \
      -s_srs EPSG:4326 -t_srs EPSG:4490 \
      -nlt PROMOTE_TO_MULTI -lco ENCODING=UTF-8
else:
    # Fiona + pyproj，适用于云环境无 GDAL
    with fiona.open(src) as s:
        with fiona.open(dst, driver=, schema=, crs=) as d:
            for feat in s:
                geom = transform_geom(src_crs, dst_crs, feat.geom)
                d.write({"geometry": geom, "properties": ...})
```

### 3.2 质量检查模块

#### 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                    API 层 (quality.py)                       │
│  POST /check  GET /{id}  GET /html  GET /json  GET /export  │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                    服务层 (quality_checker.py)                │
│                                                              │
│  detect_format() → process_attributes() → null_analysis()    │
│  → critical_fields_check() → constraint_violations() →       │
│  topology_check() → calculate_score() → build_issues()       │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                  规则引擎 (rule_engine.py)                    │
│                                                              │
│  NOT_NULL     → 字段不可为空检查                              │
│  VALUE_IN_SET → 枚举值合法性检查                              │
│  TYPE_MATCH   → 字段类型匹配检查                              │
│  LENGTH_LIMIT → 字段长度上限检查                              │
│  (可扩展)     → 自定义规则插槽                                │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                  规则生成器 (rule_generator.py)                │
│                                                              │
│  DataStandardField → QualityRule (自动/手动)                  │
│  nullable=False    → NOT_NULL rule                           │
│  allowed_values[]  → VALUE_IN_SET rule                       │
│  field_type        → TYPE_MATCH rule                         │
│  length > 0        → LENGTH_LIMIT rule                       │
└──────────────────────────────────────────────────────────────┘
```

#### 五维评分模型

```
completeness = 1 - Σ(null_rate × weight)           权重 0.25
accuracy     = 1 - type_mismatches / total_checks   权重 0.25
consistency  = 1 - violations / total_checks         权重 0.20
uniqueness   = 1 - duplicates / total_features       权重 0.15
timeliness   = min(1, 30 / days_since_update)        权重 0.15

score_overall = Σ(dimension × dimension_weight)
```

#### 评分区间

| 评分 | 颜色 | 含义 |
|------|------|------|
| ≥ 0.85 | 🟢 绿色 | 数据质量优秀 |
| ≥ 0.60 | 🟡 橙色 | 数据质量一般，需关注 |
| < 0.60 | 🔴 红色 | 数据质量较差，需整改 |

### 3.3 数据存储模块

#### 文件存储策略

```
上传文件 → PostgreSQL BYTEA 列（stored_files.file_data）
  ├─ 压缩包 → 解压 → 逐文件入库(stored_files)
  ├─ GDB    → 转化为 GeoJSON → 逐图层入库
  ├─ 矢量文件 → 空间要素展开(spatial_features 三表)
  └─ 其他文件 → 直接存储为二进制
```

#### 空间要素分表设计

```
物理分表策略：
  point_features      ← Point / MultiPoint (POINT/MULTIPOINT)
  line_features       ← LineString / MultiLineString (LINESTRING/MULTILINESTRING)
  polygon_features    ← Polygon / MultiPolygon (POLYGON/MULTIPOLYGON)

每张表公共列：
  id VARCHAR(36) PK
  stored_file_id VARCHAR(36) INDEX
  api_key_id VARCHAR(36) INDEX
  layer_name VARCHAR(256)
  geometry GEOMETRY(GEOMETRY, 4326) + GIST INDEX

跨表查询：
  ALL_FEATURE_TABLES = [PointFeature, LineFeature, PolygonFeature]
  query = UNION ALL(SELECT ... FROM point_features WHERE ...)
                  UNION ALL(SELECT ... FROM line_features WHERE ...)
                  UNION ALL(SELECT ... FROM polygon_features WHERE ...)
```

#### 数据生命周期

```
Hot 层 (0-7 天):  SSD 存储，快速读写
Warm 层 (7-30天): HDD 归档，仅保留元数据
Cold 层 (>30天):  删除物理文件，保留元数据

自动调度: Celery Beat → cleanup_temp_files (每6小时)
```

### 3.4 质检规则引擎

#### 规则类型

```python
class RuleType(str, Enum):
    NOT_NULL      = "NOT_NULL"       # 字段不可为空
    UNIQUE        = "UNIQUE"         # 字段值唯一
    VALUE_IN_SET  = "VALUE_IN_SET"   # 值必须在允许列表中
    TYPE_MATCH    = "TYPE_MATCH"     # 字段类型匹配
    LENGTH_LIMIT  = "LENGTH_LIMIT"   # 字段长度上限
    PATTERN_MATCH = "PATTERN_MATCH"  # 正则表达式匹配
    RANGE_CHECK   = "RANGE_CHECK"    # 数值范围检查
```

#### 执行流程

```
load_rules(api_key_id, standard_id=None)
    → 只加载 enabled=True 的规则
    → 按 rule_type 分发到对应的 check_rule_* 函数
    → 每个规则与数据字段通过 field_pattern 匹配（支持通配符）
    → 对采样数据逐行检查
    → 汇总 RuleCheckResult → 写入 quality_rule_results
    → 写入 quality_violations（可溯源至 feature_index）
    → 更新 quality_reports 综合评分
```

---

## 4. 数据模型设计

### 4.1 实体关系图（核心表）

```
api_keys
    │
    ├──< conversion_jobs      (api_key_id)
    ├──< batch_jobs           (api_key_id)
    ├──< quality_reports      (api_key_id)
    ├──< stored_files         (api_key_id)
    ├──< data_standards       (api_key_id)
    └──< quality_rules        (api_key_id)
               │
               └──< quality_rule_results (rule_id 可空)
                        │
                        └──< quality_violations (rule_result_id 可空)

stored_files
    │
    ├──< point_features       (stored_file_id)
    ├──< line_features        (stored_file_id)
    └──< polygon_features     (stored_file_id)

quality_reports
    │
    └──< quality_rule_results (report_id)

data_standards
    │
    └──< data_standard_sheets (standard_id)
              │
              └──< data_standard_fields (sheet_id)
                          │
                          └──< quality_rules (standard_field_id 可空)
```

### 4.2 关键表 Schema

**stored_files** — 文件二进制存储

| 列 | 类型 | 说明 |
|-----|------|------|
| id | VARCHAR(36) PK | UUID |
| api_key_id | VARCHAR(36) FK | 所属用户 |
| original_name | VARCHAR(512) | 原始文件名 |
| file_data | BYTEA | 文件二进制内容 |
| file_size | BIGINT | 文件大小（字节） |
| content_type | VARCHAR(256) | MIME 类型 |
| is_archive | BOOLEAN | 是否为压缩包 |
| created_at | TIMESTAMPTZ | 上传时间 |

**quality_rules** — 质检规则定义

| 列 | 类型 | 说明 |
|-----|------|------|
| id | VARCHAR(36) PK | UUID |
| api_key_id | VARCHAR(36) FK | 所属用户 |
| standard_field_id | VARCHAR(36) FK | 关联标准字段（可空） |
| rule_type | VARCHAR(32) | NOT_NULL / VALUE_IN_SET / ... |
| field_pattern | VARCHAR(256) | 字段名匹配模式（支持通配符） |
| params | JSONB | {allowed_values:[], max_length:100, ...} |
| enabled | BOOLEAN | 是否启用 |
| is_auto | BOOLEAN | 自动生成/手动创建 |
| severity | VARCHAR(16) | critical / warning / info |

**quality_violations** — 违规明细（按月分区）

| 列 | 类型 | 说明 |
|-----|------|------|
| id | VARCHAR(36) PK | UUID |
| report_id | VARCHAR(36) FK | 所属报告 |
| rule_result_id | VARCHAR(36) FK | 所属规则结果 |
| field_name | VARCHAR(256) | 字段名 |
| issue_type | VARCHAR(32) | 违规类型 |
| feature_index | INTEGER | 要素索引（可定位到具体行） |
| expected | TEXT | 期望值 |
| actual | TEXT | 实际值 |
| created_at | TIMESTAMPTZ | 创建时间 |

---

## 5. API 设计

### 5.1 设计原则

1. **RESTful**: 资源型 URI、HTTP 动词语义化
2. **统一鉴权**: `X-API-Key` 请求头 + `X-Admin-Init-Key` 管理密钥
3. **分页规范**: `?page=1&page_size=20` → `{items, total, page, page_size}`
4. **错误格式**: `{"detail": "人类可读的错误描述"}`
5. **异步任务**: 耗时操作返回 `202 Accepted` + `job_id`，通过 WebSocket 推送进度
6. **限流分层**: strict(5/min) → standard(10/min) → moderate(20/min) → relaxed(60/min)

### 5.2 端点总表

| 模块 | 方法 | 路径 | 限流 | 说明 |
|------|------|------|------|------|
| 系统 | GET | `/health` | — | 健康检查 |
| 系统 | GET | `/api/v1/capabilities` | — | 能力查询 |
| 转换 | POST | `/api/v1/convert` | 10/min | 提交转换任务 |
| 转换 | GET | `/api/v1/jobs` | 60/min | 任务列表 |
| 转换 | GET | `/api/v1/jobs/{id}` | 60/min | 任务详情 |
| 转换 | DELETE | `/api/v1/jobs/{id}` | 60/min | 取消任务 |
| 转换 | POST | `/api/v1/detect` | 20/min | 格式预检 |
| 批量 | POST | `/api/v1/batch` | 5/min | 批量转换 |
| 批量 | GET | `/api/v1/batch` | 60/min | 批量列表 |
| 质检 | POST | `/api/v1/quality/check` | 5/min | 执行质检 |
| 质检 | GET | `/api/v1/quality/{id}/export` | 20/min | 导出违规 |
| 数据 | POST | `/api/v1/data/upload` | 5/min | 上传文件 |
| 数据 | GET | `/api/v1/data` | 60/min | 文件列表 |
| 空间 | POST | `/api/v1/spatial/{id}/transform` | 10/min | 坐标变换 |
| 规则 | GET | `/api/v1/quality-rules` | 60/min | 规则列表 |
| 规则 | POST | `/api/v1/quality-rules` | 20/min | 创建规则 |
| 规则 | POST | `/api/v1/quality-rules/generate/{id}` | 10/min | 自动生成规则 |
| 管理 | POST | `/api/v1/admin/apikeys` | 5/min | 创建 API Key |
| WS | WS | `/api/v1/ws/progress` | — | 实时进度 |

（完整列表含 42+ 端点，详见 README.md §API 端点索引）

### 5.3 鉴权方案

```python
# API Key 生成
raw_key = "vcs_" + secrets.token_urlsafe(32)          # 43 字符
key_hash = hashlib.sha256(raw_key.encode()).hexdigest()  # 64 字符 SHA-256

# 验证（O(1) 查询 + Redis 缓存 TTL 60s）
key_hash = hash_api_key(raw_key)
redis.get(f"apikey:{key_hash}")  # 缓存命中 → 直接返回
SELECT * FROM api_keys WHERE key_hash=? AND is_active=true  # 缓存未命中 → 回源 DB
redis.setex(f"apikey:{key_hash}", 60, "valid")  # 异步写缓存

# WebSocket 鉴权（首条消息握手）
Client → {"api_key": "vcs_xxx", "job_id": "xxx"}
Server → 10s 超时 → hash 查询 → 鉴权通过/拒绝
```

---

## 6. 部署架构

### 6.1 Docker Compose 部署

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                         │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │ PostgreSQL│  │  Redis   │  │   API    │  │ Worker  │  │
│  │ +PostGIS  │  │  7       │  │ FastAPI  │  │ Celery  │  │
│  │  15       │  │          │  │ 4 Worker │  │ 4 conc  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘  │
│       │             │             │              │       │
│       └─────────────┴─────────────┴──────────────┘       │
│                         Network (vcs_net)                 │
│                                                          │
│  ┌──────────┐  ┌──────────┐                               │
│  │  Flower  │  │   Beat   │                               │
│  │ 监控 UI  │  │ 定时任务  │                               │
│  └──────────┘  └──────────┘                               │
└─────────────────────────────────────────────────────────┘
```

### 6.2 裸机部署（Windows/Linux）

```bash
# 环境
Python 3.12+ | PostgreSQL 14+ + PostGIS 3+ | Redis 3.0+ (RESP2 兼容) | GDAL (可选)

# 启动
pip install -r requirements.txt
python scripts/init_db.py
uvicorn app.main:app --host 0.0.0.0 --port 45678 --workers 4
celery -A app.workers.celery_app worker --loglevel=info -Q vector_conversion
```

### 6.3 一键重启脚本

```bash
# restart.sh (Git Bash)
kill $(lsof -ti:45678) 2>/dev/null
nohup .venv/Scripts/uvicorn app.main:app ... &
echo "服务已重启"

# restart.bat (Windows CMD)
taskkill /f /fi "WINDOWTITLE eq uvicorn*" 2>nul
start /B "" ".venv\Scripts\uvicorn" app.main:app --host 127.0.0.1 --port 45678
```

---

## 7. 安全方案

### 7.1 传输安全

- 响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`X-XSS-Protection: 1; mode=block`
- 生产环境 HSTS：`Strict-Transport-Security: max-age=31536000`
- GZip 压缩减少暴露面

### 7.2 输入安全

```
文件名验证:
  ├─ 拒绝路径遍历: ".." "/" "\" "\x00"
  ├─ 白名单扩展名: ALLOWED_EXTENSIONS (18 种矢量格式)
  └─ 危险扩展名拦截: DANGEROUS_EXTENSIONS (.exe .bat .py .sh 等)

请求体限制:
  └─ Middleware 拦截 Content-Length > max_upload_size_mb

SQL 注入防御:
  └─ 全程参数化查询 (SQLAlchemy ORM + text() 参数绑定)
```

### 7.3 鉴权安全

```
API Key:
  ├─ secrets.token_urlsafe(32) 生成 (192 位熵)
  ├─ SHA-256 哈希存储 (不可逆)
  ├─ hmac.compare_digest 常量时间比较 (timing attack 防护)
  └─ is_active 软删除 (保留审计痕迹)

管理员密钥:
  ├─ X-Admin-Init-Key 请求头
  └─ 环境变量注入，非代码硬编码
```

### 7.4 运行时安全

- 非 root 容器用户 (Dockerfile USER appuser)
- 速率限制 (slowapi 令牌桶，5/min ~ 100/min)
- 结构化审计日志 (structlog + trace_id)
- 依赖版本锁定 (requirements.txt)

---

## 8. 监控与运维

### 8.1 可观测性

| 维度 | 实现方式 |
|------|----------|
| 日志 | structlog JSON 行输出，含 trace_id / 请求耗时 |
| 指标 | Prometheus (`/metrics` 端点)、slowapi 限流计数 |
| 追踪 | X-Trace-ID 请求头接力、响应头回传 |
| 健康 | `/health`（PG + Redis 状态，10 秒缓存） |
| 定时 | Celery Beat 清理 + 质量快照 + 指标记录 |

### 8.2 数据管理

- 违规明细保留 90 天（quality_violations 按月分区）
- 每日快照保留 365 天（quality_daily_snapshots）
- 临时文件 6 小时间隔清理（cleanup_temp_files）
- 存储生命周期自动迁移（Hot → Warm → Cold）

### 8.3 数据库备份

```bash
# PostgreSQL
pg_dump -Fc vcs_db > vcs_db_$(date +%Y%m%d).dump

# 空间数据索引重建
REINDEX DATABASE vcs_db;
```

---

## 9. 开发与测试

### 9.1 开发规范

| 规范 | 要求 |
|------|------|
| Python 类型注解 | 函数签名必须包含类型注解 |
| 异常处理 | 使用 `raise ... from e`，不吞异常 |
| 异步 | 数据库操作使用 async SQLAlchemy 2.0 风格 |
| 文件操作 | 必须使用 `with` 语句 |
| 函数长度 | 不超过 80 行 |
| 资源管理 | 循环内不重复查询外部资源 |
| 提交规范 | Conventional Commits |

### 9.2 测试策略

```bash
# 层级
单元测试 → 服务层测试 → API 集成测试

# 命令
pytest tests/test_api.py -v           # 全部测试
pytest tests/test_api.py::TestQuality -v   # 质检测试
pytest tests/test_api.py -k "security"    # 安全测试

# 测试数据库
SQLite in-memory（无需外部 PostgreSQL）
CELERY_TASK_ALWAYS_EAGER=true（无需 Redis/Worker）
```

### 9.3 技术债务跟踪

| 项目 | 状态 | 说明 |
|------|------|------|
| ValidationPipeline 死代码 | ⏸ 待接入 | L1-L4 验证管道已实现，未连接 API |
| QualityCheckConfig 死代码 | ⏸ 待接入 | 配置类已定义，未实例化 |
| 审计日志未启用 | ⏸ 待接入 | install_audit_hooks 未在 main.py 调用 |
| 质检报告 DELETE 接口 | 📋 待添加 | 当前仅数据标准有删除接口 |
| anomaly_detector.py | 📋 待实现 | 异常值检测脚本已规划未编码 |

---

## 10. 版本规划

| 版本 | 阶段 | 内容 |
|------|------|------|
| v1.0 | Phase 1 | 基础转换 + 质量检查 + 前端 SPA |
| v1.1 | Phase 2 | 数据存储 + 空间分表 + 数据标准 |
| v1.2 | Phase 3 | 前端重构 + BUG 修复 |
| v2.0 | Phase 4-6 | 外部优化采纳 + Alembic + CI/CD |
| v2.1 | Phase 7 | 架构加固 + 安全增强 |
| v2.2 | Phase 8 | 数据管理优化 |
| **v2.3** | **Phase 9** | **规则引擎 + 五维评分 + 规则管理 API** |
| v2.4 | Phase B | 字段画像 + 异常检测 |
| v2.5 | Phase C | 报告可视化升级 + 趋势分析 |
| v2.6 | Phase D | 前端质检工作台 |
