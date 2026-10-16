# VCS — 矢量转换服务 (Vector Conversion Service)

矢量数据格式转换、坐标系变换、质量检查与空间数据存储一体化微服务。

**架构**：FastAPI + SQLAlchemy 2.0 async + PostgreSQL/PostGIS + Celery + Redis

---

## 快速开始

### 环境要求

- Python 3.12+
- PostgreSQL 14+（PostGIS 3.0+）
- Redis 3.0+（Windows 移植版 3.x 亦可，代码已做 RESP2 兼容；Linux 推荐 6+）
- GDAL（ogr2ogr，可选）

### 安装

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 配置

编辑 `.env` 文件（从 `.env.example` 复制）后执行：

```bash
python scripts/init_db.py
uvicorn app.main:app --host 127.0.0.1 --port 45679 --reload
```

### 访问

| 服务 | 地址 |
|------|------|
| SPA 前端 | <http://127.0.0.1:45679/app/> |
| API 测试台 | <http://127.0.0.1:45679/app/test.html> |
| Swagger 文档 | <http://127.0.0.1:45679/docs> |
| Redoc | <http://127.0.0.1:45679/redoc> |

---

## Docker 生产部署（内网 Linux 单机）

面向内网单机、自签 TLS、离线交付的生产部署路径（v2.24.0）。

```bash
# 1) 生成 secrets（POSTGRES_PASSWORD / ADMIN_INIT_KEY / FLOWER_BASIC_AUTH）
sh docker/scripts/gen_secrets.sh      # 或: make secrets
# 2) 生成自签证书
sh docker/scripts/gen_certs.sh        # 或: make certs
# 3) 复制生产 env
cp .env.prod.example .env
# 4) 起服（基线 + 生产覆盖层）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# 或: make prod-up
```

访问 `https://<host>/app/`（HTTPS，自签证书浏览器需手动信任）；Nginx 反代 `/api/`、`/ws`、
`/health`、`/docs`，并托管前端静态；80 自动 301 → 443。

**离线交付**：在联网机 `docker compose ... build` 后 `docker build` 产物用
`docker save -o vcs-images.tar <镜像...>` 导出 → 目标机 `docker load -i vcs-images.tar`。

**容器内 DWG/MDB**：镜像内置 LibreDWG（`dwg2dxf`，源码构建）+ mdbtools ODBC 驱动 ——
DWG 转换与 MDB **读取**在 Linux 可用；MDB **导出**依赖 Windows（ADOX/ACE），非 Windows
会返回明确错误，请改用 GeoJSON/Shapefile/GeoPackage/CSV。

---

## 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI (async, Python 3.12+) |
| ORM | SQLAlchemy 2.0 (async) |
| 数据库 | PostgreSQL + PostGIS 15 |
| 缓存/消息 | Redis 3+（RESP2 兼容，见下方说明） |
| 任务队列 | Celery 5.4 |
| GIS 核心 | Fiona 1.9+, Shapely 2.0+, PyProj 3.6+, geoalchemy2 |
| 前端 | 纯 JS SPA, Leaflet 1.9 地图预览 |
| 认证 | API Key (SHA-256 + HMAC 常量时间比较) |
| 监控 | Prometheus metrics / structlog 结构化日志 / slowapi 限流 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      客户端 (SPA / API)                      │
└──────────────┬──────────────────────────────┬──────────────┘
               │ HTTP/WebSocket               │
               ▼                              ▼
┌─────────────────────────┐  ┌──────────────────────────────┐
│     FastAPI 主进程       │  │   Celery Worker 进程          │
│                         │  │                              │
│  app/                    │  │  app/workers/                │
│  ├── main.py            │  │  ├── tasks.py                │
│  ├── api/v1/ (8 路由)   │  │  ├── batch_tasks.py          │
│  ├── services/ (15+)    │  │  ├── celery_app.py           │
│  ├── models/ (10 ORM)   │  │  └── beat_schedule.py        │
│  ├── schemas/ (9 模块)  │  │                              │
│  ├── core/ (安全/缓存)   │  └──────────────────────────────┘
│  └── workers/            │
└──────┬──────────┬───────┘
       │          │
       ▼          ▼
┌──────────┐ ┌──────────┐
│PostgreSQL│ │  Redis   │
│  +GiST   │ │  Pub/Sub │
│  +PostGIS│ │  缓存    │
└──────────┘ └──────────┘
```

---

## 快速启动

### 一键部署（推荐）

提供 Windows / Linux 两个版本的一键脚本，自动完成：检查/启动 Redis + PostgreSQL → 创建 venv → 安装依赖 → 数据库迁移 → 启动 API + Celery Worker + Flower 监控。

#### Windows

```bat
:: 前置：安装 Python 3.12+、PostgreSQL 16（scripts\install_postgres.ps1）、Redis for Windows
:: Redis 注册为服务（仅首次）：
"C:\Program Files\Redis\redis-server.exe" --service-install "C:\Program Files\Redis\redis.windows-service.conf" --service-name Redis
net start Redis

:: 一键部署 / 重启（覆盖全部组件）
deploy_windows.bat
```

脚本输出：API 前端 `http://127.0.0.1:45679/app/`、Flower 监控 `http://127.0.0.1:5555`。

> 注：`scripts\start_redis.bat` 已改为 Windows 服务方式（原 WSL 启动方式废弃）。

