# VCS 空间质检 — 项目压缩上下文

> **详细基准线**：详见 `BASELINE.md`（包含完整的数据库表结构、API 端点、服务模块、目录结构等全量信息）。  
> **版本**: v2.25.0  
> **最后更新**: 2026-11-10  
> **v2.25.0 要点**：五维评分口径 v3——维度分改由要素级派生（`1 - Σfailed/Σtotal_checked`），条目通过率降为诊断字段；T06–T10 与 A7–A9 降为诊断项；未评价维度为 NULL 并从权重归一化中剔除；迁移 0014 加口径元数据三列并放宽五列为可空；回滚开关 `SCORING_PROFILE=v2`。

### 🎯 核心定位
**矢量数据质检微服务（Vector Conversion Service, VCS）** —— 集格式转换、坐标系变换、质量检查、空间数据存储于一体。

### 📌 核心特性
- **质检引擎**：规则自动生成 + 五维评分（完整性/准确性/一致性/唯一性/拓扑）
- **格式转换**：GDB/MDB/Shapefile/GeoJSON/KML/DXF → 多格式互转
- **空间存储**：要素分表（point/line/polygon）+ PostGIS GiST 空间索引
- **API 安全**：SHA-256 哈希 + 分组权限隔离 + 密钥生命周期管理
- **数据标准 v2**：层级图层树 + 表分类 + 字典（全局/标准）+ 版本管理

### 🚀 快速启动
```bash
# 一键重启
restart.bat          # Windows
restart.sh           # Linux

# 手动启动
uvicorn app.main:app --host 127.0.0.1 --port 45679 --reload

# Docker（推荐生产）
docker-compose up -d
```

### 📍 访问地址
- 前端：`http://localhost:45679/app/`
- API 测试台：`http://localhost:45679/app/test.html`
- Swagger：`http://localhost:45679/docs`
- ReDoc：`http://localhost:45679/redoc`
- Flower：`http://localhost:5555`
- 健康检查：`http://localhost:45679/health`

> 详细统计信息（端点数、表数量、测试覆盖等）请参见 `BASELINE.md` §4、§3、§6 等章节。
