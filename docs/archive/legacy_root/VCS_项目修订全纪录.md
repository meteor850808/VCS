# VCS 项目修订全纪录

> 本文档记录空间API服务（矢量转换服务中心）从项目立项至今的**所有修订**，
> 包含需求来源、修改内容、技术方案、影响范围。
> 非项目内容（外部参考、旧版方案、临时记录）统一归档于此。

---

## 目录

1. [项目概述](#1-项目概述)
2. [需求溯源](#2-需求溯源)
3. [修订时间线](#3-修订时间线)
4. [非项目内容归档](#4-非项目内容归档)
5. [版本发布记录](#5-版本发布记录)
6. [附录：文件清单](#6-附录文件清单)

---

## 1. 项目概述

| 项目 | 内容 |
|------|------|
| 项目名称 | 矢量转换服务（Vector Conversion Service, VCS） |
| 项目定位 | 矢量数据格式转换、坐标系变换、质量检查与空间数据存储一体化微服务 |
| 技术栈 | FastAPI + SQLAlchemy 2.0 async + PostgreSQL/PostGIS + Celery + Redis |
| 前端 | Vanilla JS SPA + Leaflet 1.9（单文件 HTML） |
| 部署 | Docker Compose（4 容器）+ 裸机部署（Windows/Linux） |
| 当前版本 | 2.9.0（2026-08-12） |

---

## 2. 需求溯源

### 2.1 核心需求（项目立项 2026-05-07）

源自项目建设方案（`VCS_项目建设书.md`）和项目书（`VCS_项目书.md`），归纳为以下五大模块：

| 需求编号 | 模块 | 需求描述 | 优先级 | 当前状态 |
|----------|------|----------|--------|----------|
| RQ-001 | 格式转换 | 主流矢量格式互转（SHP/GeoJSON/KML/DXF/GPKG/GML） | P0 | ✅ 已完成 |
| RQ-002 | 坐标系 | CRS 坐标变换，支持 EPSG 编码 + 中文别名 | P0 | ✅ 已完成 |
| RQ-003 | 批量转换 | 一次提交最多 50 个文件的批量转换 | P1 | ✅ 已完成 |
| RQ-004 | 质量检查 | 属性完整性、关键字段（YWBH/FEATURECODE）验证 | P1 | ✅ 已完成（v2.4 标准驱动重建） |
| RQ-005 | 实时进度 | WebSocket 推送任务进度 | P1 | ✅ 已完成 |
| RQ-006 | API 鉴权 | API Key 认证 + Admin 密钥管理 | P0 | ✅ 已完成 |
| RQ-007 | 前端 SPA | 浏览器端可视化操作界面 | P1 | ✅ 已完成 |
| RQ-008 | 数据存储 | 文件上传到 PostgreSQL BYTEA，支持压缩包解压 | P1 | ✅ 已完成 |
| RQ-009 | 空间数据 | 空间要素入库、分表存储（point/line/polygon） | P2 | ✅ 已完成 |
| RQ-010 | 数据标准 | Excel 导入字段约束定义、数据标准管理 | P2 | ✅ 已完成 |
| RQ-011 | Docker 化 | Docker Compose 全栈编排 | P1 | ✅ 已完成 |
| RQ-012 | 自动部署 | 一键重启脚本（Windows/Git Bash） | P2 | ✅ 已完成 |
| RQ-013 | 拓扑检查 | 多边形重叠/间隙/自相交拓扑验证 | P2 | ✅ 已完成（v2.4 扩展至 13 项） |
| RQ-014 | 违规导出 | 质检违规要素导出 GeoJSON/Excel | P2 | ✅ 已完成 |
| RQ-015 | 增量同步 | 变更集 UPSERT 增量同步引擎 | P3 | ✅ 已完成 |
| RQ-016 | 版本控制 | 数据快照/差异比对/回滚 | P3 | ✅ 已完成 |
| RQ-017 | Webhook | 任务完成/失败事件推送（HMAC 签名） | P3 | ✅ 已完成 |
| RQ-018 | 审计日志 | 全操作可追溯 | P3 | ⏸ 代码完成，未接入启动 |
| RQ-019 | CI/CD | GitHub Actions 流水线 | P2 | ✅ 已完成 |

### 2.2 优化需求（演进阶段）

| 需求编号 | 来源 | 需求描述 | 阶段 | 当前状态 |
|----------|------|----------|------|----------|
| RQ-020 | 用户反馈 2026-05-22 | 前端页面白屏修复 | Phase 3 | ✅ 已修复 |
| RQ-021 | 用户反馈 2026-05-27 | 一键重启脚本 | Phase 3 | ✅ 已完成 |
| RQ-022 | 用户反馈 2026-05-27 | TEST 页面所有测试项无法调用（端口/body stream） | Phase 3 | ✅ 已修复 |
| RQ-023 | 用户反馈 2026-06-04 | 删除 API Key 功能不正常（test.html 列表不显示） | Phase 3 | ✅ 已修复 |
| RQ-024 | 用户反馈 2026-06-05 | 采纳空间API服务1的优化 | Phase 4-6 | ✅ 已完成 |
| RQ-025 | 用户反馈 2026-06-11 | 管理界面删除 API Key 列表不显示 | Phase 4-6 | ✅ 已修复 |
| RQ-026 | 用户反馈 2026-06-16 | 清理冗余文件 | Phase 7 | ✅ 已完成 |
| RQ-027 | 用户反馈 2026-06-16 | 前端设置 APIKEY 点击无反应（Leaflet 阻塞 + SRI 错误） | Phase 7 | ✅ 已修复 |
| RQ-028 | 用户反馈 2026-06-16 | 服务无法启动（slowapi response 参数） | Phase 7 | ✅ 已修复 |
| RQ-029 | 用户反馈 2026-06-16 | 数据上传 500 错误 | Phase 7 | ✅ 已修复 |
| RQ-030 | 用户反馈 2026-06-17 | 数据管理页面慢（N+1 查询）、分页不可调 | Phase 8 | ✅ 已修复 |
| RQ-031 | 用户反馈 2026-06-17 | 请求 `S.t is not a function` 错误 | Phase 8 | ✅ 已修复 |
| RQ-032 | 用户反馈 2026-06-18 | 创建项目修订全纪录文档 | Phase 9 | ✅ 已完成 |
| RQ-033 | 架构决策 2026-07-17 | 版本管理 bug 修复（diff 500 / 重复 version_number） | Phase 10 | ✅ 已完成 |
| RQ-034 | 架构决策 2026-07-21 | 质检模块重建：标准驱动属性检查 9 项 + 拓扑检查 13 项 + 五维评分 | Phase 11 | ✅ 已完成 |
| RQ-035 | 架构决策 2026-07-22 | 数据上传模块重建：按标准分类入库 + 自动建标准表 + 大文件切片 + 数据类型细分 | Phase 12 | ✅ 已完成 |
| RQ-036 | 用户反馈 2026-07-31 | 质检页紧凑重构（紧凑单页工作台 + 无标准纯拓扑检查 + 超级管理员可见性修复） | Phase 14 | 已完成 |
| RQ-037 | 用户反馈 2026-08-03 | ERROR.txt 使用问题批量修复（8 模块 30+ 项：密钥启用/重生成、字典查重、Excel 4-sheet 导入、空要素质检、健康检查超时等） | Phase 15 | 已完成 |
| RQ-038 | 架构决策 2026-08-04 | v2.8 规划：数据链路稳定性（S1 入库稳定性 / S2 CRS 元数据 / S3 Multipatch 降维）→ 质检联动 → 标准闭环 → 运营部署 | Phase 16+ | 进行中 |
| RQ-039 | 用户反馈 2026-08-12 | 数据字典展示拆分：全局字典 / 对应数据标准字典两个独立维度，不与表/字段做关联 | Phase 17 | ✅ 已完成（v2.9） |
| RQ-040 | 质检异常 2026-08-19 | 质检拓扑检查返回 0 条结果：要素表 `api_key_id=NULL` vs 存储文件虚拟 ID 导致查询过滤为空 | Phase 18 | ✅ 已完成（v2.10.1） |

---

## 3. 修订时间线

### Phase 1 — 项目初始搭建（2026-05-07 至 2026-05-09）

**需求来源：** RQ-001 ~ RQ-011（项目建设方案）

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 05-07 | 项目初始化，FastAPI 入口 + 路由结构 | `app/main.py`, `app/api/v1/router.py` | RQ-001 |
| 05-07 | 格式转换核心 API（submit/jobs/detect/capabilities） | `app/api/v1/convert.py` | RQ-001 |
| 05-07 | API Key 鉴权系统（SHA-256 哈希 + Admin 密钥） | `app/core/security.py`, `app/core/deps.py` | RQ-006 |
| 05-07 | 数据库模型（api_key / conversion_job / batch_job） | `app/models/` | RQ-006 |
| 05-07 | Docker Compose 全栈编排 | `docker-compose.yml`, `Dockerfile` | RQ-011 |
| 05-07 | Pydantic 请求/响应模型 | `app/schemas/conversion.py` | RQ-001 |
| 05-08 | Celery Worker 异步任务框架 | `app/workers/celery_app.py`, `app/workers/tasks.py` | RQ-001 |
| 05-08 | 格式检测服务（Fiona + chardet） | `app/services/format_detection.py` | RQ-001 |
| 05-08 | CRS 坐标系解析（EPSG + 中文别名） | `app/services/crs_transform.py` | RQ-002 |
| 05-08 | 属性处理服务（编码转码/字段过滤） | `app/services/attribute_processing.py` | RQ-001 |
| 05-08 | 几何修复服务（buffer(0)/make_valid） | `app/services/geometry_repair.py` | RQ-001 |
| 05-08 | ogr2ogr/Fiona 脚本生成 + 沙箱执行 | `app/services/script_generation.py` | RQ-001 |
| 05-08 | 批量转换 API + 任务协调 | `app/api/v1/batch.py`, `app/workers/batch_tasks.py` | RQ-003 |
| 05-08 | 质量检查服务 | `app/services/quality_checker.py` | RQ-004 |
| 05-08 | 质量检查 API | `app/api/v1/quality.py` | RQ-004 |
| 05-08 | WebSocket 实时进度推送 | `app/api/v1/ws.py`, `app/services/progress_tracker.py` | RQ-005 |
| 05-08 | 配置管理（环境变量） | `app/config.py` | — |
| 05-08 | 数据库初始化脚本 | `scripts/init_db.py` | — |
| 05-09 | 前端 SPA 基础版（5 页） | `app/frontend/index.html` | RQ-007 |
| 05-09 | API 全功能测试台 | `app/frontend/test.html` | RQ-007 |
| 05-09 | pytest 测试套件（38 用例） | `tests/test_api.py` | — |

---

### Phase 2 — 功能扩展（2026-05-09 至 2026-05-22）

**需求来源：** RQ-008 ~ RQ-010, RQ-013 ~ RQ-019

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 05-20 | 数据存储模型 + 上传 API | `app/models/stored_file.py`, `app/api/v1/data.py` | RQ-008 |
| 05-20 | 压缩包自动解压 | `app/services/archive_extractor.py` | RQ-008 |
| 05-20 | GDB 自动转 GeoJSON | `app/services/gdb_processor.py` | RQ-008 |
| 05-20 | 空间要素分表存储（point/line/polygon） | `app/models/spatial_feature.py` | RQ-009 |
| 05-20 | 跨三表 FeatureStore | `app/services/feature_store.py` | RQ-009 |
| 05-20 | 空间要素加载器 | `app/services/spatial_loader.py` | RQ-009 |
| 05-20 | 数据标准模型（Standard/Sheet/Field 三表） | `app/models/data_standard.py` | RQ-010 |
| 05-20 | 数据标准 Excel 解析 | `app/services/data_standard_parser.py` | RQ-010 |
| 05-20 | 数据标准管理 API | `app/api/v1/data_standards.py` | RQ-010 |
| 05-20 | 空间数据处理 API（坐标转换/几何修复） | `app/api/v1/spatial.py` | RQ-009 |
| 05-20 | 拓扑检查服务 | `app/services/topology_checker.py` | RQ-013 |
| 05-20 | 质量违规要素导出（GeoJSON/Excel） | `app/services/quality_exporter.py` | RQ-014 |
| 05-20 | 增量同步引擎 | `app/services/incremental_sync.py` | RQ-015 |
| 05-20 | 数据版本控制 | `app/services/version_control.py` | RQ-016 |
| 05-20 | Webhook 通知（HMAC 签名 + 重试） | `app/services/webhook.py` | RQ-017 |
| 05-20 | 审计日志系统 | `app/services/audit_logger.py` | RQ-018 |
| 05-20 | 数据去重服务 | `app/services/data_dedup.py` | — |
| 05-20 | 四层验证管道（L1 格式→L2 几何→L3 属性→L4 拓扑） | `app/services/validation_pipeline.py` | — |
| 05-20 | 空间查询（BBOX/KNN） | `app/services/spatial_query.py` | — |
| 05-20 | 存储生命周期管理（Hot/Warm/Cold） | `app/services/storage_tier.py` | — |
| 05-20 | 服务层 Protocol 接口 | `app/core/services.py` | — |
| 05-20 | 各类常量抽取 + 安全检查 | `app/core/constants.py` | — |
| 05-20 | 文件名验证（路径遍历/危险扩展名） | `app/core/file_utils.py` | — |
| 05-20 | Celery Beat 定时任务（清理/指标快照） | `app/workers/beat_schedule.py` | — |

---

### Phase 3 — 前端重构与 BUG 修复（2026-05-22 至 2026-06-11）

**需求来源：** RQ-020 ~ RQ-023, RQ-007

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 05-22 | 前端 5 页→6 页重构 + 右侧抽屉详情面板 | `app/frontend/index.html` | RQ-007 |
| 05-22 | Leaflet 死代码清理（334 行） | `app/frontend/index.html` | RQ-007 |
| 05-22 | 批量上传旧系统清理（100 行） | `app/frontend/index.html` | RQ-007 |
| 05-22 | CSS 变量暗色主题统一 | `app/frontend/index.html` | RQ-007 |
| 05-22 | 前端页面白屏修复（CSS @import → `<link>`） | `app/frontend/index.html` | RQ-020 |
| 05-22 | 前端页面白屏修复（默认 page 未激活） | `app/frontend/index.html` | RQ-020 |
| 05-22 | 端口 8000 → 45678 迁移 | `restart.*` | RQ-021 |
| 05-27 | 一键重启脚本（Git Bash + Windows） | `scripts/restart.sh`, `scripts/restart.bat` | RQ-021 |
| 05-27 | TEST 页面 apiCall body stream 已读错误修复 | `app/frontend/test.html` | RQ-022 |
| 05-27 | SPA 新增 admin 页面 + API Key 管理 | `app/frontend/index.html` | RQ-022 |
| 06-04 | DELETE URL 反斜杠→正斜杠修复 | `app/frontend/test.html` | RQ-023 |
| 06-11 | test.html 恢复 refreshAdminKeyList() 函数 | `app/frontend/test.html` | RQ-023 |

---

### Phase 4-6 — 外部 AI 优化采纳（2026-06-11 至 2026-06-15）

**需求来源：** RQ-024

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 06-11 | Schemas 拆分（conversion.py → 9 个领域文件） | `app/schemas/` | RQ-024 |
| 06-11 | 质检子包（models.py + reporter.py） | `app/services/quality/` | RQ-024 |
| 06-11 | FeatureStore 统一跨三表 CRUD | `app/services/feature_store.py` | RQ-024 |
| 06-11 | GDB Processor | `app/services/gdb_processor.py` | RQ-024 |
| 06-11 | 文件名验证 + 数据标准加载 | `app/core/file_utils.py`, `app/core/db_utils.py` | RQ-024 |
| 06-11 | Pydantic v2（@dataclass → pydantic-settings） | `app/config.py` | RQ-024 |
| 06-11 | 常量抽取（OUTPUT_SUFFIX_MAP / DANGEROUS_EXTENSIONS） | `app/core/constants.py` | RQ-024 |
| 06-11 | API Key 软删除（is_active=False） | `app/api/v1/admin.py` | RQ-024 |
| 06-13 | 分块流式上传 + 限流装饰器 | `app/api/v1/data.py`, `app/api/v1/convert.py` | RQ-024 |
| 06-13 | Redis 缓存装饰器 | `app/core/cache.py` | RQ-024 |
| 06-13 | 速率限制（slowapi） | `app/core/rate_limit.py` | RQ-024 |
| 06-13 | 请求追踪中间件（trace_id） | `app/core/trace_middleware.py` | RQ-024 |
| 06-13 | 结构化日志（structlog） | `app/core/logging_setup.py` | RQ-024 |
| 06-15 | Alembic 数据库迁移框架 | `alembic/`, `alembic.ini` | RQ-024 |
| 06-15 | CI/CD 流水线 | `.github/workflows/ci.yml` | RQ-019 |
| 06-15 | Docker 构建优化（.dockerignore） | `.dockerignore` | RQ-024 |

---

### Phase 7 — BUG 修复与架构加固（2026-06-16）

**需求来源：** RQ-025 ~ RQ-029

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 06-16 | `requirements.txt` 缺少 `pydantic-settings` + `geoalchemy2` | `requirements.txt` | RQ-025 |
| 06-16 | `audit_logger.py` 未定义变量 `SQLALCHEMY_MODELS_BASE` | `app/services/audit_logger.py` | RQ-025 |
| 06-16 | `spatial_query.py` 引用不存在的 `spatial_features` 表 | `app/services/spatial_query.py` | RQ-025 |
| 06-16 | Alembic env.py 缺少模型导入 | `alembic/env.py` | RQ-025 |
| 06-16 | Alembic 0002 迁移引用无效表/列 | `alembic/versions/0002_performance_indexes.py` | RQ-025 |
| 06-16 | `scripts/init_db.py` 导入不存在的 `SpatialFeature` | `scripts/init_db.py` | RQ-025 |
| 06-16 | 字段约束去重集合混用 | `app/services/quality_checker.py` | RQ-025 |
| 06-16 | AuditLog 独立 DeclarativeBase → 继承 Base | `app/services/audit_logger.py` | RQ-025 |
| 06-16 | **S1:** 异步引擎连接池配置 | `app/db/session.py`, `app/config.py` | RQ-025 |
| 06-16 | **S2:** Celery 结果过期 + 崩溃重试 | `app/workers/celery_app.py` | RQ-025 |
| 06-16 | **S3:** Celery Beat 启动（docker-compose） | `docker-compose.yml`, `celery_app.py` | RQ-025 |
| 06-16 | **S4:** CORS 可配置 origins | `app/main.py`, `app/config.py` | RQ-025 |
| 06-16 | **S5:** 全局请求体大小限制 | `app/core/security_middleware.py` | RQ-025 |
| 06-16 | **A1:** 消除 N+1 查询（selectinload） | `app/core/db_utils.py`, `app/models/data_standard.py` | RQ-025 |
| 06-16 | **A3:** 健康检查缓存 10s | `app/main.py` | RQ-025 |
| 06-16 | **A4:** API Key Redis 缓存 | `app/core/deps.py` | RQ-025 |
| 06-16 | **B1:** Dockerfile non-root 用户 | `Dockerfile` | RQ-025 |
| 06-16 | **B2:** Flower 认证 | `docker-compose.yml`, `Dockerfile` | RQ-025 |
| 06-16 | **B3:** 安全响应头中间件 | `app/core/security_middleware.py` | RQ-025 |
| 06-16 | **B4:** 代理 IP 识别（X-Forwarded-For） | `app/core/rate_limit.py` | RQ-025 |
| 06-16 | **C1:** 字段名统一 `MAX_FEATURES_PER_UPLOAD` | `app/config.py`, `app/api/v1/data.py` | RQ-025 |
| 06-16 | **C2:** 内联 import 移到顶部 | `app/core/deps.py`, `app/main.py` | RQ-025 |
| 06-16 | **C3:** 删除死代码 `_get_db_sync()` | `app/db/session.py` | RQ-025 |
| 06-16 | **C4:** 测试改用 CELERY_TASK_ALWAYS_EAGER | `tests/test_api.py` | RQ-025 |
| 06-16 | **C5/C6:** 前端 CDN SRI + CSP | `app/frontend/index.html` | RQ-025 |
| 06-16 | 环境变量模板更新（`DB_POOL_*` 等） | `.env.example` | RQ-025 |
| 06-16 | 前端 API Key 按钮无反应（Leaflet 阻塞 + addEventListener） | `app/frontend/index.html` | RQ-027 |
| 06-16 | 前端 API Key 双重保存（input 自动 + 按钮点击） | `app/frontend/index.html` | RQ-027 |
| 06-16 | SRI hash 不匹配导致 Leaflet CSS 被拦截 | `app/frontend/index.html` | RQ-027 |
| 06-16 | CSP 移除（SRI 兼容性问题） | `app/frontend/index.html` | RQ-027 |
| 06-16 | Leaflet defer 加载避免阻塞 HTML 解析 | `app/frontend/index.html` | RQ-027 |
| 06-16 | **服务启动修复：** slowapi `@limiter.limit` 缺少 `response: Response` 参数 | `app/api/v1/convert.py`, `app/api/v1/data.py` | RQ-028 |
| 06-16 | **上传修复：** slowapi `_inject_headers` 检查 Response 实例 | `app/api/v1/convert.py`, `app/api/v1/data.py` | RQ-029 |
| 06-16 | 前端 `S.t` → `toast` 修复（管理页面 3 处） | `app/frontend/index.html` | RQ-031 |
| 06-16 | 前端 GET 检测 401 自动停定时器 | `app/frontend/index.html` | RQ-030 |
| 06-16 | 清理冗余文件（26 个旧文档/脚本） | 项目根目录 | RQ-026 |
| 06-16 | 新建 `VCS_优化实施记录.md` | 项目根目录 | RQ-026 |
| 06-16 | 新建 `overview.md` 项目总览 | 项目根目录 | RQ-026 |

---

### Phase 8 — 数据管理页优化（2026-06-17）

**需求来源：** RQ-030

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 06-17 | **后端：** 分页接口返回 `{items, total, page, page_size}` | `app/api/v1/data.py`, `app/schemas/storage.py` | RQ-030 |
| 06-17 | **后端：** 批量 UNION ALL 查询要素数（消除 N+1） | `app/api/v1/data.py` | RQ-030 |
| 06-17 | **后端：** 日期范围过滤（from/to 参数） | `app/api/v1/data.py` | RQ-030 |
| 06-17 | **前端：** 可调分页大小（10/20/50/100） | `app/frontend/index.html` | RQ-030 |
| 06-17 | **前端：** 日期筛选控件 + 清除按钮 | `app/frontend/index.html` | RQ-030 |
| 06-17 | **前端：** 移除 N+1 要素数请求（直接用 `f.feature_count`） | `app/frontend/index.html` | RQ-030 |
| 06-17 | **前端：** 真实分页信息（第 X 页 / 共 Y 页 · 共 Z 条） | `app/frontend/index.html` | RQ-030 |

---

### Phase 9 — 修订记录标准化（2026-06-18）

**需求来源：** RQ-032

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 06-18 | 创建 `VCS_项目修订全纪录.md`（本文档） | 项目根目录 | RQ-032 |
| 06-18 | 所有非项目内容整理归档至此文档 | `CLAUDE-FABLE-5.md` 等 | RQ-032 |
| 06-18 | **Bug:** 前端拓扑配置格式不匹配修复 | `app/frontend/index.html` | RQ-031 |
| 06-18 | **Bug:** CRITICAL_FIELDS 常量未使用修复 | `app/services/quality_checker.py` | RQ-031 |
| 06-18 | **Phase A:** 质检规则 ORM 模型（4 张新表） | `app/models/quality_rule*.py` + 迁移 | RQ-031 |
| 06-18 | **Phase A:** 规则生成器（从标准字段自动生成规则） | `app/services/quality/rule_generator.py` | RQ-031 |
| 06-18 | **Phase A:** 规则引擎（NOT_NULL/VALUE_IN_SET/TYPE_MATCH/LENGTH_LIMIT） | `app/services/quality/rule_engine.py` | RQ-031 |
| 06-18 | **Phase A:** 五维评分模型（完整性/准确性/一致性/唯一性/及时性） | `app/services/quality/scoring.py` | RQ-031 |
| 06-18 | **Phase A:** 规则管理 API（CRUD + 自动生成） | `app/api/v1/quality_rules.py` | RQ-031 |
| 06-18 | 文档更新（README / overview / 修订全纪录） | 项目文档 | RQ-032 |

---

### Phase 10 — 版本管理修复（2026-07-17）

**需求来源：** Bug 修复

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 07-17 | **Bug:** `_deep_diff` 不支持 list 类型对比导致 diff 端点 500 | `app/api/v1/data_standard_v2.py` | Bug Fix |
| 07-17 | **Bug:** 重复 version_number 导致 diff/rollback 查询歧义 | `app/api/v1/data_standard_v2.py` | Bug Fix |
| 07-17 | **Bug:** `_deep_diff` 重写支持 dict/list/None/标量递归对比 | `app/api/v1/data_standard_v2.py` | Bug Fix |
| 07-17 | **Bug:** diff/rollback 查询加 `.order_by(created_at.desc()).limit(1)` | `app/api/v1/data_standard_v2.py` | Bug Fix |
| 07-17 | E2E 版本管理生命周期测试 11/11 通过 | — | 验证 |

---

### Phase 11 — 质检模块重建（2026-07-21）

**需求来源：** 质检模块全面重建，标准驱动 + 五维评分

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 07-21 | **Phase 1:** StoredFile 新增 standard_id + standard_table_id | `app/models/stored_file.py` | 质检重建 |
| 07-21 | **Phase 1:** QualityReport 新增五维评分 + summary 字段 | `app/models/quality_report.py` | 质检重建 |
| 07-21 | **Phase 2-3:** 属性检查引擎（9 项规则 A1-A9，pandas 向量化） | `app/services/quality/attribute_checker.py` | 质检重建 |
| 07-21 | **Phase 4:** 拓扑检查引擎重写（13 项 T01-T13，混合 SQL+shapely） | `app/services/topology_checker.py` | 质检重建 |
| 07-21 | **Phase 5:** 质检编排器重写（串联属性+拓扑+五维评分） | `app/services/quality_checker.py` | 质检重建 |
| 07-21 | **Phase 6a:** 新增 v2 请求/响应 Schema | `app/schemas/quality.py` | 质检重建 |
| 07-21 | **Phase 6b:** 重写质检 API（v2 + 兼容旧接口） | `app/api/v1/quality.py` | 质检重建 |
| 07-21 | **Phase 6c:** 重写 `/data/{id}/quality-check` 端点 | `app/api/v1/data.py` | 质检重建 |
| 07-21 | 新增 `GET /data-standards-v2/{id}/tables` 端点 | `app/api/v1/data_standard_v2.py` | 质检重建 |
| 07-21 | **Phase 7:** 前端质检配置面板（标准→表级联/13 项规则/容差面板） | `app/frontend/index.html` | 质检重建 |
| 07-21 | **Phase 7:** 前端质检结果展示（五维评分卡片+检查汇总表） | `app/frontend/index.html` | 质检重建 |
| 07-21 | 修复 CheckResult 缺少 @dataclass 装饰器 | `app/services/quality/attribute_checker.py` | Bug Fix |
| 07-21 | 修复 field 属性名遮蔽 dataclasses.field | `app/services/quality/attribute_checker.py` | Bug Fix |
| 07-21 | 安装 pandas 到两个 venv（空间质检 + 空间API服务） | 环境 | 依赖 |
| 07-21 | 文档更新（修订记录 / 技术方案 / 测试手册） | `docs/` | 文档 |

---

### Phase 12 — 数据上传模块重建 + 数据类型细分（2026-07-22）

**需求来源：** 上传模块全面重建，支持按标准分类入库 + 大文件切片 + 几何子类型

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 07-22 | **Phase 1:** StoredFile 新增 11 个字段（data_type/geometry_type/file_format/detected_crs/layer_count/feature_count/field_schema/upload_id/chunk_dir/standard_id/standard_table_id/data_standard_name） | `app/models/stored_file.py` | 上传重建 |
| 07-22 | **Phase 1:** 新增 5 个索引（api_created/data_type/geometry_type/file_format/upload_id） | `app/models/stored_file.py` | 上传重建 |
| 07-22 | **Phase 2:** StoredFileBrief / StoredFileUploadResponse 新增 geometry_type + data_standard_name | `app/schemas/storage.py` | 上传重建 |
| 07-22 | **Phase 3:** 格式检测 BOM 修复（utf-8-sig 编码 + strip BOM） | `app/services/format_detection.py` | Bug Fix |
| 07-22 | **Phase 3:** 新增 detect_geometry_type() + normalize_geometry_type()（GeoJSON/SHP/KML/GPKG/OBJ/CSV） | `app/services/format_detection.py` | 上传重建 |
| 07-22 | **Phase 3:** FormatResult 新增 geometry_type + data_type 字段 | `app/schemas/detection.py` | 上传重建 |
| 07-22 | **Phase 4:** upload 端点扩展（standard_id/standard_name/layer_group_id/layer_group_name/geometry_type/auto_create_standard） | `app/api/v1/data.py` | 上传重建 |
| 07-22 | **Phase 4:** 标准分类逻辑（三种模式：不关联/关联已有/新增标准） | `app/api/v1/data.py` | 上传重建 |
| 07-22 | **Phase 4:** list_files 支持 geometry_type 过滤（复合格式 geometry:Point） | `app/api/v1/data.py` | 数据类型细分 |
| 07-22 | **Phase 4:** data/stats 新增 by_geometry_type 分布统计 | `app/api/v1/data.py` | 数据类型细分 |
| 07-22 | **Phase 5:** 上传配置面板重写（三模式 Radio + 标准→图层层联 + 字段预览） | `app/frontend/index.html` | 上传重建 |
| 07-22 | **Phase 5:** 数据管理页：6 类统计卡片 + 几何子类型筛选 + 文件列表类型标签 | `app/frontend/index.html` | 数据类型细分 |
| 07-22 | **Phase 5:** 标准管理页：列表项 ✏️ 编辑 / 🗑️ 删除按钮 + v2EditStd 编辑弹窗 + v2SaveStd 保存 | `app/frontend/index.html` | 标准管理 |
| 07-22 | **Phase 5:** 标准管理页路由修复（loadStds → v2LoadStandards） | `app/frontend/index.html` | Bug Fix |
| 07-22 | **迁移:** 0003b_fix_standard_id.py — 补 stored_files standard_id/standard_table_id 列 | `alembic/versions/` | 数据库 |
| 07-22 | **迁移:** 0003c_fix_quality_reports_cols.py — 补 quality_reports 10 个缺失列 | `alembic/versions/` | 数据库 |
| 07-22 | **迁移:** 0003d_add_geometry_type.py — 添加 geometry_type 列 + 索引 + 回填 | `alembic/versions/` | 数据库 |
| 07-22 | **诊断:** 端口 45678 运行错误项目（空间API服务）导致 401/500 → 杀掉 PID 42892 | 环境 | Bug Fix |
| 07-22 | **诊断:** 浏览器 localStorage API Key 吊销 → 重新激活 | 环境 | Bug Fix |
| 07-22 | **回填:** 39 个现有文件 geometry_type 回填（Point:10, Polygon:2, table:1） | DB 数据 | 数据类型细分 |
| 07-22 | 文档更新（修订记录 / 技术方案 / 测试手册） | `docs/` | 文档 |

---

### Phase 13 — 分组权限 + 超级管理员 + 硬删除（2026-07-29 ~ 07-30）

**需求来源：** 团队级数据协作 + 管理员权限增强 + 管理功能完善

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 07-29 | **模型:** 新增 KeyGroup 模型（group_id FK + group relationship） | `app/models/key_group.py`, `app/models/api_key.py` | 分组权限 |
| 07-29 | **迁移:** 0004_add_key_group — key_groups 表 + api_keys.group_id | `alembic/versions/0004_add_key_group.py` | 数据库 |
| 07-29 | **认证:** AuthContext 类（accessible_key_ids/scope_clause/owns/__getattr__/create_super_admin） | `app/core/auth_context.py` | 分组权限 |
| 07-29 | **认证:** authenticated() 返回 AuthContext + admin init key 超级管理员兜底 | `app/core/deps.py` | 超级管理员 |
| 07-29 | **查询迁移:** 68 处 scope_clause/owns 替换（data/quality/spatial/convert/batch/standards 等 9 文件） | `app/api/v1/*.py` | 分组权限 |
| 07-29 | **Schema:** 新增 KeyGroup/SetKeyGroup 系列 Schema + ApiKey 扩展字段 | `app/schemas/admin.py` | 分组权限 |
| 07-29 | **Admin API:** Group CRUD（5 端点）+ Key-Group 指派 + 硬删除端点 | `app/api/v1/admin.py` | 分组+硬删除 |
| 07-29 | **前端:** 双 Tab 管理（Keys/Groups）+ 分组下拉 + 成员卡片 + 编辑/删除分组 | `app/frontend/index.html` | 分组权限 |
| 07-29 | **前端:** 已吊销 Key 显示❌删除按钮（两次确认防误操作） | `app/frontend/index.html` | 硬删除 |
| 07-29 | **前端 Bug:** `esc` 重复声明 + `fmt` 冲突修复（admin 页 JS 零错误） | `app/frontend/index.html` | Bug Fix |
| 07-29 | **Bug:** `geojson_to_wkt()` Z 维度剥离（`_force_2d()` + shapely.transform） | `app/services/spatial_loader.py` | Bug Fix |
| 07-29 | **脚本:** `migrate_auth_patterns.py` + `fix_auth_varname.py` 查询模式迁移 | `scripts/` | 分组权限 |
| 07-29 | **配置:** restart.bat 修复（编码/端口/kill）+ .env ADMIN_INIT_KEY 更新 | `restart.bat`, `.env` | 运维 |
| 07-30 | 文档更新（修订记录） | `docs/revision_v2_5.md` | 文档 |

### Phase 14 ：质检页紧凑重构 + 后端适配（2026-07-31）

**需求来源：** RQ-036（质检页紧凑重构 + 无标准纯拓扑检查 + 超级管理员可见性修复）

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 07-31 | **前端:** `#page-quality` 紧凑重构（文件栏 + 标准栏 + 可折叠检查配置 + 报告表） | `app/frontend/index.html` | RQ-036 |
| 07-31 | **前端:** 检查项胶囊式横向排列（flex-wrap）+ 两档字号体系（12px/11px） | `app/frontend/index.html` | RQ-036 |
| 07-31 | **前端:** QC JS 模块重写（loadStoredSel / loadStdsSel / qcRenderCfg / doQcStored / qcSaveCfg 等） | `app/frontend/index.html` | RQ-036 |
| 07-31 | **后端:** `standard_id` 可空，无标准时仅执行拓扑检查（attr_summary 返回零值） | `app/api/v1/quality.py`, `app/services/quality_checker.py` | RQ-036 |
| 07-31 | **认证修复:** AuthContext 新增 `is_super_admin` 标记，scope_clause/owns 放行 | `app/core/auth_context.py` | Bug Fix |
| 07-31 | **环境修复:** `data_standards.sheet_count` 补默认值（SET DEFAULT 0） | 开发库 DDL | 环境 |
| 07-31 | E2E 验证（Playwright）：页面功能 10 项 + 布局计算样式 2 项 + 后端 curl 3 项全部通过 | 验证 | 验证 |
| 07-31 | 数据清理：删除 E2E 标准 1 条 + 报告 4 条，库状态 46 文件 / 14 标准 / 72 报告 | DB 数据 | 清理 |
| 07-31 | 文档更新（修订记录 v2.6 / 测试手册 v2.6 / README） | `docs/revision_v2_6.md`, `docs/test_manual_v2_6.md`, `README.md` | 文档 |

---

### Phase 15 ：使用问题批量修复 + 一键重启修复（2026-08-04）

**需求来源：** RQ-037（ERROR.txt 使用问题清单 8 模块 30+ 项）

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 08-04 | **管理 API:** 新增 `PUT /admin/apikeys/{id}/activate`（重新启用）+ `POST /admin/apikeys/{id}/regenerate`（重算哈希，旧钥立即失效，新明文仅展示一次） | `app/api/v1/admin.py` | RQ-037 |
| 08-04 | **前端:** 密钥表格按钮（已吊销 ▶️启用/🔁重生成；激活中 🗑️吊销/🔁重生成）+ 事件委托补 `.key-activate-btn`/`.key-regen-btn` | `app/frontend/index.html` | RQ-037 |
| 08-04 | **前端通用:** `esc()` 单引号转义（onclick 属性断裂通用根因）、fetch 30s 超时 + RFC 5987 下载、admKey localStorage 持久化、qcTopoState 默认值初始化、全局拖拽拦截、压缩包空壳过滤、字典栏目增删改、层组父级下拉、showGroupModal 竞态 | `app/frontend/index.html` | RQ-037 |
| 08-04 | **质检:** 空要素入口 400「该文件无图形要素」+ Phase 0.5 守卫返回 0 分 + `_calculate_score_legacy` total==0 改 0.0 + 报告 total_features 用 DB 值 | `app/api/v1/quality.py`, `app/services/quality_checker.py` | RQ-037 |
| 08-04 | **标准 v2:** 字典创建预查重 + IntegrityError→409、scope 枚举校验→400、Excel 4-sheet 模板导入（v2 解析器 + 旧结构回退 + .xls 提示） | `app/api/v1/data_standard_v2.py`, `app/services/data_standard_parser.py` | RQ-037 |
| 08-04 | **健康检查:** /health PG/Redis ping 各 2s 超时（degraded 而非挂起） | `app/main.py` | RQ-037 |
| 08-04 | **WebSocket:** Redis 不可用时连接存活检测（`WebSocketState.CONNECTED` 轮询替代死循环 sleep） | `app/api/v1/ws.py` | RQ-037 |
| 08-04 | **下载:** 响应头 RFC 5987 `filename*=UTF-8''`（中文文件名下载失败根因） | `app/api/v1/data.py` | RQ-037 |
| 08-04 | **自检页:** apiCall 15s 超时 + runAllTests 串行 await | `app/frontend/test.html` | RQ-037 |
| 08-04 | **运维:** restart.bat 卡住修复（Start-Process 隐藏窗口 + 成功自动关窗 + curl -m 5 超时 + 日志分储）双版本同步 | `restart.bat`, `scripts/restart.bat` | Bug Fix |
| 08-04 | E2E 验证（HTTP 实测）：管理密钥生命周期 / 字典 409·400 / 空要素 400 / Excel 4-sheet 导入全部通过 + py_compile·node 语法校验 | 验证 | 验证 |
| 08-04 | 数据清理：测试 Key 5 个 hard 删除、测试标准 2 个删除、e2e_std.xlsx 移临时目录 | DB 数据 | 清理 |
| 08-04 | 文档更新（修订记录 v2.7 / 测试手册 v2.7 / README / overview / 修订全纪录 / 优化实施记录） | `docs/revision_v2_7.md`, `docs/test_manual_v2_7.md`, `README.md`, `overview.md` 等 | 文档 |

---

### Phase 16 ：v2.8 数据链路稳定性 — S1 PostGIS 入库稳定性（2026-08-04）

**需求来源：** RQ-038（v2.8 规划 `docs/plan_v2_8.md` 阶段 A · S1；ERROR.txt 四-2 PostGIS 入库不稳定）

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 08-04 | **几何转换:** `geojson_to_wkt_status` 新增（make_valid 自动修复自相交 + 3D 降维 + 解析失败返回 `ok=False` 不再静默 EMPTY）；`geojson_to_wkt` 保留旧签名委托新函数 | `app/services/spatial_loader.py` | RQ-038/S1 |
| 08-04 | **要素写入:** `write_feature` 返回三态（written/skipped_empty/skipped_no_geometry）；新增 `write_features_batch` 批量写入 + 每 500 条 flush，无几何落 NULL 属性保留 | `app/services/feature_store.py` | RQ-038/S1 |
| 08-04 | **子任务独立会话:** 三个 `_load_*_async` 移除共享 `db`，改 `AsyncSessionLocal()` 独立提交，单子文件失败不影响整单；统一返回统计 dict + `_collect_sub_task_results` 收拢 | `app/api/v1/data.py` | RQ-038/S1 |
| 08-04 | **主端点聚合回填:** upload/merge/reindex 三端点 gather 后 flush+expire_all 刷新主会话；extraction_details 回填真实写入数；附带修复 merge 非压缩包 `load_errors` 未定义隐患 | `app/api/v1/data.py` | RQ-038/S1 |
| 08-04 | E2E 验证（Postgres 实测）：单文件 bowtie 自相交→MultiPolygon 入库、3D 降维、feature_count=5/5；ZIP 并发 3 子文件独立会话 detail 回填与真实入库数一致（3+2，CSV 行数 2 汇总 7）；单文件/压缩包双场景收尾回归通过 | 验证 | 验证 |
| 08-04 | **S2 CRS 元数据:** 三表 geometry 列去 SRID typmod（迁移 0005）｜入库按 detected_crs 写源 SRID（9 处调用点传 srid）｜预览查询 CASE 自适应 ST_Transform→4326｜转换写回目标 SRID + detected_crs 联动｜repair 保留原 SRID｜`crs_to_srid` 新增 + `transform_coords` always_xy | `app/models/spatial_feature.py`, `alembic/versions/0005_remove_srid_typmod.py`, `app/services/feature_store.py`, `app/services/crs_transform.py`, `app/services/spatial_query.py`, `app/api/v1/data.py`, `app/api/v1/spatial.py` | RQ-038/S2 |
| 08-04 | **S2 修复附带:** `_transform_coords` tuple 不可变赋值 bug（shapely mapping 返回 tuple，转换恒 0）重写递归列表化；`func.case`→`sqlalchemy.case`（预览 500 根因） | `app/services/feature_store.py` | RQ-038/S2 |
| 08-04 | S2 E2E 验证（Postgres 实测）：4547 投影上传 SRID=4547+预览转 4326 经纬度正确 / 4326 回归 / transform 4326→4547（transformed=1、detected_crs 更新、误差<0.02°）/ repair 保留 4490 / S1 回归通过 | 验证 | 验证 |
| 08-04 | 文档更新（v2.8 规划 S1 标记完成 / 修订记录 v2.8 / 修订全纪录） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `VCS_项目修订全纪录.md` | 文档 |
| 08-04 | **S3 3D 降维:** `geojson_to_wkt_status` 对 MultiPatch/PolyhedralSurface 按 surface 首环外环语义降维 2D MultiPolygon、TIN 每三角形一面（Z 由 _force_2d 丢弃）；`_guess_format` 补 `.obj→OBJ`；`extract_features_from_file` 新增 OBJ 分支走 `obj_parser.py`（每对象质心 Point + vertex/face 统计属性） | `app/services/spatial_loader.py` | RQ-038/S3 |
| 08-04 | **S3 类型归一化 + 回填:** `write_feature` 新增 `_GEOM_TYPE_NORMALIZE`（MultiPatch/PolyhedralSurface→MultiPolygon、TIN→Polygon）保证分表路由；upload 非压缩包路径回填 `feature_count=written`（原停留检测值，OBJ 时=面数非对象数）+ geometry_type + error 透传 warning | `app/services/feature_store.py`, `app/api/v1/data.py` | RQ-038/S3 |
| 08-04 | S3 E2E 验证（Postgres 实测）：OBJ 上传 feature_count=2（实际写入数）、geometry_type=Point、预览 2 要素 2D 坐标、DB 2 条几何非空（SRID=4326、object_name 保留）；秒传去重复用正确；S2 回归 4/4 通过；MultiPatch/TIN 单元验证（本机无法构造 MultiPatch 样本，遗留说明见文档） | 验证 | 验证 |
| 08-04 | S3 文档登记（v2.8 规划 S3 标记完成 / 修订记录 S3 章节 / 测试手册 TC-S3.1~S3.5） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-05 | **S4 链路透传:** `run_quality_check`/`run_attribute_checks`/`load_standard_tables` 全链新增 `standard_table_id` 过滤（指定时仅检查该标准表）；`StandardTableDef` 增 `table_id` | `app/services/quality_checker.py`, `app/services/quality/attribute_checker.py` | RQ-038/S4 |
| 08-05 | **S4 单表检查模式:** standard_table_id 给定（文件关联表）时 A1 必要图层检查跳过「标准表名 vs 源图层名」误报（两命名空间本不同），逐表检查改用文件全部要素；字段级 A2/A3/A4/A5/A6/A8/A9 照常执行；多表模式保持原行为 | `app/services/quality/attribute_checker.py` | RQ-038/S4 |
| 08-05 | **S4 上传回填:** link 分支按「几何类型匹配」回填 `standard_table_id`（无匹配取首表）；层组多表几何校验升级（存在任一匹配即通过，无匹配 400）；`StoredFileUploadResponse` 补 `standard_table_id`，直接上传/秒传/切片合并三路回填 | `app/api/v1/data.py`, `app/schemas/storage.py` | RQ-038/S4 |
| 08-05 | **S4 前置校验 + 分页:** `feature_count=0` 先查 spatial 分表实际要素数（有则放行，无则 400 + reindex 引导）；修复函数内 import 位置导致 UnboundLocalError；`/data` page_size 100→200、`/quality` 50→200 | `app/api/v1/quality.py`, `app/api/v1/data.py` | RQ-038/S4 |
| 08-05 | **S4 前端联动:** 文件切换自动回填关联标准（`applyFileStandard`）；`doQcStored` 携带 `standard_table_id`（标准一致时）；`loadStoredSel`/`loadQCReports`/`loadStdsSel` page_size→200；feature_count=0 引导提示 | `app/frontend/index.html` | RQ-038/S4 |
| 08-05 | S4 E2E 验证（Postgres 实测，`s4_e2e.py` 17/17）：上传关联层组 → 响应+详情双回填 table_id → 不带标准/表质检正确继承关联标准执行字段级检查（6 项，A1 pass 不误报）→ 几何不匹配上传 400；S2 回归 4/4、S3 OBJ 回归通过、前端 node --check 通过 | 验证 | 验证 |
| 08-05 | S4 文档登记（v2.8 规划 S4 标记完成 / 修订记录 S4 章节 / 测试手册 TC-S4.1~S4.4） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-05 | **S5 详情 schema 透传:** `QualityReportDetail` 新增 `attr_results`/`topo_results`/`total_violations`/`violation_samples`/`severity_dist`（默认值 + from_attributes 保留，兼容存量）；`CheckItemResult.samples`/`ViolationSample.sample` 放宽类型 | `app/schemas/quality.py` | RQ-038/S5 |
| 08-05 | **S5 详情接口手工构建:** `GET /quality/{id}` 从 JSONB 重建检查项列表；违规样本（attr `samples[:5]` int→feature_index、topo `feature_indices[:5]`，前 100）；`total_violations`（fail 项 failed_count 求和）；`severity_dist`（fail 按 severity 计数） | `app/api/v1/quality.py` | RQ-038/S5 |
| 08-05 | **S5 HTML 报告模板化:** 五维 SVG 环形仪表（综合评分大环 + 5 维度环，≥85 绿/≥60 黄/红）+ 严重度分布条 + 违规样本明细表（要素索引/值着色）+ `@media print` 打印样式，全部内联零 CDN | `app/api/v1/quality.py` | RQ-038/S5 |
| 08-05 | **S5 前端详情面板:** `toggleQcDetail` 新增五维评分卡片行 + 严重度分布条 + 违规样本明细表（跨列着色），直接消费详情新字段 | `app/frontend/index.html` | RQ-038/S5 |
| 08-05 | **S5 顺带修复 A4 必填漏判:** pandas 2.x 字符串列 dtype 为 `str` 非 `object`，原 `if col.dtype == object` 导致空字符串不触发必填违规；统一 isna+空串掩码；样本由 df 行序映射回 `feature_index`（此前永远取不到） | `app/services/quality/attribute_checker.py` | RQ-038/S5 |
| 08-05 | S5 E2E 验证（Postgres 实测，`s5_e2e.py` 28/28）：违规报告详情字段全对齐（attr 7/topo 9、total_violations=3、severity_dist={error:2}、样本 feature_index=1 与 "X2"）；HTML 五维/严重度/样本/print；无违规报告兼容；S4 回归 17/17、S2 回归 4/4、前端 node --check 通过 | 验证 | 验证 |
| 08-05 | S5 文档登记（v2.8 规划 S5 标记完成 / 修订记录 S5 章节 / 测试手册 TC-S5.1~S5.5） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-05 | **S6 导出对齐:** Excel 导出重写为五 Sheet（标准信息/层组定义/数据表定义/字段定义/字典定义），列位与模板/解析器完全一致；层组「父级编码」改导出 `group_code`（`group_code_by_id` 索引），不再导出 UUID；表/字段首列改导出 `table_code`；字段补 `field_alias`（中文名）；必填/主键导出「是」布尔；JSON 导出层组过滤 `parent_id is None` | `app/api/v1/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 模板补字典 Sheet:** `download_excel_template` 新增 Sheet 5「字典定义」（PT_TYPE 点类型 A 平面点/B 高程点示例） | `app/api/v1/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 解析器:** `V2FieldDef` 新增 `field_alias`（读「中文名」列下标 2）；`V2DictDef` 新增 `description`；新增字典定义 Sheet 解析（每字典一行、字典项展开多行按 dict_code 聚合、scope/描述容错、识别标志补「字典定义」） | `app/services/data_standard_parser.py` | RQ-038/S6 |
| 08-05 | **S6 字典导入:** `_import_from_v2` 按 dict_code 聚合创建 `StandardDictionary` + `DictionaryItem`（scope 校验 standard/global）；`ImportResult` 补 `dictionaries_count` | `app/api/v1/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 详情树去重:** `DataStandard.layer_groups` 关系返回全部层组（含子层组）→ 子层组同时出现在顶层与父 children 重复渲染；`DataStandardDetail` 加 `model_validator` 顶层仅留根层组 | `app/schemas/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 bug① 超管建字典恒 409:** `AuthContext.create_super_admin` 使用虚拟 ApiKey ID（不在 `api_keys` 表），写 `api_key_id` 触发外键失败被 IntegrityError 兜底误报「名称已存在」；新增 `owner_id` 属性（超管→None），三处字典插入改之；兜底 handler 补真实异常日志 | `app/core/auth_context.py`, `app/api/v1/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 bug② 详情 dictionaries 恒空:** schema 字段名与模型属性 `standard_dictionaries` 不匹配 → 加 `validation_alias` 映射 | `app/schemas/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | **S6 bug③ 中文标准名导出 500:** `Content-Disposition` 文件名含中文触发 latin-1 UnicodeEncodeError → 新增 `_content_disposition`（RFC 5987 `filename*` UTF-8 转义 + ASCII fallback），JSON/Excel 两分支共用 | `app/api/v1/data_standard_v2.py` | RQ-038/S6 |
| 08-05 | S6 E2E 验证（Postgres 实测，`s6_e2e.py` 40/40）：导出五 Sheet 列位对齐、层组父级编码引用非 UUID、字段必填/主键「是」+ 中文名回环、字典 Sheet 含项/空字典保行、导入回环计数（3 层组/2 表/4 字段/2 字典/2 字典项）、详情树去重 + dictionaries 非空；S4 回归 17/17、S5 回归 28/28 | 验证 | 验证 |
| 08-05 | S6 文档登记（v2.8 规划 S6 标记完成 / 修订记录 S6 章节 / 测试手册 TC-S6.1~S6.9） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-05 | **S7 模型+迁移:** `DataStandard` 新增 `code` 列 + `uq_ds_api_code`（api_key_id, code）唯一约束 | `app/models/data_standard.py`, `alembic/versions/0006_add_standard_code.py` | RQ-038/S7 |
| 08-05 | **S7 导入三模式:** JSON/Excel/CSV 三个导入端点统一支持 `mode=create|update|upsert`；`_resolve_import_target` 按 code（优先）→name 匹配目标（scope_clause 数据范围），update 未命中 404；命中则删子→重建→版本快照+1；create/update 撞唯一约束 409 | `app/api/v1/data_standard_v2.py` | RQ-038/S7 |
| 08-05 | **S7 版本联动:** `_import_version_snapshot` 复用发布链路（快照→diff→写 `StandardVersion`，`api_key.owner_id` 超管 None 安全）→ `target.version += 1`；`ImportResult` 补 `mode`/`updated`/`version` | `app/api/v1/data_standard_v2.py` | RQ-038/S7 |
| 08-05 | **S7 根因修复 MissingGreenlet:** `_delete_standard_children` bulk 删除后 identity map 驻留旧子记录，`db.expire_all()` 在 async 会话触发同步刷新 IO → 500；改 `_build_standard_snapshot` 加 `.execution_options(populate_existing=True)` 并删除全部 4 处 `db.expire_all()`（JSON/Excel 扁平/Excel v2/CSV 四更新路径） | `app/api/v1/data_standard_v2.py` | RQ-038/S7 |
| 08-05 | **S7 导出/模板/解析器补标准编码:** JSON/Excel 导出与模板「标准信息」Sheet 补 `STD_CODE` 行；parser `V2StandardParseResult.code` 读「标准编码」；create/update_standard 支持 code + 409；`DataStandardCreate/Update/Brief/Detail` 补 `code` | `app/api/v1/data_standard_v2.py`, `app/services/data_standard_parser.py`, `app/schemas/data_standard_v2.py` | RQ-038/S7 |
| 08-05 | S7 E2E 验证（Postgres 实测，`s7_e2e.py` 37/37）：create 带 code/同名 409、update 按 code 重建 version+1 与表名替换/新字段落库、未命中 404、upsert 新建+命中更新、Excel 导出→改字段定义加 h_grade→回传 5 字段+中文名回环 version+1、版本历史 2 条 + diff 含 h_grade、POST 带 code + code 冲突 409、四更新路径全 201（MissingGreenlet 修复验证） | 验证 | 验证 |
| 08-05 | S7 文档登记（v2.8 规划 S7 标记完成 / 修订记录 S7 章节 / 测试手册 TC-S7.1~S7.9） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-05 | **S8 diff 自然键对齐:** `_deep_diff` 列表改按 `_LIST_KEY_CHAIN` 自然键配对（层组/表/字段/字典/字典项），消除中间插入导致的全段级联漂移；路径可读 `layer_groups/BG/tables/CTRL_PT/fields/elevation`；`_IGNORED_KEYS={id,ordering}` 剔 UUID/排序噪音；`_compute_diff` 剔 `standard/version` | `app/api/v1/data_standard_v2.py` | RQ-038/S8 |
| 08-05 | **S8 统计/摘要/中文名:** `_diff_entity_stats` 最内层实体去重统计 + `_diff_summary` 中文摘要（如「新增：字段 1、字典项 1」）+ `_collect_diff_labels` 自然键→中文名映射（前后快照合并）；`VersionDiff` 新增 `stats`/`summary`/`labels`；快照补 `field_alias` | `app/api/v1/data_standard_v2.py`, `app/schemas/data_standard_v2.py` | RQ-038/S8 |
| 08-05 | **S8 前端层级折叠树:** `v2ShowDiff` 实体级统计徽标（+N/-N/~N）+ 变色摘要 + 新增/删除/修改三分区；`_renderDiffTree` 按路径段建折叠树（中文名优先+编码灰显、修改显示旧→新）；**顺带修复必崩 bug**：收尾 `render(root)`→`render(root.children)`（Object.entries(undefined) 崩溃根因） | `app/frontend/index.html` | RQ-038/S8 |
| 08-05 | S8 E2E 验证（Postgres 实测，`s8_e2e.py` 27/27 + `s8_unit.py` 10/10 + Playwright UI 12/12）：中间插入 elevation 仅 1 条 added 无级联漂移、路径可读、`standard/version` 噪音剔除、stats/summary 口径正确、前端层级树中文名+10→12 正确渲染；S7 回归 37/37 | 验证 | 验证 |
| 08-05 | S8 文档登记（v2.8 规划 S8 标记完成 / 修订记录 S8 章节 / 测试手册 TC-S8.1~S8.7） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-06 | **S9 表多分类模型:** 新增 `StandardLayerGroupTableLink`（`layer_id`/`table_id`/`is_primary`/`ordering`，联合唯一 + 双外键 CASCADE）+ 迁移 0007 建表/索引/回填（存量表在主子层组建 `is_primary=True` 关联行，无损升级）；`StandardLayerGroup`/`StandardTable` 增 `table_links` 关系与 `table_count` | `app/models/standard_layer_group_table_link.py`(新增), `app/models/standard_layer_group.py`, `app/models/standard_table.py`, `alembic/versions/0007_layer_group_table_links.py`(新增) | RQ-038/S9 |
| 08-06 | **S9 主归属唯一真源 + 7 创建点注入:** 主归属 = `standard_tables.layer_id` + 关联表 `is_primary=True`（互相同步）；`_primary_table_link` 辅助注入全部 7 处建表点（create_table/复制/快照回滚/Excel 扁平/v2 children/CSV/JSON 导入）；`list_tables` 重写为关联表 join（返回本层组全部表含次归属 + `is_primary`）；`list_layers` 用 `_layer_tree_options` 深载修复 3 层树 MissingGreenlet | `app/api/v1/data_standard_v2.py` | RQ-038/S9 |
| 08-06 | **S9 移动/复制/删除规则:** B2 防环（目标祖先链上溯命中自身 400）+ B3 跨标准移动 400「请使用复制」+ B1 跨标准复制（`new_standard_id`，`_copy_layer_recursive` 重写 async 显式查询，同标准保留子树外次归属/跨标准丢弃）+ B5 删除守卫（`_find_shared_primary_tables` 子树主归属被子树外引用 400） | `app/api/v1/data_standard_v2.py` | RQ-038/S9 |
| 08-06 | **S9 归属 API + 前端:** `POST/DELETE /layers/{lid}/tables/{tid}/attach`（次挂载/主归属转移+layer_id 同步/主归属摘挂 400）+ `GET /tables/{tid}/layers`（`TableLayerLink` 主前排序）；前端表行主/次徽标 + ⛓ 归属管理面板 + 选中表归属芯片 + 移动对话框去跨标准下拉/复制对话框加目标标准下拉 | `app/api/v1/data_standard_v2.py`, `app/schemas/data_standard_v2.py`, `app/frontend/index.html` | RQ-038/S9 |
| 08-06 | **S9 顺带修复回滚 500（真实 bug）:** `DataStandard.layer_groups` 返回全部层组行，`_build_standard_snapshot` 顶层同时输出嵌套子层组（children 内）与扁平子层组（带 parent_id）→ 快照同层组双份 → 回滚恢复对同 id 双插 `standard_layer_groups_pkey` 唯一冲突；修复为顶层仅根层组（`parent_id is None`），子层组经 children 递归；嵌套导入标准回滚 500→201 | `app/api/v1/data_standard_v2.py` | RQ-038/S9 |
| 08-06 | S9 E2E 验证（Postgres 实测，`s9_e2e.py` 49/49 + `s9_import_probe.py` 7/7 + Playwright UI 21/21）：主归属自动建/B3 拦截/B2 防环/次挂载与主归属转移与 layer_id 同步/主归属摘挂 400/B5 守卫/同标准复制保留次归属/跨标准复制丢弃子树外次归属/发布回滚次归属不进快照/嵌套树回滚 201；回归 S7 37/37 + S8 27/27 + s8_unit ALL PASS + 前端 node --check 通过（截图 s9_front_shot.png / s9_attach_shot.png） | 验证 | 验证 |
| 08-06 | S9 文档登记（v2.8 规划 S9 标记完成 / 修订记录 S9 章节 / 测试手册 TC-S9.1~S9.8） | `docs/plan_v2_8.md`, `docs/revision_v2_8.md`, `docs/test_manual_v2_8.md` | 文档 |
| 08-06 | **API 测试台新增 S9 section:** `test.html` 新增「9. 数据标准 v2 · S9 多分类 (12 项)」——一键 S9 场景（23 步 E2E：建标准→嵌套树→建表自动主归属→次挂载→B5 守卫→主归属转移→摘挂→B2 防环→B3 跨标准移动→B1 跨标准复制→发布×2→嵌套树回滚→清理）+ 11 单点卡片（建标准/层组/表、层组表列表、表归属、挂载三态、移动/复制/删层组、发布/版本/回滚、删标准），自动填充 ID 联动；**顺带修复既有 bug**：`setStatus` 用 `card.className=...` 覆盖抹掉 `open` 致卡片运行后自动折叠 → 保留 wasOpen | `app/frontend/test.html` | RQ-038/S9 |
| 08-06 | S9 测试台 E2E 验证（Playwright，`s9_testbench_front.py` 17/17）：一键场景 23/23 通过 0 失败；单点按钮链路（建标准→层组→表→归属查询→次挂载→清理）全通；无 JS 控制台错误/无意外 404（截图 s9_testbench_shot.png） | 验证 | 验证 |
| 08-06 | S9 测试台文档登记（测试手册 TC-S9.9） | `docs/test_manual_v2_8.md` | 文档 |
| 08-06 | **测试台 runAllTests 10 项失败修复:** ①`POST /convert` 在 Redis 未启动时 apply_async 因 kombu 对 broker/result backend 的重试连接挂起数分钟占住服务线程，后续请求排队 15s 超时（`signal is aborted without reason`×7）→ 新增 `_redis_available()` 2s 探活，不可用即降级进程内同步执行 `run_conversion.apply`（临时置 `max_retries=0` 短路 self.retry 防再连 Redis 挂起）；②`/quality/check` 已改为 file_id 必填 query，测试台仍按旧语义 multipart 上传 → 422×2 → 改用 `/quality/check/upload` 旧兼容端点；③data-list/data-search 适配 `{items,total,page,page_size}` 分页对象；④data-download 服务端「仅入库不存原始字节」恒 410 → 判定 warn；⑤celery_app 增 `broker_connection_retry=False` / `broker_connection_max_retries=0` / `broker_transport_options` 快速失败 | `app/api/v1/convert.py`, `app/workers/celery_app.py`, `app/frontend/test.html` | RQ-001/RQ-008 |
| 08-06 | **GDB 压缩包上传两问题修复 + FileuploadService 借鉴:** ①压缩包上传误受 100MB 单文件（BYTEA 上限）限制 → 新增 `max_archive_size_mb=500`，压缩包跳过单文件限制、分块上限按类型分级（非压缩包 200MB/100MB 不变）；②GDB 每 layer 先转 GeoJSON bytes 存 BYTEA 再二次提取（内存膨胀、观感"存成 GeoJSON"）→ 重构 gdb_processor 为 `prepare_gdb`（写临时目录+列层元数据 CRS/几何类型）+ `read_layer_features`（Fiona 逐层流式读取），data.py GDB 分支直接以 PostGIS 原生几何（POINT/LINESTRING/POLYGON/Multi*）写入分表，子记录 file_format=gdb、file_data=None，feature_count 由主流程按子任务统计回填；③借鉴 FileuploadService：ZIP 中文文件名乱码修复（无 UTF-8 flag 时 cp437→gbk 还原）| `app/config.py`, `app/services/gdb_processor.py`, `app/services/archive_extractor.py`, `app/api/v1/data.py` | RQ-008 |
| 08-07 | **真实 GDB 上传 500 修复（StringDataRightTruncationError）:** ①根因: 新代码将 GDB layer 完整 CRS WKT（`PROJCS["Beijing 1954 / Gauss-Kruger CM 123E"...]` 618 字符）写入 `stored_files.detected_crs`，DB 列为 varchar(64) → 批量 INSERT 超长报错、事务回滚 → 500；模型已声明但 DB 未迁移。修复: 模型 String(64)→String(2048) + `ALTER TABLE stored_files ALTER COLUMN detected_crs TYPE VARCHAR(2048)`（实测 618 字符 WKT 存取正常）。②同时发现数据完整性问题: `max_features_per_upload=2000` 对真实图层（GDB 4.9 万要素）静默截断 → 提至 200000，`write_features_batch` 返回 `total/truncated` 统计，上传响应 warning 明确提示截断图层与条数。③修复新路径 SHP/GeoJSON 子记录 `feature_count` 未回填致 archive 聚合恒 0（旧 GeoJSON 路径正常）→ 统一按 `source_archive` 回填所有子记录 feature_count。④清理旧代码产物: 07-56 上传的 `组图形.gdb.zip` 乱码残留（GBK zip 无 UTF-8 flag，`组图形` 被 cp437 误读为 `╫Θ═╝╨╬`，子记录为 GeoJSON 中间产物）——删除主/子记录及分表 4045 行，待用户重传。实测: SHP zip 上传 archive feature_count=2（聚合修复）；runAllTests 回归 20 项 pass=19 fail=0 warn=1；618 字符 CRS WKT 存取正常 | `app/models/stored_file.py`, `app/config.py`, `app/services/feature_store.py`, `app/api/v1/data.py`, DB ALTER | RQ-008 |
| 08-08 | **v2.10 空间分表 LIST 分区化（"一个数据源一个表"）+ 流式全量落库:** 方案对比 A 每源动态表（质检 ORM/SQL 全盘重写，出局）/ B LIST 分区（现有代码零改动+分区裁剪+detach 秒删，中标）/ C 提上限（过渡）。①三张分表转 `PARTITION BY LIST (stored_file_id)`，每源一物理分区 `{table}_p_{file_id}`（uuid 含 `-` 故分区名 `-`→`_`，FOR VALUES 用原始 sid）；PK 改复合 `(stored_file_id, id)`；`stored_file_id` NOT NULL；`properties` JSON→JSONB（GIN 索引要求）；Geometry 统一 `'GEOMETRY'`（分区父/子 typmod 须一致）；`db.api_key_id` 无 stored_files FK 的说明之外**新增关键点：分表保留 api_keys FK，超级管理员虚拟 id `0000...0001` 违反 FK → 分表写入改用 `auth.owner_id`（super admin→None），查询 count/query 支持 api_key_id=None 不过滤**。②流式全量: `read_layer_features` 生成器化 + `write_features_batch_sync`（SyncSessionLocal + flush_every=1000）+ data.py `_load_gdb_layer_async`→`to_thread(_sync_load_gdb_layer)`；**移除 `max_features_per_upload` 与全部 truncated 逻辑**（真实图层数十万要素不再截断）。③删除端点改 `_delete_file_recursive`（父记录递归删子记录+各分区 DETACH+DROP 秒删）。④asyncpg 不支持 DDL 参数绑定 → `_ensure_partition` 的 `FOR VALUES IN` 改内联（sid 内部 uuid+防御转义）。实测: ORM 级 sync/async 写+查+drop 全通；HTTP e2e SHP zip 上传 201→自动建分区→要素查询 2→删除后分区全消+stored_files 0；runAllTests 20 项 pass=19 fail=0 warn=1 | `app/models/spatial_feature.py`, `app/models/stored_file.py`, `app/config.py`, `app/db/session.py`, `app/services/feature_store.py`, `app/services/gdb_processor.py`, `app/api/v1/data.py`, DB 重建（DROP+create_all） | RQ-008 |
| 08-10 | **v2.10.1 GDB 上传要素为 0 静默失败修复:** ①根因: GDB 子任务分表写入仍传 `api_key.id`（超级管理员虚拟 id `0000...0001`）→ 违反分表 FK `polygon_features_api_key_id_fkey` → 整批回滚 → written=0 且旧代码吞异常（仅 logger.warning）→ 静默 0；SHP/GeoJSON 路径此前已改 owner_id，GDB 路径漏改。修复: `_handle_gdb_group` 新增 `api_key_owner_id` 参数——StoredFile 子记录保留 `api_key_id`（=api_key.id，NOT NULL），分表子任务改用 `api_key_owner_id`（super admin→None，FK 安全）；两处调用点均传 `api_key_id=api_key.id, api_key_owner_id=api_key.owner_id`。②防御加固: `gdb_processor.read_layer_features` 异常不再吞（异常从迭代处向上传播至 `_sync_load_gdb_layer` → `stats["error"]` → 上传响应 warning 明确提示失败图层与原因）；新增 `gdb_read_lock` per-path threading 锁（并发 fiona.open 同一 GDB 防护；无锁 5 线程并发实测全正常，锁为防御性保留）；两处临时目录 rmtree 前 `gc.collect()` 且失败不再静默（记日志保留目录待查）。实测: HTTP e2e marker zip 上传 201 无 warning、子记录 fc=2、分区 `polygon_features_p_{sid}` 2 行；损坏 GDB 直调 `_sync_load_gdb_layer` 正确返回 `stats["error"]`（旧代码会静默 0）；GeoJSON 上传回归 201/fc=1/删除 204；测试数据全部清理（孤儿分区 DROP、临时目录删除），用户 7 条记录完好 | `app/services/gdb_processor.py`, `app/api/v1/data.py` | RQ-008 |

### Phase 17 ：v2.9 数据字典展示拆分（2026-08-12）

**需求来源：** RQ-039（用户反馈 2026-08-12「数据字典展示需要拆分 全局字典 或者对应数据标准字典 不要跟表做关联」）

| 日期 | 修改内容 | 涉及文件 | 需求 |
|------|----------|----------|------|
| 08-12 | **v2.9 字典维度拆分（后端）:** `GET /{stdId}/dictionaries` 强制 `scope='standard'`（去 scope 参数）；`POST /{stdId}/dictionaries` 拒绝 `scope='global'`（400 引导走全局端点）；**新增 `POST /dictionaries`** 创建全局字典（`standard_id=NULL`、scope 强制 global、应用层重名预查——`uq_sd_standard_name` 对 NULL standard_id 不生效）；`GET /dictionaries` 只返回 `scope='global' AND standard_id IS NULL`；`PUT /dictionaries/{id}` scope 一致性（转 global 清 standard_id、转 standard 无归属 400）；`share_dictionary` 转 global 同时 `standard_id=None` | `app/api/v1/data_standard_v2.py` | RQ-039 |
| 08-12 | **v2.9 字典维度拆分（前端）:** 右栏字典面板双 tab（🌐 全局字典默认 / 📋 标准字典）+ `.v2-dict-tab` CSS；`V2.dictTab` 状态 + `initStds` 默认 `v2LoadDicts()`；重写 `v2LoadDicts`/`v2AddDict` 按 tab 分流（global→`GET/POST /dictionaries` 不依赖选中；standard→`/{stdId}/dictionaries` 仅依赖 `V2.stdId`，未选标准提示「请先在左侧选择数据标准」）；新增 `v2DictTab`/`_v2RenderDictList`（badge 共享/标准）；`v2DelStd` 删除后刷新字典面板；字段-字典关联下拉核对无需改动 | `app/frontend/index.html` | RQ-039 |
| 08-12 | v2.9 验收（后端集成 8/8 + Playwright 冒烟/端到端 0 JS 错误）：标准端点 201 scope=standard / 拒 global 400 / 全局端点 201 scope=global standard_id=NULL / 两列表纯维度 / share 清归属 / update 一致性 / 维度无重叠；UI 默认全局 tab、未选标准提示、创建全局字典「共享」badge、建标准后标准 tab 创建「标准」badge、双 tab 互不串扰；py_compile + node --check 通过 | 验证 | 验证 |
| 08-12 | **v2.9 数据清洗:** 发现 2 条 `scope='global'` 且 `standard_id` 非空的隐身脏字典（拆分前 share 历史遗留，两维度列表均不可见）；清理 32 个测试标准及全部关联数据（86 sheets 字段/7 sheets/6 字典/9 版本/54 标准字段/33 标准表/39 层组/1 孤儿质检规则）；外围 SET NULL 保留 1 源表/5 存储文件/109 质检报告 | DB 数据清理 | RQ-039 |
| 08-12 | v2.9 文档登记（修订记录 v2.9 / 测试手册 v2.9 TC-A1~A8·B1~B7 / 全纪录 RQ-039+Phase 17+版本发布 2.9.0 / 优化实施记录 Phase 17） | `docs/revision_v2_9.md`(新增), `docs/test_manual_v2_9.md`(新增), `VCS_项目修订全纪录.md`, `VCS_优化实施记录.md`, `overview.md`, `README.md` | 文档 |
| 08-12 | **字典父级关联方案存档（暂不实施）:** 决策：字典条目层级能力当前无阻塞需求，暂不实施；完整方案存档 `docs/proposal_dictionary_hierarchy.md`——方案 A 校验加固（最小）/ **方案 B 自引用外键（推荐）** / 方案 C 独立关联表 DAG；推荐方案含数据模型（parent_id FK ondelete RESTRICT + 保留 parent_code 双写）、API 校验（父存在/自引用/深度≤3/删父有子 409）、A5 父码通配（默认关闭零回归）、Excel 两遍法导入 + 树前序导出、前端父级下拉/树渲染，分期 P1→P4；兼容性四保证（存量数据/API/质检/旧 Excel 均不破坏）；明确不做：多父 DAG、跨字典引用、拖拽编排 | `docs/proposal_dictionary_hierarchy.md`(新增), `docs/revision_v2_9.md` | 文档 |
| 08-12 | **方案文档补充「现状 vs 方案 B」决策对比（§3.1）:** 补全数据完整性（假父/自引用/深度/删父保护六项对比）、质检父码通配增值、**版本快照与回滚（确认 `_build_standard_snapshot` 已携带字典 items，方案 B 双写使快照零改动兼容）**、Excel 往返、**多标准复用放大效应**、成本风险（含**失败模式：静默 vs 显式**）、现实例子、决策结论（树→B / 枚举→现状 / 拿不准→先补方案 A 校验缺口） | `docs/proposal_dictionary_hierarchy.md` | 文档 |

---

## 4. 非项目内容归档

以下文件原为项目目录中的非项目内容，已移出或删除，内容摘要归档于此。

### 4.1 CLAUDE-FABLE-5.md（已删除，120KB）

**文件性质：** Anthropic Claude Fable 5 模型 System Prompt 配置文件，不属于本项目。

**来源：** 可能是被复制/泄漏到此工作区。

**包含内容：**
- Claude Fable 5 产品信息、行为规则、安全策略（约 110 行）
- Artifact 存储 API（`window.storage` 前端存储接口）
- MCP App 连接器搜索与建议
- 搜索指令、版权合规规则
- 完整工具定义（JSON Schema，约 700 行）
- Claudeception（AI Artifact 中嵌入 API 调用）
- 网络配置域名白名单、文件系统只读目录布局

**处理意见：** 安全审计报告确认无恶意代码，但存在 IP 泄漏/知识产权合规风险。已删除。

### 4.2 旧审查与规划文档（已删除）

| 文件名 | 大小 | 性质 | 归档说明 |
|--------|------|------|----------|
| `CODE_REVIEW_优化方案.md` | 9.8KB | 外部 AI 代码审查 | 优化项已逐条实施，方案内容已吸收至 `VCS_优化实施记录.md` |
| `FINAL_REVIEW_全面审查报告.md` | 8.3KB | 全量审查报告 | 评分基于 Phase 4-6 改造，当前项目已超出该范围 |
| `PHASE1_实施报告.md` | 3.4KB | 阶段性报告 | 内容已纳入修订时间线 §3.7 |
| `详细工作计划_WBS.md` | 116KB | WBS 分解 | 工作项已全部实施完成 |
| `功能增强与数据管理优化计划.md` | 23KB | 规划文档 | 功能已实施，优化建议已纳入 §3.7-3.8 |

### 4.3 旧版本项目方案文档（已删除）

| 文件名 | 大小 | 归档说明 |
|--------|------|----------|
| `VCS_前端优化方案.md` | 6.9KB | 优化方案已实施，内容已吸收至 `README.md` |
| `VCS_部署方案.md` | 20KB | 已被 `README.md` 部署章节取代 |
| `VCS_项目书.md` | 20KB | 已被 `README.md` 取代 |
| `VCS_项目全书.md` | 29KB | 已被 `README.md` 取代 |
| `VCS_项目建设书.md` | 18KB | 需求已全部录入 §2 |
| `VCS_Linux部署文档.md` | 23KB | 部署信息已过时（端口 8000），已被 `README.md` 取代 |

### 4.4 一次性迁移脚本（已删除）

以下脚本均为一次性迁移/修复工具，改造完成后无保留价值：

```
scripts/add_spatial_viewer.py
scripts/apply_gdb_support.py
scripts/apply_round2_fixes.py
scripts/apply_round3_fixes.py
scripts/frontend_redesign.py
scripts/restore_standards.py
scripts/restructure_frontend.py
scripts/unify_frontend.py
scripts/update_frontend.py
scripts/update_frontend_v2.py
scripts/update_quality_reports.py
scripts/backup.py
```

### 4.5 外部 AI 分析脚本（已删除）

以下脚本来自外部 AI（空间API服务1）的独立分析，不属于项目代码：

```
(项目根目录下的 analyze_img.py 等脚本已在清理时删除)
```

### 4.6 运行时生成数据（已删除）

| 文件 | 说明 |
|------|------|
| `dump.rdb` | Redis 自动生成的持久化文件 |
| `vcs_local.db` | SQLite 测试数据库 |

---

## 5. 版本发布记录

| 版本 | 日期 | 主要变更 | 对应 Phase |
|------|------|----------|------------|
| 1.0.0 | 2026-05-09 | 基础转换服务 + 质量检查 + 前端 SPA + Docker Compose | Phase 1 |
| 1.1.0 | 2026-05-20 | 数据存储 + 空间要素分表 + 数据标准 + 拓扑检查 + 增量同步 | Phase 2 |
| 1.2.0 | 2026-06-11 | 前端 6 页重构 + 详情面板 + 端口 45678 | Phase 3 |
| 2.0.0 | 2026-06-15 | 外部 AI 优化采纳 + Schemas 拆分 + Alembic + CI/CD | Phase 4-6 |
| 2.1.0 | 2026-06-16 | 架构加固（连接池/Celery Beat/CORS/缓存/安全）+ 故障修复 | Phase 7 |
| 2.2.0 | 2026-06-17 | 数据管理页优化（分页可调/日期过滤/消除 N+1） | Phase 8 |
| 2.3.0 | 2026-06-18 | 智能质检规则引擎（规则生成器/引擎/评分/4 张新表/管理 API）+ 修订记录标准化 | Phase 9 |
| 2.3.1 | 2026-07-17 | 版本管理修复（diff 500 / 重复 version_number / _deep_diff 重写） | Phase 10 |
| 2.4.0 | 2026-07-21 | 质检模块重建：标准驱动属性检查 9 项 + 拓扑检查 13 项 + 五维评分 + API 重构 + 前端升级 | Phase 11 |
| 2.4.1 | 2026-07-22 | 数据上传模块重建：按标准分类入库 + 自动建标准表 + 大文件切片 + 数据类型细分 + 标准管理可维护 | Phase 12 |
| 2.5.0 | 2026-07-29 | 分组权限：Group 数据共享 + 超级管理员 X-API-Key + AuthContext + 硬删除 + 分组管理前端 | Phase 13 |
| 2.6.0 | 2026-07-31 | 质检页紧凑重构（文件栏/标准栏/可折叠检查配置/报告表）+ 纯拓扑检查 + 超级管理员可见性修复 | Phase 14 |
| 2.7.0 | 2026-08-04 | 使用问题批量修复（密钥启用/重生成 + 字典查重 + Excel 4-sheet 导入 + 空要素质检 + 健康检查限时 + 前端通用修复）+ restart.bat 卡住修复 | Phase 15 |
| 2.8.0-S1 | 2026-08-04 | v2.8 阶段 A S1：PostGIS 入库稳定性（子任务独立会话 + 分批 flush + make_valid 几何修复 + 非法几何置 NULL） | Phase 16 |
| 2.8.0-S2 | 2026-08-04 | v2.8 阶段 A S2：CRS 元数据链路（去 SRID typmod + 入库源 SRID + 预览 ST_Transform + 转换写回目标 SRID + always_xy 统一） | Phase 16 |
| 2.8.0-S3 | 2026-08-04 | v2.8 阶段 A S3：Multipatch/OBJ 3D 降维入库（MultiPatch/TIN 面语义降维 + OBJ 解析接入 + 3D 类型归一化 + 非压缩包 feature_count 回填） | Phase 16 |
| 2.8.0-S4 | 2026-08-05 | v2.8 阶段 B S4：数据-标准-质检联动（standard_table_id 全链透传 + 单表检查模式 A1 语义修正 + 上传 link 回填 + feature_count=0 区分引导 + 前端文件切换自动带标准） | Phase 16 |
| 2.8.0-S5 | 2026-08-05 | v2.8 阶段 B S5：质检报告可视化（详情接口透传违规明细/严重度分布/违规样本 + HTML 报告五维 SVG 仪表模板化 + 前端详情面板五维卡片；顺带修复 A4 空字符串必填漏判） | Phase 16 |
| 2.8.0-S6 | 2026-08-05 | v2.8 阶段 C S6：Excel 模板补字典 Sheet + 导入导出对齐（导出五 Sheet 列位对齐 + 父级编码引用 + 字段中文名回环 + 字典导入/`dictionaries_count`；顺带修复超管建字典恒 409 外键 bug / 详情 dictionaries 恒空 / 中文文件名导出 500） | Phase 16 |
| 2.8.0-S7 | 2026-08-05 | v2.8 阶段 C S7：标准回传更新（`DataStandard.code` + `uq_ds_api_code`；JSON/Excel/CSV 导入 `mode=create|update|upsert`，update 按 code 匹配删子重建+版本快照+1，未命中 404；根因修复 async 下 `db.expire_all()` MissingGreenlet 500 → `populate_existing`；导出/模板/解析器补标准编码；撞唯一约束 409） | Phase 16 |
| 2.8.0-S8 | 2026-08-05 | v2.8 阶段 C S8：版本对比明细增强（`_deep_diff` 列表按 `_LIST_KEY_CHAIN` 自然键对齐消除级联漂移 + 路径可读 `layer_groups/BG/tables/…/fields/elevation` + `_IGNORED_KEYS={id,ordering}` + 剔 `standard/version`；`VersionDiff` 新增 `stats` 实体去重统计 / `summary` 中文摘要 / `labels` 自然键→中文名映射，快照补 `field_alias`；前端 `v2ShowDiff` 统计徽标+变色摘要+`_renderDiffTree` 层级折叠树中文名优先编码灰显；顺带修复 `_renderDiffTree` 必崩 bug `render(root)`→`render(root.children)`）。验收：单测 10/10 + e2e 27/27 + S7 回归 37/37 + Playwright UI 12/12 | Phase 16 |
| 2.8.0-S9 | 2026-08-06 | v2.8 阶段 C S9：层组多分类 + 移动/复制修复（方案 A 表多分类：新增 `standard_layer_group_tables` 关联表 + 唯一主归属 `is_primary` + 迁移 0007 回填；主归属 = `standard_tables.layer_id` + 关联行双真源同步，7 处建表点全注入；B2 移动防环 / B3 跨标准移动 400 / B1 跨标准复制 / B5 删除引用守卫；新增挂载/摘挂/主归属转移与表归属查询 API + 前端主/次徽标/归属面板/移动复制对话框改造；顺带修复嵌套层组回滚 500 真实 bug——快照顶层误含扁平子层组导致同 id 双插）。验收：e2e 49/49 + 导入探针 7/7 + Playwright UI 21/21 + S7/S8/unit 回归全绿 | Phase 16 |
| 2.9.0 | 2026-08-12 | v2.9 数据字典展示拆分：全局字典 / 对应数据标准字典两个独立维度（不与表/字段关联）——后端 6 处（标准端点强制 standard 维度 + 拒 global 400 + 新增 `POST /dictionaries` 全局创建端点 + 全局列表 `scope='global' AND standard_id IS NULL` + update scope 一致性 + share 清归属）+ 前端双 tab（🌐 全局字典默认 / 📋 标准字典，未选标准提示）；清理隐身脏字典 2 条 + 测试标准全量清理。验收：后端集成 8/8 + Playwright 冒烟/端到端 0 JS 错误 | Phase 17 |
| 2.10.1 | 2026-08-19 | v2.10.1 质检检查器修复：拓扑检查器 `topology_checker.py` 移除全部 13 处 `api_key_id` 查询过滤（要素表 `api_key_id=NULL` vs 存储文件虚拟 ID 导致要素查询 0 条）；属性检查器 `attribute_checker.py` 同步移除；规则生成器 `rule_generator.py` + `quality_rules.py` 从旧 v1 模型迁移到 v2 `StandardField`（新增字典关联 VALUE_IN_SET + 主键 UNIQUE 规则，修复 async 调用）；新增 `GET /data-standards-v2/{id}/qc-summary` 质检概览端点；前端质检配置区标准概览卡片 + 字段字典关联 | Phase 18 |

---

## 6. 附录：文件清单

### 6.1 核心代码文件

```
app/
├── main.py                          # FastAPI 入口
├── config.py                        # pydantic-settings 配置
├── core/
│   ├── security.py                  # API Key 生成/验证
│   ├── deps.py                      # 鉴权依赖注入（返回 AuthContext v2.5）
│   ├── auth_context.py              # AuthContext 认证上下文（新增 v2.5）
│   ├── constants.py                 # 全局常量
│   ├── rate_limit.py                # slowapi 限流
│   ├── cache.py                     # Redis 缓存装饰器
│   ├── file_utils.py                # 文件名安全验证
│   ├── db_utils.py                  # 数据标准字段加载
│   ├── logging_setup.py             # structlog 结构化日志
│   ├── trace_middleware.py          # 请求追踪
│   ├── security_middleware.py       # 安全响应头 + 请求体限制
│   └── services.py                  # Protocol 接口抽象
├── db/session.py                    # 异步 SQLAlchemy 会话
├── models/                          # 18 个 ORM 模型
│   ├── api_key.py                   # API Key（含 group_id FK）
│   ├── key_group.py                 # KeyGroup 分组（新增 v2.5）
│   ├── conversion_job.py            # 转换任务
│   ├── batch_job.py                 # 批量任务
│   ├── quality_report.py            # 质检报告
│   ├── stored_file.py               # 存储文件
│   ├── spatial_feature.py           # 空间要素分表 (point/line/polygon)
│   ├── data_standard.py             # 数据标准 (Standard/Sheet/Field)
│   ├── quality_rule.py              # 质检规则
│   ├── quality_rule_result.py       # 规则执行结果
│   ├── quality_daily_snapshot.py    # 每日质量快照
│   ├── standard_layer_group.py      # 层组（新增 v2.2）
│   ├── standard_table.py            # 标准表（新增 v2.2）
│   ├── standard_field.py            # 标准字段（新增 v2.2）
│   ├── standard_dictionary.py       # 标准字典（新增 v2.2）
│   ├── dictionary_item.py           # 字典条目（新增 v2.2）
│   ├── standard_field_dict_link.py  # 字段-字典关联（新增 v2.2）
│   ├── source_data.py               # 源数据（新增 v2.2）
│   └── __init__.py
├── schemas/                         # 9 个 Pydantic 模块
│   ├── conversion.py                # 转换相关
│   ├── quality.py                   # 质检相关
│   ├── batch.py                     # 批量相关
│   ├── detection.py                 # 格式检测
│   ├── admin.py                     # 管理
│   ├── storage.py                   # 存储 (含 PaginatedFiles)
│   ├── data_standards.py            # 数据标准
│   ├── ws.py                        # WebSocket
│   └── common.py                    # 通用
├── api/v1/                          # 11 个路由模块
│   ├── router.py                    # 路由聚合
│   ├── convert.py                   # 格式转换
│   ├── admin.py                     # API Key 管理 + Group CRUD（v2.5）
│   ├── batch.py                     # 批量转换
│   ├── quality.py                   # 质量控制
│   ├── data.py                      # 数据存储（分页/过滤）
│   ├── data_standards.py            # 数据标准
│   ├── spatial.py                   # 空间处理
│   ├── quality_rules.py             # 质检规则管理（v2.3）
│   ├── data_standard_v2.py          # 数据标准 v2（层组/表/字段/字典/版本）
│   ├── source_data.py               # 源数据管理（v2.2）
│   └── ws.py                        # WebSocket
├── services/                        # 15+ 业务服务
│   ├── quality/                     # 质检子包
│   │   ├── models.py               # 质检数据类
│   │   └── reporter.py             # 报告生成
│   ├── quality_checker.py           # 质量检查主服务
│   ├── format_detection.py          # 格式检测
│   ├── crs_transform.py            # 坐标系解析
│   ├── attribute_processing.py      # 属性处理
│   ├── geometry_repair.py          # 几何修复
│   ├── script_generation.py        # 脚本生成 + 沙箱
│   ├── feature_store.py            # 跨三表 CRUD
│   ├── spatial_loader.py           # 要素提取
│   ├── gdb_processor.py            # GDB 转换
│   ├── archive_extractor.py        # 压缩包解析
│   ├── progress_tracker.py         # 进度发布
│   ├── topology_checker.py         # 拓扑检查
│   ├── data_standard_parser.py     # 数据标准解析
│   ├── quality_exporter.py         # 违规导出
│   ├── validation_pipeline.py      # 四层验证管道
│   ├── data_dedup.py               # 数据去重
│   ├── spatial_query.py            # 空间查询
│   ├── incremental_sync.py         # 增量同步
│   ├── storage_tier.py             # 生命周期管理
│   ├── version_control.py          # 版本控制
│   ├── webhook.py                  # Webhook 通知
│   └── audit_logger.py             # 审计日志
├── frontend/
│   ├── index.html                  # SPA 主页面
│   └── test.html                   # API 测试台
└── workers/
    ├── celery_app.py               # Celery 实例
    ├── tasks.py                    # 转换主任务
    ├── batch_tasks.py              # 批量协调
    └── beat_schedule.py            # 定时任务
```

### 6.2 项目文档文件

| 文件 | 说明 |
|------|------|
| `README.md` | 项目总文档（快速开始/API 索引/架构/部署/开发指南） |
| `overview.md` | 项目总览（精简版） |
| `VCS_优化实施记录.md` | 优化实施追溯（S/A/B/C 分级） |
| `VCS_项目修订全纪录.md` | **本文档**：全量修订时间线 + 需求溯源 + 非项目内容归档 |
| `docs/revision_v2_6.md` | v2.6 修订记录（QC 页重构 + 纯拓扑 + 认证修复 + E2E 验证） |
| `docs/test_manual_v2_6.md` | v2.6 测试手册（QC 紧凑页 / 纯拓扑检查 / 后端接口） |
| `docs/revision_v2_7.md` | v2.7 修订记录（ERROR.txt 使用问题批量修复 + restart.bat 修复 + E2E 验证） |
| `docs/test_manual_v2_7.md` | v2.7 测试手册（密钥生命周期 / 字典校验 / Excel 4-sheet 导入 / 空要素校验） |
| `docs/plan_v2_8.md` | v2.8 规划（阶段 A 数据链路稳定性 / B 质检联动 / C 标准闭环 / D 运营部署，S1~S12 全完成） |
| `docs/revision_v2_8.md` | v2.8 修订记录（S1 入库稳定性 / S2 CRS 元数据 / S3 3D 降维 / S4 联动 / S5 报告可视化 / S6 Excel 对齐 / S7 回传更新 / S8 版本对比 / S9 层组多分类） |
| `docs/test_manual_v2_8.md` | v2.8 测试手册（S1~S9 各阶段回归用例） |
| `docs/deployment.md` | 部署方案（依赖/容器/安全/运营四层 + 磁盘清理 + 端口规划 + 迁移回滚） |
| `docs/revision_v2_9.md` | v2.9 修订记录（数据字典展示拆分：全局 / 标准双维度，不与表/字段关联） |
| `docs/test_manual_v2_9.md` | v2.9 测试手册（TC-A1~A8 后端维度拆分 / TC-B1~B7 前端双 Tab） |
| `docs/proposal_dictionary_hierarchy.md` | 数据字典父级关联方案存档（暂不实施：方案 A/B/C 对比 + 推荐 B 分期 P1→P4 + 兼容性保证） |

### 6.3 配置文件

| 文件 | 说明 |
|------|------|
| `requirements.txt` | Python 依赖 |
| `.env.example` | 环境变量模板 |
| `docker-compose.yml` | Docker Compose 编排 |
| `Dockerfile` | Docker 构建（多阶段） |
| `alembic.ini` | Alembic 迁移配置 |
| `.github/workflows/ci.yml` | CI/CD 流水线 |

### 6.4 工具脚本

| 文件 | 说明 |
|------|------|
| `scripts/init_db.py` | 数据库初始化 + 首个 API Key |
| `scripts/restart.sh` | Git Bash 一键重启 |
| `scripts/restart.bat` | Windows CMD 一键重启 |
| `alembic/` | 数据库迁移版本 |

---

> **维护说明：** 每次提交新修改时，在 §3 修订时间线中添加新条目，
> 并同步更新 §5 版本发布记录和 §6 文件清单。
> 非项目内容统一归档到 §4。