#### Linux

```bash
# 需 root（自动 apt 安装缺失的 Redis/PostgreSQL/GDAL）
sudo bash deploy_linux.sh

# 非 root 也可运行（跳过系统依赖安装，仅管理项目进程）
bash deploy_linux.sh
```

### Docker Compose 部署

```bash
# 1. 复制环境变量模板
cp .env.example .env
#    Docker 模式无需修改 .env 的数据库/Redis 连接串 —— docker-compose.yml
#    已自动覆盖为 compose 服务名（postgres / redis）。按需修改：
#    - ADMIN_INIT_KEY：超级管理员初始化 Key
#    - FLOWER_BASIC_AUTH：Flower 监控面板账号密码（默认 admin:changeme）
#    （如需自定义数据库密码，必须同步修改 .env 中 DATABASE_URL 的密码部分）

# 2. 构建并启动全部服务（PostgreSQL / Redis / migrate / API / Worker / Flower / Beat）
docker compose up -d

# 3. 查看初始化日志（获取首个 API Key）
docker compose logs migrate

# 4. 验证
curl http://localhost:45679/health
#    前端入口：http://localhost:45679/app/
```

### 本地开发（Windows）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 PostgreSQL（确保已安装 PostGIS）
# 3. 启动 Redis（Windows 服务）
net start Redis
#    或：scripts\start_redis.bat

# 4. 配置环境变量
copy .env.example .env
# 修改 DATABASE_URL 为本地连接串

# 5. 初始化数据库
python scripts/init_db.py

# 6. 启动 API
uvicorn app.main:app --reload --port 45679

# 7. 启动 Worker（另开终端）
celery -A app.workers.celery_app worker --loglevel=info --pool=solo
```

---

## Redis 兼容性说明（v2.10.1）

redis-py 8.x 默认使用 RESP3 协议（连接时发送 `HELLO` 命令），而 Windows 移植版 Redis 3.x（2016 年微软版）与部分老版本不支持 `HELLO`，导致健康检查报 `unknown command 'HELLO'` 且 Celery 无法投递任务。

本项目已做全面 RESP2 兼容（`protocol=2`），可无缝运行于 Redis 3.0 ~ 7.x：

| 连接点 | 处理方式 |
|---|---|
| API 健康检查 / 缓存 / WebSocket（`main.py`、`cache.py`、`ws.py`） | `from_url(..., protocol=2)` |
| 转换探测 / 进度推送（`convert.py`、`progress_tracker.py`） | `from_url(..., protocol=2)` |
| Celery broker（kombu） | monkey patch `_connparams` 强制 `protocol=2` |
| Celery result backend（`RedisBackend`） | `__init__` 后注入 `connparams["protocol"]=2` |

> Linux 生产环境推荐 Redis 6+（完整支持 RESP3），代码同样兼容。

---

## 服务端口

| 服务 | 端口 | 说明 |
|---|---|---|
| FastAPI | 45679 | REST API + Swagger Docs（`restart.bat` 默认端口） |
| Flower | 5555 | Celery 任务监控 UI |
| PostgreSQL | 5432 | 数据库 |
| Redis | 6379 | 消息队列 |

---

## 前端页面

入口：`http://127.0.0.1:45679/app/`

| 页面 | 路径 | 功能 |
|---|---|---|
| 📊 任务看板 | `#dashboard` | 转换任务列表、进度追踪、任务详情弹窗（WebSocket 实时更新） |
| 📂 数据处理 | `#data-ops` | 上传矢量文件 + 文件管理表格（质检/坐标转换/几何修复/下载/删除） |
| 🗺 数据预览 | `#spatial-view` | Leaflet 地图浏览已入库空间要素，图层/几何类型筛选，3 种底图切换；属性表 **全字段渲染（横向滚动）** + **客户端分页（50/100/200）**，最多加载前 10000 个要素（超出提示截断）；**点击属性行 → 地图定位 + 高亮闪烁 + 行选中**（再点取消） |
| ⚑ 质量检查 | `#quality` | 紧凑质检工作台：文件栏 + 标准栏 + 可折叠检查配置（属性 9 项 / 拓扑 13 项）+ 报告表；**不选标准可执行纯拓扑检查** |
| 📋 数据标准 | `#data-standards` | 上传 Excel 标准定义、下载模板、字段约束管理 |
| 🧪 API 测试台 | `/app/test.html` | 全功能 API 测试页面（22 项测试，含创建 API Key） |

---

## API 端点索引

### 系统

```bash
# 健康检查（免鉴权）
curl http://127.0.0.1:45679/health
# → {"status":"ok","postgres":"ok","redis":"ok"}

# 服务能力查询（免鉴权）
curl http://127.0.0.1:45679/api/v1/capabilities

# API 文档
# http://127.0.0.1:45679/docs
```

### API Key 管理

需要 `X-Admin-Init-Key` 请求头（通过 `.env` 中 `ADMIN_INIT_KEY` 配置）。

```bash
# 创建 API Key（可选指定分组）
curl -X POST http://127.0.0.1:45679/api/v1/admin/apikeys \
  -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" \
  -H "Content-Type: application/json" \
  -d '{"name": "my_app", "group_id": null}'
# → {"id":"uuid","api_key":"vcs_xxx...","message":"请保管好此密钥，仅显示一次"}

# 查询 API Key 列表（含分组信息）
curl -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/admin/apikeys

# 吊销 API Key（软删除，is_active=false）
curl -X DELETE -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/admin/apikeys/{key_id}

# 永久删除 API Key（从数据库彻底移除）
curl -X DELETE -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/admin/apikeys/{key_id}/hard
```

