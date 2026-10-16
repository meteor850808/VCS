# VCS 空间质检 — 项目压缩上下文

> **详细基准线**: 见 `BASELINE.md` — 完整的数据库表结构、API 端点、服务模块清单。  
> **版本**: v2.24.0  
> **最后更新**: 2026-10-13  
> **v2.24.0**: Docker 生产加固 — Nginx 反代(自签 TLS)/Docker secrets/entrypoint 派生连接串/LibreDWG+mdbtools Linux 适配/Flower+Beat 修复/Makefile 发布脚本

### 🎯 核心定位
**矢量数据质检微服务** — 集格式转换、坐标系变换、质量检查、空间数据存储于一体。

### 🛠️ 技术栈
| 层级 | 技术 |
|------|------|
| 框架 | FastAPI + SQLAlchemy 2.0 async |
| 数据库 | PostgreSQL 15 / PostGIS 3.4 |
| 任务队列 | Celery 5 + Redis 7 |
| GIS | Fiona/Shapely/pyproj/geoalchemy2/ogr2ogr |
| 前端 | 单文件 HTML SPA (Vanilla JS + Leaflet) |

### 📁 目录结构
```
空间质检/
├── app/
│   ├── api/v1/          # 26个路由模块 (含 cad/ data_standard_v2/ 子目录, 147端点含1 WebSocket)
│   ├── services/        # 27个顶层服务模块 + quality/子包(5 files) = 32
│   ├── models/          # 28个ORM模型 (Base 子类)
│   ├── schemas/         # Pydantic 请求/响应模型
│   ├── core/            # 12个核心模块(2026-09-01 清理 services.py)
│   ├── frontend/        # SPA前端
│   └── workers/         # Celery任务
├── alembic/             # 数据库迁移
├── scripts/             # 初始化/迁移/修复/冒烟脚本 (18 files: 16 py + ps1/bat 等)
├── tests/               # pytest 测试套件 (75 个 test_*.py, 1617 例)
└── docs/                # 设计文档（revision_history + 主目录 15 个 revision_v*.md + archive/ 30 个归档文件）
```

### 🔑 核心功能模块
| 模块 | 说明 |
|------|------|
| **质检引擎** | 规则自动生成 + 五维评分(完整性/准确性/一致性/唯一性/拓扑) |
| **格式转换** | GDB/MDB/Shapefile/GeoJSON/KML/DXF → 多格式互转 |
| **空间存储** | 要素分表(point/line/polygon) + PostGIS GiST空间索引 |
| **API安全** | SHA-256哈希 + 分组权限隔离 + 密钥生命周期管理 |
| **数据标准 v2** | 层级图层树 + 表多分类 + 字典双维度(全局/标准) + 版本管理 |
| **源数据管理** | 旧版源表/字段/字典管理 (迁移中) |

### 🚀 快速启动
```bash
# 一键重启
restart.bat          # Windows
restart.sh           # Linux

# 手动启动
uvicorn app.main:app --host 127.0.0.1 --port 45679 --reload

# Docker
docker-compose up -d
```

### 📍 访问地址
- 前端: http://localhost:45679/app/
- API测试台: http://localhost:45679/app/test.html
- Swagger: http://localhost:45679/docs
- ReDoc: http://localhost:45679/redoc
- Flower: http://localhost:5555
- 健康检查: http://localhost:45679/health

### 📊 关键数字
> v2.22.3 实测复核（2026-09-29），`app/` 下 `@router.*` 装饰器全量计数

- **146** REST API 端点 + 1 WebSocket（GET 58 / POST 51 / DELETE 23 / PUT 13 / PATCH 1）— **原写 114，已修正**
- **32** 数据库物理表
- **27** 服务模块
- **21** ORM 模型
- **19** 脚本文件（16 个 .py + ps1/bat 等，含 v2.22.3 新增 `verify_csv_archive.py` 与 `mutation_check.py`）
- **84** `tests/test_*.py`，**1778** 测试用例（100% 覆盖，11665 stmts / 0 miss）