#### 分组管理（v2.5）

同组 API Key 自动共享数据访问权限。

```bash
# 创建分组
curl -X POST http://127.0.0.1:45679/api/v1/admin/groups \
  -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" \
  -H "Content-Type: application/json" \
  -d '{"name": "team-a", "description": "A 组"}'

# 列出分组
curl -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/admin/groups

# 设置 Key 所属分组（null 则移出）
curl -X PUT http://127.0.0.1:45679/api/v1/admin/apikeys/{key_id}/group \
  -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" \
  -H "Content-Type: application/json" \
  -d '{"group_id": "分组ID"}'

# 删除分组（组内 Key 自动移出）
curl -X DELETE -H "X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/admin/groups/{group_id}
```

#### 超级管理员（v2.5）

`ADMIN_INIT_KEY` 可直接作为 `X-API-Key` 使用，查询**所有**数据（跨所有 API Key）：

```bash
curl -H "X-API-Key: VCS-admin-2026-q6k4H9nZxR3" http://127.0.0.1:45679/api/v1/data
```

### 数据文件管理

```bash
# 上传文件（支持压缩包自动解压，GDB 自动转 GeoJSON）
curl -X POST http://127.0.0.1:45679/api/v1/data/upload \
  -H "X-API-Key: vcs_xxx" \
  -F "file=@data.geojson" \
  -F "description=建筑数据"

# 查询文件列表
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data

# 存储统计
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data/stats

# 查询空间要素（GeoJSON FeatureCollection）
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data/{id}/features

# 下载文件
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data/{id}/download

# 删除文件
curl -X DELETE -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data/{id}

# 批量删除
curl -X POST -H "X-API-Key: vcs_xxx" \
  -H "Content-Type: application/json" \
  -d '["id1","id2"]' \
  http://127.0.0.1:45679/api/v1/data/batch-delete
```

### 质量检查

```bash
# 执行质量检查（v2.4+：对已入库文件执行，file_id + JSON body；standard_id 可选）
curl -X POST http://127.0.0.1:45679/api/v1/quality/check \
  -H "X-API-Key: vcs_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "<已入库文件id>",
    "standard_id": "std_id",
    "topology_config": {"t06": {"enabled": true, "overlap_tolerance": 0.0001}}
  }'
# → 200: 属性 9 项（标准驱动）+ 拓扑 13 项 + 五维评分
# → 200: 不传 standard_id 时执行纯拓扑检查（attr_summary: total=0/failed=0/passed=0）
# → 400: 无 file_id 或无任何检查项

# 查询报告列表（page_size 上限 50）
curl -H "X-API-Key: vcs_xxx" "http://127.0.0.1:45679/api/v1/quality?page_size=50"

# 查询报告详情
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality/{id}

# 查看 HTML 报告
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality/{id}/html

# 导出违规要素（GeoJSON / Excel）
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality/{id}/export?format=geojson
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality/{id}/export?format=excel
```

### 空间处理

```bash
# 坐标转换（对已入库数据执行 CRS 变换）
curl -X POST "http://127.0.0.1:45679/api/v1/spatial/{file_id}/transform?source_crs=EPSG:4326&target_crs=EPSG:4490" \
  -H "X-API-Key: vcs_xxx"

# 几何修复（make_valid + buffer(0)）
curl -X POST http://127.0.0.1:45679/api/v1/spatial/{file_id}/repair \
  -H "X-API-Key: vcs_xxx"
```

### 格式转换

```bash
# 格式预检（不上传全量文件）
curl -X POST http://127.0.0.1:45679/api/v1/detect \
  -H "X-API-Key: vcs_xxx" \
  -F "file=@data.geojson"

# 提交转换任务（需要 Redis/Celery）
curl -X POST http://127.0.0.1:45679/api/v1/convert \
  -H "X-API-Key: vcs_xxx" \
  -F "file=@data.shp" \
  -F "dst_crs=EPSG:4490" \
  -F "output_format=GeoJSON"

# 查询任务
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/jobs
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/jobs/{id}
```

### 数据标准

```bash
# 下载导入模板（Excel）
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards/template

# 导入标准定义
curl -X POST http://127.0.0.1:45679/api/v1/data-standards/import \
  -H "X-API-Key: vcs_xxx" \
  -F "file=@标准.xlsx" \
  -F "name=我的标准"

# 查询标准列表
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards

# 查询标准详情（含字段定义）
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards/{id}

# 删除标准
curl -X DELETE -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards/{id}
```

### 数据标准 v2（层组/表/字段/字典 CRUD + 版本控制）

```bash
# 标准 CRUD
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2
curl -X POST http://127.0.0.1:45679/api/v1/data-standards-v2 \
  -H "X-API-Key: vcs_xxx" -H "Content-Type: application/json" \
  -d '{"name":"标准名","code":"STD001"}'
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}
curl -X PUT -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}
curl -X DELETE -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}

# 层组管理
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/layers
curl -X POST http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/layers -H "X-API-Key: vcs_xxx" -H "Content-Type: application/json" -d '{"name":"层组名"}'

# 表与字段管理
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/tables
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/tables/{table_id}/fields
curl -X POST http://127.0.0.1:45679/api/v1/tables/{table_id}/fields -H "X-API-Key: vcs_xxx" -H "Content-Type: application/json" -d '{"name":"YWBH","data_type":"string","required":true}'

# 字典管理（值域控制）
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/dictionaries
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/dictionaries

# 版本控制（发布/历史/对比/回滚）
curl -X POST -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/publish
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/versions
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/versions/{v1}/diff/{v2}
curl -X POST -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/rollback/{version_number}

# 导入导出（JSON / Excel / CSV）
curl -X POST -F "file=@std.json" -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/import/json
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/template/json
curl -X POST -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/data-standards-v2/{standard_id}/export
```

### 质检规则库

```bash
# 规则列表 / 创建 / 更新 / 删除
curl -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality-rules
curl -X POST http://127.0.0.1:45679/api/v1/quality-rules -H "X-API-Key: vcs_xxx" -H "Content-Type: application/json" -d '{"name":"YWBH非空","rule_type":"NOT_NULL","field":"YWBH"}'

# 从标准自动生成规则
curl -X POST -H "X-API-Key: vcs_xxx" http://127.0.0.1:45679/api/v1/quality-rules/generate/{standard_id}
```

---

## 全量 API 端点矩阵

| 模块 | 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|------|
| 系统 | GET | `/health` | 健康检查（PG/Redis ping 各 2s 限时，超时返回 degraded） | 无 |
| 系统 | GET | `/api/v1/capabilities` | 能力查询 | X-API-Key |
| 转换 | POST | `/api/v1/convert` | 提交转换任务 | X-API-Key |
| 转换 | GET | `/api/v1/jobs` | 任务列表 | X-API-Key |
| 转换 | GET | `/api/v1/jobs/{id}` | 任务详情 | X-API-Key |
| 转换 | DELETE | `/api/v1/jobs/{id}` | 取消任务 | X-API-Key |
| 转换 | POST | `/api/v1/detect` | 格式预检 | X-API-Key |
| 批量 | POST | `/api/v1/batch` | 批量转换 | X-API-Key |
| 批量 | GET | `/api/v1/batch` | 批量列表 | X-API-Key |
| 批量 | GET | `/api/v1/batch/{id}` | 批量详情 | X-API-Key |
| 批量 | GET | `/api/v1/batch/{id}/jobs` | 子任务列表 | X-API-Key |
| 批量 | DELETE | `/api/v1/batch/{id}` | 删除批量 | X-API-Key |
| 质检 | POST | `/api/v1/quality/check` | 执行质检（`standard_id` 可选，为空则纯拓扑） | X-API-Key |
| 质检 | GET | `/api/v1/quality` | 报告列表（page_size 上限 50） | X-API-Key |
| 质检 | GET | `/api/v1/quality/{id}` | 报告详情 | X-API-Key |
| 质检 | GET | `/api/v1/quality/{id}/html` | HTML 报告 | X-API-Key |
| 质检 | GET | `/api/v1/quality/{id}/json` | JSON 报告 | X-API-Key |
| 质检 | GET | `/api/v1/quality/{id}/export` | 导出违规 | X-API-Key |
| 规则 | GET | `/api/v1/quality-rules` | 规则列表 | X-API-Key |
| 规则 | POST | `/api/v1/quality-rules` | 创建规则 | X-API-Key |
| 规则 | PUT | `/api/v1/quality-rules/{rule_id}` | 更新规则 | X-API-Key |
| 规则 | DELETE | `/api/v1/quality-rules/{rule_id}` | 删除规则 | X-API-Key |
| 规则 | POST | `/api/v1/quality-rules/generate/{standard_id}` | 从标准生成规则 | X-API-Key |
| 数据 | POST | `/api/v1/data/upload` | 上传文件 | X-API-Key |
| 数据 | GET | `/api/v1/data` | 文件列表 | X-API-Key |
| 数据 | GET | `/api/v1/data/stats` | 存储统计 | X-API-Key |
| 数据 | GET | `/api/v1/data/{id}` | 文件元数据 | X-API-Key |
| 数据 | GET | `/api/v1/data/{id}/features` | 空间要素 | X-API-Key |
| 数据 | GET | `/api/v1/data/{id}/download` | 下载文件 | X-API-Key |
| 数据 | DELETE | `/api/v1/data/{id}` | 删除文件 | X-API-Key |
| 数据 | POST | `/api/v1/data/batch-delete` | 批量删除 | X-API-Key |
| 数据 | POST | `/api/v1/data/{id}/quality-check` | 文件原地质检 | X-API-Key |
| 空间 | POST | `/api/v1/spatial/{id}/transform` | 坐标变换 | X-API-Key |
| 空间 | POST | `/api/v1/spatial/{id}/repair` | 几何修复 | X-API-Key |
| 标准 | POST | `/api/v1/data-standards/import` | 导入标准 | X-API-Key |
| 标准 | GET | `/api/v1/data-standards/template` | 下载模板 | X-API-Key |
| 标准 | GET | `/api/v1/data-standards` | 标准列表 | X-API-Key |
| 标准 | GET | `/api/v1/data-standards/{id}` | 标准详情 | X-API-Key |
| 标准 | DELETE | `/api/v1/data-standards/{id}` | 删除标准 | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2` | 标准列表（v2） | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2` | 创建标准（v2） | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}` | 标准详情（v2） | X-API-Key |
| 标准v2 | PUT | `/api/v1/data-standards-v2/{standard_id}` | 更新标准（v2） | X-API-Key |
| 标准v2 | DELETE | `/api/v1/data-standards-v2/{standard_id}` | 删除标准（v2） | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}/tables` | 标准全部表（扁平） | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}/layers` | 层组列表 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/layers` | 创建层组 | X-API-Key |
| 标准v2 | PUT | `/api/v1/layers/{layer_id}` | 更新层组 | X-API-Key |
| 标准v2 | DELETE | `/api/v1/layers/{layer_id}` | 删除层组 | X-API-Key |
| 标准v2 | POST | `/api/v1/layers/{layer_id}/move` | 移动层组 | X-API-Key |
| 标准v2 | POST | `/api/v1/layers/{layer_id}/copy` | 复制层组 | X-API-Key |
| 标准v2 | GET | `/api/v1/layers/{layer_id}/tables` | 层组下表列表 | X-API-Key |
| 标准v2 | POST | `/api/v1/layers/{layer_id}/tables` | 层组下建表 | X-API-Key |
| 标准v2 | GET | `/api/v1/tables/{table_id}` | 表详情 | X-API-Key |
| 标准v2 | PUT | `/api/v1/tables/{table_id}` | 更新表 | X-API-Key |
| 标准v2 | DELETE | `/api/v1/tables/{table_id}` | 删除表 | X-API-Key |
| 标准v2 | GET | `/api/v1/tables/{table_id}/fields` | 字段列表 | X-API-Key |
| 标准v2 | POST | `/api/v1/tables/{table_id}/fields` | 添加字段 | X-API-Key |
| 标准v2 | PUT | `/api/v1/fields/{field_id}` | 更新字段 | X-API-Key |
| 标准v2 | DELETE | `/api/v1/fields/{field_id}` | 删除字段 | X-API-Key |
| 标准v2 | POST | `/api/v1/tables/{table_id}/fields/reorder` | 字段排序 | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}/dictionaries` | 标准下字典列表 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/dictionaries` | 创建字典 | X-API-Key |
| 标准v2 | GET | `/api/v1/dictionaries` | 全局字典列表 | X-API-Key |
| 标准v2 | GET | `/api/v1/dictionaries/{dict_id}` | 字典详情 | X-API-Key |
| 标准v2 | PUT | `/api/v1/dictionaries/{dict_id}` | 更新字典 | X-API-Key |
| 标准v2 | DELETE | `/api/v1/dictionaries/{dict_id}` | 删除字典 | X-API-Key |
| 标准v2 | POST | `/api/v1/dictionaries/{dict_id}/items` | 批量添加字典条目 | X-API-Key |
| 标准v2 | POST | `/api/v1/dictionaries/{dict_id}/share` | 设为全局共享 | X-API-Key |
| 标准v2 | GET | `/api/v1/fields/{field_id}/dict-links` | 字段字典关联 | X-API-Key |
| 标准v2 | POST | `/api/v1/fields/{field_id}/dict-links` | 关联字典 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/publish` | 发布标准（版本快照） | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}/versions` | 版本历史 | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/{standard_id}/versions/{v1}/diff/{v2}` | 版本对比 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/rollback/{version_number}` | 回滚到指定版本 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/import/{json,excel,csv}` | 导入标准（三格式） | X-API-Key |
| 标准v2 | GET | `/api/v1/data-standards-v2/template/{json,excel,csv}` | 下载导入模板 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/export` | 导出标准 | X-API-Key |
| 标准v2 | POST | `/api/v1/data-standards-v2/{standard_id}/auto-create-table` | 按文件结构自动建表 | X-API-Key |
| 管理 | POST | `/api/v1/admin/apikeys` | 创建 Key | Admin |
| 管理 | GET | `/api/v1/admin/apikeys` | Key 列表 | Admin |
| 管理 | DELETE | `/api/v1/admin/apikeys/{id}` | 吊销 Key（软删除） | Admin |
| 管理 | PUT | `/api/v1/admin/apikeys/{id}/activate` | 重新启用已吊销 Key（v2.7） | Admin |
| 管理 | POST | `/api/v1/admin/apikeys/{id}/regenerate` | 重新生成 Key（旧钥立即失效，v2.7） | Admin |
| 管理 | DELETE | `/api/v1/admin/apikeys/{id}/hard` | 硬删除 Key（不可恢复） | Admin |
| 管理 | PUT | `/api/v1/admin/apikeys/{id}/group` | Key 指派分组 | Admin |
| 管理 | POST | `/api/v1/admin/groups` | 创建分组 | Admin |
| 管理 | GET | `/api/v1/admin/groups` | 分组列表 | Admin |
| 管理 | GET | `/api/v1/admin/groups/{id}` | 分组详情 | Admin |
| 管理 | PUT | `/api/v1/admin/groups/{id}` | 更新分组 | Admin |
| 管理 | DELETE | `/api/v1/admin/groups/{id}` | 删除分组 | Admin |
| 源数据 | GET | `/api/v1/source-tables` | 源表列表 | X-API-Key |
| 源数据 | GET | `/api/v1/source-tables/{id}` | 源表详情 | X-API-Key |
| 源数据 | POST | `/api/v1/source-tables` | 创建源表 | X-API-Key |
| 源数据 | GET | `/api/v1/source-dictionaries` | 字典列表 | X-API-Key |
| 源数据 | POST | `/api/v1/field-dict-links` | 字段字典关联 | X-API-Key |
| WS | WS | `/api/v1/ws/progress` | 实时进度 | 握手鉴权 |

---

## 项目结构

```
空间API服务/
├── app/
│   ├── main.py                     FastAPI 入口（生命周期/CORS/路由）
│   ├── config.py                   pydantic-settings 配置
│   ├── core/
│   │   ├── security.py             API Key SHA-256 生成/验证
│   │   ├── deps.py                 鉴权依赖注入（O(1) 哈希查询）
│   │   ├── constants.py            全局常量（OUTPUT_SUFFIX_MAP / CRITICAL_FIELDS）
│   │   ├── rate_limit.py           slowapi 令牌桶限流
│   │   ├── cache.py                Redis 缓存装饰器
│   │   ├── file_utils.py           文件名安全验证
│   │   ├── db_utils.py             数据标准字段加载
│   │   ├── logging_setup.py        structlog 结构化日志
│   │   └── trace_middleware.py     请求追踪（trace_id）
│   ├── db/session.py               异步 SQLAlchemy 会话管理
│   ├── models/
│   │   ├── api_key.py              API Key（SHA-256 哈希 + is_active 软删除）
│   │   ├── conversion_job.py       转换任务（含进度/脚本/修复统计）
│   │   ├── batch_job.py            批量任务
│   │   ├── quality_report.py        质检报告（含 topology_violations JSON）
│   │   ├── quality_rule.py          质检规则（支持 7 种规则类型）
│   │   ├── quality_rule_result.py   规则执行结果 + 违规明细
│   │   ├── quality_daily_snapshot.py每日质量快照（趋势分析）
│   │   ├── stored_file.py           存储文件（BYTEA）
│   │   ├── spatial_feature.py       空间要素分表（point/line/polygon + GIST）
│   │   ├── data_standard.py         数据标准（Standard → Sheet → Field 三表）
│   │   └── __init__.py              模型统一导出
│   ├── schemas/                    9 个 Pydantic 模型模块
│   ├── api/v1/
│   │   ├── router.py               路由聚合
│   │   ├── convert.py              格式转换/任务管理/格式检测
│   │   ├── admin.py                API Key 管理
│   │   ├── batch.py                批量转换
│   │   ├── quality.py              质量检查/报告/违规导出
│   │   ├── quality_rules.py        质检规则管理 CRUD + 自动生成
│   │   ├── data.py                 文件上传/管理/空间要素查询
│   │   ├── data_standards.py       数据标准导入/管理
│   │   ├── spatial.py              坐标转换/几何修复
│   │   └── ws.py                   WebSocket 实时进度
│   ├── services/                   18+ 业务服务
│   │   └── quality/                质检子包（models.py / reporter.py / rule_generator.py / rule_engine.py）
│   ├── frontend/
│   │   ├── index.html              SPA 主页（6 页一体化工作台）
│   │   └── test.html               API 全功能测试台
│   └── workers/
│       ├── celery_app.py           Celery 应用实例
│       ├── tasks.py                转换主任务（6 步骤流水线）
│       ├── batch_tasks.py          批量协调任务
│       └── beat_schedule.py        定时清理/指标快照
├── alembic/                        数据库迁移（env.py + 2 个迁移版本）
├── scripts/
│   ├── init_db.py                  初始化数据库 + 创建首个 API Key
│   └── restart.sh / restart.bat    一键重启脚本
├── tests/
│   └── test_api.py                 38 个测试用例
├── static/templates/
│   └── data_standard_template.xlsx  数据标准导入模板
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── .github/workflows/ci.yml        CI 流水线
│   ├── services/
│   │   ├── format_detection.py      Magic bytes + 扩展名 + 编码 + CRS 检测
│   │   ├── quality_checker.py       字段完整性 + 关键字段 + 约束检查 + 评分
│   │   ├── topology_checker.py      6 种拓扑规则（重叠/间隙/自交/重复/碎面/未闭合）
│   │   ├── spatial_loader.py       矢量文件读取 + PostGIS 入库
│   │   ├── archive_extractor.py    压缩包解压 + GDB 检测 + 路径遍历防护
│   │   ├── data_standard_parser.py  Excel 标准解析（中英双语表头）
│   │   ├── quality_exporter.py      违规导出（GeoJSON / Excel）
│   │   ├── crs_transform.py         CRS 解析 + pyproj 坐标变换
│   │   ├── geometry_repair.py       Shapely 几何修复（buffer(0) + make_valid）
│   │   ├── attribute_processing.py  属性读写 + 编码检测 + 字段过滤
│   │   ├── script_generation.py     ogr2ogr / Fiona 脚本生成 + 沙箱执行
│   │   └── progress_tracker.py      Redis Pub/Sub 进度推送
│   ├── workers/
│   │   ├── celery_app.py            Celery 实例
│   │   ├── tasks.py                 转换任务定义（5 步流水线）
│   │   └── batch_tasks.py           批量任务编排
│   └── frontend/                    前端 SPA
│       ├── index.html               主应用（~2200 行，6 页 SPA）
│       └── test.html                全功能 API 测试台（~1260 行，22 项测试）
├── scripts/
│   ├── init_db.py                   数据库初始化（建表 + 种子数据）
│   ├── start_redis.bat              Windows 启动 Redis 脚本
│   ├── frontend_redesign.py         前端重构脚本
│   ├── restore_standards.py         前端功能恢复脚本
│   └── *.py                         其他迁移/修复脚本
├── alembic/                         数据库迁移
├── tests/test_api.py                pytest 测试套件
├── docker-compose.yml               全栈编排（6 服务）
├── Dockerfile                       多阶段构建
├── requirements.txt                 依赖清单
└── .env.example                     环境变量模板
```

---

## 主要功能

### 支持格式

| 格式 | 说明 | 状态 |
|------|------|------|
| GeoJSON | 标准地理 JSON | ✅ 支持 |
| SHP | ESRI Shapefile | ✅ 支持 |
| GPKG | GeoPackage | ✅ 支持 |
| KML/KMZ | Google Earth 格式 | ✅ 支持 |
| DXF | AutoCAD DXF | ✅ 支持 |
| DWG | AutoCAD DWG（LibreDWG 转换） | ✅ 支持 |
| GML | Geography Markup Language | ✅ 支持 |
| GDB | ESRI File Geodatabase | ✅ 支持 |
| MDB | ESRI Personal Geodatabase | ✅ 支持 |
| GeoTIFF | 地理参考栅格 | ✅ 支持 |
| DGN | MicroStation Design | ✅ 支持 |
| OBJ | 3D 模型 | ✅ 支持 |
| CSV | 逗号分隔值 | ✅ 支持 |

### 数据上传
- 支持格式：GeoJSON, SHP, GPKG, KML, KMZ, DXF, GML, GDB, GeoTIFF, MDB, CSV
- 压缩包自动解压（ZIP, TAR, TGZ），GDB/MDB 自动转为 GeoJSON 图层
- MDB (ESRI Personal Geodatabase) 支持：通过 pyodbc + MS Access Driver 读取，提取空间表并转为 GeoJSON features 入库
- 文件入库到 PostgreSQL BYTEA，空间要素展开到 PostGIS `spatial_features` 表
- 路径遍历防护、文件类型白名单、大小限制

### 质量检查
- **字段完整性**：空值率统计、缺失字段检测
- **关键字段验证**：YWBH（业务编号）、FEATURECODE（要素代码）等 7 个字段别名专项检查
- **数据标准约束**：与导入的 Excel 标准对比字段类型、长度、可空性、枚举值
- **规则引擎**：从数据标准自动生成质检规则（NOT_NULL / VALUE_IN_SET / TYPE_MATCH / LENGTH_LIMIT），支持手动创建/编辑/启用/禁用
- **规则管理 API**：完备的规则 CRUD（创建/查询/更新/删除）+ 一键从标准自动生成
- **五维评分模型**：完整性(0.25) + 准确性(0.25) + 一致性(0.20) + 唯一性(0.15) + 及时性(0.15)
- **属性检查**（v2.4+，9 项，标准驱动）：必要图层/要素类型/属性结构/字段必填/值域控制/字段唯一/图层非空/小数位/长度限制
- **拓扑检查**（v2.4+，13 项 T01-T13）：空几何/自相交/极大面/极小面/微短线/面重叠/点重叠/线重叠/面裂隙/线连通/不规则面/环方向/多部件（8 项可调阈值）
- **纯拓扑检查**（v2.6+）：不选标准也可执行质检（仅拓扑检查，`attr_summary` 返回零值）
- **空要素校验**（v2.7+）：无图形要素文件执行质检返回 400「该文件无图形要素」；报告 `total_features` 取自数据库记录
- **质检页**（v2.6+）：紧凑工作台——文件栏 + 标准栏 + 可折叠检查配置（胶囊式横向排列）+ 报告表 + 方案保存/加载/导出
- 综合评分 0.0-1.0

### 坐标转换
- 支持 EPSG 编码（EPSG:4326, EPSG:4490 等）
- 支持中文别名（国家2000, 北京54, 西安80, WGS84）
- 基于 PyProj Transformer，递归转换所有几何对象

### 几何修复
- `buffer(0)` 修复自相交、无效几何
- `shapely.validation.make_valid` 二次修复
- 环方向纠正（右手定则）

### 数据标准
- Excel 模板下载（中英双语表头）
- 导入标准定义 → 自动解析工作表、字段类型、长度、约束
- **4-sheet 模板导入**（v2.7+）：标准信息 / 层组定义 / 数据表定义 / 字段定义 4 张工作表一次导入，自动建层组树/表/几何类型/字段；旧扁平结构自动回退解析；`.xls` 提示另存 `.xlsx`
- 字典管理（v2.7+）：创建预查重（重名 409）+ scope 枚举校验（400），前端支持重命名/删除/条目编辑
- 质检时关联标准 → 字段级约束验证

### 格式转换
- 支持 10+ 输入格式 → 6 种输出格式
- ogr2ogr / Fiona 双引擎
- 属性过滤（select_fields）、条件过滤（where_clause）
- 沙箱执行、进度追踪（Redis Pub/Sub → WebSocket）

---

## 配置说明

关键环境变量（`.env`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///...` | PostgreSQL 连接串（async） |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `ADMIN_INIT_KEY` | （必填，无默认） | 管理接口密钥；缺失/弱口令应用拒绝启动（v2.16 起） |
| `MAX_UPLOAD_SIZE_MB` | 500 | 单文件最大上传（MB） |
| `MAX_DATA_STORE_SIZE_MB` | 200 | 数据存储文件最大（MB） |
| `MAX_BATCH_SIZE` | 50 | 批量转换最大文件数 |
| `DEBUG` | false | 调试模式（CORS 全开） |

---

## 数据库模型

| 表 | 说明 |
|---|---|
| `api_keys` | API Key（SHA-256 哈希存储，含 group_id FK） |
| `key_groups` | Key 分组（团队级数据共享，v2.5） |
| `conversion_jobs` | 转换任务（状态机：PENDING→RUNNING→SUCCESS/FAILED） |
| `batch_jobs` | 批量任务（聚合多个子任务） |
| `quality_reports` | 质检报告（五维评分 + attr/topology summary，含 topology_violations JSON 字段） |
| `quality_rules` | 质检规则定义（支持 7 种规则类型，可自动生成） |
| `quality_rule_results` | 规则执行结果（一次质检对应多条结果） |
| `quality_violations` | 违规明细（每条违规溯源至具体要素） |
| `quality_daily_snapshots` | 每日质量快照（趋势分析用） |
| `stored_files` | 上传文件（BYTEA 存储，含 data_type/geometry_type/standard 关联字段） |
| `spatial_features` | 空间要素（PostGIS Geometry 列，GIST 索引） |
| `data_standards` | 数据标准定义（v2.x 重构） |
| `standard_layer_groups` | 标准层组（v2.2） |
| `standard_tables` | 标准表（v2.2） |
| `standard_fields` | 标准字段定义（v2.2） |
| `standard_dictionaries` | 标准字典（值域控制，v2.2） |
| `dictionary_items` | 字典条目（v2.2） |
| `standard_field_dict_links` | 字段-字典关联（v2.2） |
| `standard_versions` | 标准版本快照（发布/对比/回滚，v2.3） |
| `source_tables` | 源数据表（v2.2） |

---

## 测试

```bash
# 后端单元测试
pytest tests/ -v

# 前端 API 测试（浏览器）
# http://127.0.0.1:45679/app/test.html
```

---

## 版本与优化记录

**当前版本**: 2.12.0 — MDB (ESRI Personal Geodatabase) 支持

| 版本 | 日期 | 变更 |
|------|------|------|
| **2.12.0** | **2026-08-27** | **MDB (ESRI Personal Geodatabase) 支持：pyodbc + MS Access Driver 读取、格式检测归类为空间格式、upload 端点集成、mdb_processor 处理器 + extract_mdb_to_stored_files 入库流水线 + 11 项单元测试 + 集成测试** |
| **2.9.0** | **2026-08-12** | **数据字典展示拆分：全局字典 / 对应数据标准字典两个独立维度（不与表/字段关联）——后端 6 处（标准端点强制 standard + 新增全局创建端点 + 全局列表过滤 + scope 一致性）+ 前端双 tab（🌐 全局字典 / 📋 标准字典）+ 隐身脏字典清理** |
| **2.7.0** | **2026-08-04** | **使用问题批量修复（密钥启用/重生成 + 字典查重 409 + Excel 4-sheet 模板导入 + 空要素 400 校验 + 健康检查限时 + 下载 RFC 5987 + 前端 esc/fetch 通用修复）+ restart.bat 卡住修复** |
| 2.6.0 | 2026-07-31 | 质检页紧凑重构（文件栏/标准栏/可折叠检查配置/报告表）+ 无标准纯拓扑检查 + 超级管理员可见性修复 |
| 2.5.0 | 2026-07-29 | 分组权限：Group 数据共享 + 超级管理员 X-API-Key + AuthContext + 硬删除 + 分组管理前端 |
| 2.4.1 | 2026-07-22 | 数据上传模块重建：按标准分类入库 + 自动建标准表 + 大文件切片 + 数据类型细分 + 标准管理可维护 |
| 2.4.0 | 2026-07-21 | 质检模块重建：标准驱动属性检查 9 项 + 拓扑检查 13 项 + 五维评分 + API 重构 + 前端升级 |
| 2.3.1 | 2026-07-17 | 版本管理修复（diff 500 / 重复 version_number / _deep_diff 重写） |
| 2.3.0 | 2026-06-18 | 智能质检规则引擎：规则生成器 + 规则引擎 + 五维评分 + 4 张新表 + 规则管理 API |
| 2.2.0 | 2026-06-17 | 数据管理页优化（分页可调/日期过滤/消除 N+1 查询） |
| 2.1.0 | 2026-06-16 | 连接池配置、Redis 缓存鉴权、安全响应头、CORS 可配置、Celery Beat、SRI/CSP、20 项优化 |
| 2.0.0 | 2026-06-15 | 空间分表存储、四层验证、增量同步、Webhook、审计日志、CI/CD |

完整的优化实施追溯见 [VCS_优化实施记录.md](VCS_优化实施记录.md)（S/A/B/C 四级分类）。
全量修订时间线与需求溯源见 [VCS_项目修订全纪录.md](VCS_项目修订全纪录.md)（含非项目内容归档）。
