# VCS 优化实施记录

> 本文件记录项目所有优化实施的完整追溯，从 Phase 1 至今。
> 每次提交新优化时，在此文件中添加新条目。

---

## Phase 15 — 使用问题批量修复 + 一键重启修复（2026-08-04）

**来源：** ERROR.txt 使用问题清单（8 模块 30+ 项）+ restart.bat 卡住反馈

### 管理 API 增强（v2.7）

| 编号 | 优化项 | 文件 | 问题 | 方案 |
|------|--------|------|------|------|
| K1 | 密钥重新启用 | `app/api/v1/admin.py` | 已吊销 Key 只能永久删除，无法恢复 | 新增 `PUT /admin/apikeys/{id}/activate`（`is_active=True`） |
| K2 | 密钥重新生成 | `app/api/v1/admin.py` | Key 明文遗失无法重新获取（只能新建） | 新增 `POST /admin/apikeys/{id}/regenerate`（重算哈希，旧钥立即失效，新明文仅展示一次） |

### 前端通用修复（`app/frontend/index.html`，v2.7）

| 编号 | 优化项 | 问题 | 方案 |
|------|--------|------|------|
| F1 | `esc()` 单引号转义 | `onclick="f('${s}')"` 含单引号时属性断裂 → 按钮无效 | 补 `'` → `&#39;` 转义 |
| F2 | fetch 无超时 | 挂起请求永久卡死 UI | `_t()` 统一 30s AbortController + GET 非 JSON 兜底 |
| F3 | 下载文件名解析 | `filename*=UTF-8''` 整段被当文件名 → 中文名下载挂起 | 优先 RFC 5987 `filename*` 解析 |
| F4 | adminInitKey 不持久化 | 刷新页面管理员密钥丢失 | localStorage（`vcs_admin_key`）读写 |
| F5 | qcTopoState 未初始化 | t09/t10/t11（设计 on:false）误显示勾选 | `TOPO_RULES.forEach` 按 `r.on !== false` 初始化 |
| F6 | 拖拽全局拦截缺失 | 拖文件到非上传区浏览器直接打开文件 | `window` dragover/drop preventDefault |
| F7 | 压缩包空壳记录 | 解压后原压缩包记录仍显示在列表 | `loadFiles` 过滤 `is_archive` |
| F8 | 字典不可维护 | 只能建不能改名/删除/编辑条目 | 列表 ✏️重命名/🗑️删除 + 条目 ✏️编辑/🗑️删除 |
| F9 | 层组父级手输 UUID | prompt 手输易错 | 弹层 `<select>` 下拉 + `_flatLayers` 递归缩进 |
| F10 | showGroupModal 竞态 | 选项未加载完可点保存 | 加载完成前禁用保存按钮 |
| F11 | 自检页挂起 | runAllTests 串行 await，单请求挂死整页 | apiCall 15s 超时自动跳过 |

### 后端功能与健壮性（v2.7）

| 编号 | 优化项 | 文件 | 问题 | 方案 |
|------|--------|------|------|------|
| B1 | 空要素质检 | `app/api/v1/quality.py`, `quality_checker.py` | 零要素文件执行质检报错/评分异常 | 入口 400 + Phase 0.5 守卫 + `_calculate_score_legacy` total==0 改 0.0 |
| B2 | 报告 total_features 错误 | `quality.py` | 错用 `attr_summary.total` | 改取 DB `StoredFile.feature_count` |
| B3 | 字典重名 500 | `data_standard_v2.py` | 重名 IntegrityError 未捕获 | 预查重 + 409「字典名称已存在」 |
| B4 | scope 无校验 | `data_standard_v2.py` | 任意 scope 值可入库 | None/standard/global 枚举校验 → 400 |
| B5 | Excel 导入仅旧扁平结构 | `data_standard_parser.py` | 4-sheet 模板（标准/层组/数据表/字段）无法导入 | `parse_standard_excel_v2` + `_import_from_v2` 完整建层级 + 旧结构回退 + .xls 提示 |
| B6 | /health 挂起 | `app/main.py` | PG 不可达时默认 60s 拖垮健康检查 | PG/Redis ping 各 2s 限时，超时返回 degraded |
| B7 | WS 连接永不断开 | `app/api/v1/ws.py` | Redis 不可用时死循环 sleep(600) | 按 `WebSocketState.CONNECTED` 轮询存活 |
| B8 | 下载中文文件名失败 | `app/api/v1/data.py` | `filename="{中文}"` latin-1 编码失败 | RFC 5987 `filename*=UTF-8''{quote()}` |

### 一键重启修复（restart.bat / scripts/restart.bat）

| 问题 | 根因 | 方案 |
|------|------|------|
| bat 每次跑都卡住、窗口关不掉 | `start /b uvicorn` 共享控制台 + 结尾 `pause` + curl 无超时 | `Start-Process` 隐藏窗口 + 日志重定向 + 成功 `exit /b 0` 自动关窗（失败保留 pause）+ `curl -m 5` |

---

## Phase 16 — v2.8 数据链路稳定性 + 数据标准闭环 + 运营部署（2026-08-04 至 2026-08-10）

**来源：** 架构决策 2026-08-04（RQ-038）——完整修订细节见 `VCS_项目修订全纪录.md` Phase 16 与 `docs/revision_v2_8.md`，要点：

- S1 PostGIS 入库稳定性（子任务独立会话 + 分批 flush + make_valid 几何修复 + 非法几何置 NULL）
- S2 CRS 元数据链路（去 SRID typmod + 源 SRID 入库 + ST_Transform 预览/转换 + always_xy 统一）
- S3 Multipatch / OBJ 3D 降维入库
- S4 数据-标准-质检联动（standard_table_id 全链透传 + 单表检查模式 A1 语义修正）
- S5 质检报告可视化（五维 SVG 仪表 + 违规明细 + 严重度分布）
- S6 Excel 模板补字典 Sheet + 导入导出对齐
- S7 标准回传更新（code 匹配 update/upsert + 版本快照）
- S8 版本对比明细增强（_deep_diff 自然键对齐 + 可读路径 + 中文摘要）
- S9 层组多分类 + 移动/复制修复（standard_layer_group_tables 关联表）
- S10 任务看板（积分算法 + 状态筛选 + 卡片操作）
- S11 批量操作（批量删除/合并存活性检查 + 一键重检）
- S12 部署方案文档 + 容器编译装订 + 端口对齐
- v2.10 空间分表 LIST 分区化（一个数据源一个表）+ 流式全量落库
- v2.10.1 GDB 上传要素为 0 静默失败修复

---

## Phase 17 — v2.9 数据字典展示拆分（2026-08-12）

**来源：** 用户反馈（RQ-039）——「数据字典展示需要拆分 全局字典 或者对应数据标准字典 不要跟表做关联」

### A 级（功能正确性）

| 编号 | 模块 | 优化项 | 文件 | 问题 | 方案 |
|------|------|--------|------|------|------|
| A1 | 数据标准-字典 | 后端维度拆分 | `app/api/v1/data_standard_v2.py` | 标准端点与全局端点边界不清：标准端点可建 global 字典、全局列表会混入/漏掉数据 | 标准端点强制 `scope='standard'`（拒 global 400）；新增 `POST /dictionaries` 全局创建端点（`standard_id=NULL`、应用层重名预查）；`GET /dictionaries` 只返回 `scope='global' AND standard_id IS NULL`；update/share 保证 scope 一致性（转 global 清 standard_id、转 standard 无归属 400） |
| A2 | 前端 | 字典面板双 Tab | `app/frontend/index.html` | 字典展示混杂且依赖选中表/字段上下文 | 右栏面板拆「🌐 全局字典」（默认）/「📋 标准字典」双 tab，`v2LoadDicts`/`v2AddDict` 按 tab 分流；全局维度不依赖选中标准；标准维度仅依赖 `V2.stdId`（未选标准提示）；删除标准后刷新字典面板 |
| A3 | 数据库 | 隐身脏数据清理 | DB 数据清理 | 2 条 `scope='global'` 且 `standard_id` 非空的历史字典两维度列表均不可见 | 清理 32 个测试标准及全部关联数据（86 sheets 字段/7 sheets/6 字典/9 版本/54 标准字段/33 标准表/39 层组/1 孤儿质检规则）；外围 SET NULL 保留 1 源表/5 存储文件/109 质检报告 |

**验收：** 后端集成测试 8/8（`test_dict_split.py`）+ Playwright 冒烟/端到端（0 JS 错误、维度隔离、badge 共享/标准）+ `py_compile` / `node --check` 通过。详细用例见 `docs/test_manual_v2_9.md`，修订说明见 `docs/revision_v2_9.md`。

---

## Phase 7 — 架构加固与安全增强（2026-06-16）

### S 级（生产环境必修）

| 编号 | 模块 | 优化项 | 文件 | 问题 | 方案 |
|------|------|--------|------|------|------|
| S1 | 数据库 | 异步引擎连接池配置 | `app/db/session.py` | 无 pool_size/pool_recycle，负载下连接耗尽 | 添加 `DB_POOL_SIZE=10`、`DB_MAX_OVERFLOW=20`、`DB_POOL_RECYCLE=1800`、`DB_POOL_PRE_PING=true`，SQLite 自动降级 |
| S2 | Celery | 结果过期 + 崩溃重试 | `app/workers/celery_app.py` | 结果永不过期→Redis OOM；Worker 崩溃丢失任务 | `result_expires=3600`、`task_acks_late=True`、`task_reject_on_worker_lost=True` |
| S3 | 部署 | Celery Beat 未启动 | `docker-compose.yml` + `celery_app.py` | 清理/指标快照定时任务从未执行 | 导入 `beat_schedule` 配置；docker-compose 新增 `beat` 服务 |
| S4 | API | CORS 生产阻断 | `app/main.py` | `allow_origins=[]` 在生产中导致所有跨域请求失败 | 改为 `settings.cors_origins`（可配置白名单） |
| S5 | 安全 | 全局请求体大小限制 | `app/core/security_middleware.py` | JSON端点无请求体上限，HTTP洪水攻击向量 | 在 SecurityHeadersMiddleware 中按 Content-Length 拦截超限请求 |

### A 级（性能与架构）

| 编号 | 模块 | 优化项 | 文件 | 问题 | 方案 |
|------|------|--------|------|------|------|
| A1 | 数据库 | 消除 N+1 查询 | `app/core/db_utils.py` + `app/models/data_standard.py` | 查 Sheet 再循环查 Field 多次 DB 往返 | `selectinload()` 一次性 JOIN，添加 `ForeignKey` + `relationship` |
| A2 | 缓存 | Redis 客户端单例 | _(设计限制，暂缓)_ | `cache.py` 自建连接池 vs `main.py` 连接池 | 当前架构下合理，双池复用同一 URL 开销可忽略 |
| A3 | 监控 | 健康检查缓存 | `app/main.py` | 负载均衡器高频探测（3-5s）→ 每次查 PG+Redis | 内存缓存 10 秒（`_health_cache` 时间戳） |
| A4 | 鉴权 | API Key Redis 缓存 | `app/core/deps.py` | 每个请求 `SELECT ... FROM api_keys WHERE key_hash = ?` | Redis 缓存 `apikey:{hash}` TTL 60s，缓存穿透时回源 DB |
| A5 | 前端 | SPA 路由回退 | _(Hash 路由，无需)_ | 前端使用 hash 路由 `#data-ops`，非 path 路由 | 无需处理 |

### B 级（安全深度防御）

| 编号 | 模块 | 优化项 | 文件 | 问题 | 方案 |
|------|------|--------|------|------|------|
| B1 | 部署 | Dockerfile non-root | `Dockerfile` | 容器以 root 运行，逃逸风险 | `RUN adduser appuser && USER appuser` |
| B2 | 部署 | Flower 认证 | `docker-compose.yml` + `Dockerfile` | Celery 监控面板无密码 | `--basic-auth=$FLOWER_BASIC_AUTH`、环境变量默认值 |
| B3 | 安全 | 安全响应头 | `app/core/security_middleware.py` | 缺少 `X-Content-Type-Options`、`X-Frame-Options`、`HSTS` | 中间件自动注入 |
| B4 | 限流 | 代理 IP 识别 | `app/core/rate_limit.py` | `get_remote_address` 在有代理时恒为 127.0.0.1 | `_get_client_ip()` 解析 `X-Forwarded-For` 或 `X-Real-IP` |

### C 级（代码质量）

| 编号 | 模块 | 优化项 | 文件 | 问题 | 方案 |
|------|------|--------|------|------|------|
| C1 | 配置 | 字段名统一 | `app/config.py` | 驼峰 `MAX_FEATURES_PER_UPLOAD` 破坏一致性 | 改为 `max_features_per_upload` |
| C2 | 代码 | 内联 import 标准化 | `app/core/deps.py`、`app/main.py` | `hash_api_key`、`import os` 在函数体内 | 移到文件顶部 |
| C3 | 清理 | 死代码删除 | `app/db/session.py` | `_get_db_sync()` 从未被调用 | 删除（Celery tasks.py 自建引擎） |
| C4 | 测试 | Celery 同步执行 | `tests/test_api.py` | `monkeypatch` 绕过全路径 | 改用 `CELERY_TASK_ALWAYS_EAGER=true`、完整模型 import |
| C5 | 前端 | CDN SRI 完整性 | `app/frontend/index.html` | Leaflet CSS 从 CDN 加载无 `integrity` | 添加 `sha384-sHL9NAb...` + `crossorigin="anonymous"` |
| C6 | 前端 | CSP 内容安全策略 | `app/frontend/index.html` | 无 `Content-Security-Policy` | 添加 CSP meta 标签（限制 script/style/img/connect 来源） |

### 配置文件更新

| 文件 | 变更 |
|------|------|
| `.env.example` | 新增 `DB_POOL_*`、`CORS_ORIGINS` 配置项，添加 Docker/本地注释 |
| `requirements.txt` | 新增 `pydantic-settings`、`geoalchemy2`（之前缺失导致启动崩溃） |

---

## Phase 4-6 — 外部 AI 优化采纳与 BUG 修复（2026-06-11 至 2026-06-15）

### 关键故障修复

| 编号 | 问题 | 文件 | 症状 | 修复 |
|------|------|------|------|------|
| F1 | `requirements.txt` 缺少依赖 | `requirements.txt` | 缺少 `pydantic-settings`、`geoalchemy2` → 启动崩溃 | 添加两行依赖 |
| F2 | 未定义变量引用 | `app/services/audit_logger.py:86` | `SQLALCHEMY_MODELS_BASE` 未定义 | 改为 `from app.db.session import Base` |
| F3 | 表不存在 | `app/services/spatial_query.py` | SQL 中 `spatial_features` 表不存在（已拆分三表） | 全部替换为跨三表 UNION ALL |
| F4 | Alembic 缺少模型 | `alembic/env.py` | 未 import `stored_file`/`data_standard`/`spatial_feature` | 补充 import |
| F5 | 迁移引用无效表和列 | `alembic/versions/0002_performance_indexes.py` | `spatial_features` 表 + `stored_files.file_hash` 列不存在 | 改为三表 GIST 索引 |
| F6 | 导入不存在的模型 | `scripts/init_db.py:29` | `from spatial_feature import SpatialFeature` | 改为 `PointFeature, LineFeature, PolygonFeature` |
| F7 | 去重集合混用 | `app/services/quality_checker.py` | 类型冲突和值域违规共用一个去重 set | 拆为独立 set |
| F8 | AuditLog 独立元数据 | `app/services/audit_logger.py:12` | 使用独立 `DeclarativeBase`，`audit_logs` 表不被创建 | 改为继承自 `Base` |

### 外部 AI 优化采纳（来自空间API服务1）

| 模块 | 变更内容 | 影响 |
|------|----------|------|
| **Schemas 拆分** | `conversion.py` → 9 个领域文件 | 接口隔离，避免单文件膨胀 |
| **质检子包** | `services/quality/` (models.py + reporter.py) | QualityResult/Issue/CleanConfig 数据类，HTML/JSON 报告分离 |
| **FeatureStore** | `services/feature_store.py` | 跨三表 (point/line/polygon) 统一 CRUD |
| **GDB Processor** | `services/gdb_processor.py` | ESRI GDB → GeoJSON 自动转换 |
| **文件名验证** | `core/file_utils.py` | 路径遍历 + 危险扩展名检查 |
| **数据标准加载** | `core/db_utils.py` | 统一查询接口 |
| **Pydantic v2** | `config.py` | `@dataclass` → `pydantic-settings.BaseSettings` |
| **常量抽取** | `core/constants.py` | `OUTPUT_SUFFIX_MAP`、`DANGEROUS_EXTENSIONS` |

---

## Phase 3 — 前端重构（2026-05-22 至 2026-06-11）

### 前端故障修复

| 编号 | 问题 | 修复 |
|------|------|------|
| U1 | test.html API Key 删除列表不显示 | 新增 `refreshAdminKeyList()` 函数 |
| U2 | test.html `apiCall` body stream 重复读 | `resp.json()` + catch `.text()` → 单次 `resp.text()` + `JSON.parse` |
| U3 | 前端页面白屏（CSS `@import`） | 改为 `<link>` 标签加载 Leaflet CSS |
| U4 | 前端页面白屏（默认 page 未激活） | `document.getElementById('page-data-ops').classList.add('active')` |
| U5 | 前端 JS 语法错误（缺少闭合括号） | 修复 `deleteStandard` 函数 |

### 一体化工作台重构

- 5 页 → 6 页 SPA + 右侧抽屉详情面板
- Leaflet 死代码清理（334 行）
- 批量上传旧系统清理（100 行）
- 侧边栏图标导航 + 详情面板 5 个 Tab（属性/地图/质检/转换/修复）
- CSS 变量暗色主题统一

---

## Phase 1-2 — 基础服务搭建（2026-05-07 至 2026-05-22）

### 核心功能实现

| 功能 | 版本 | 说明 |
|------|------|------|
| 格式转换 | 1.0.0 | SHP/GeoJSON/KML/DXF 互转 + CRS 变换 |
| 批量转换 | 1.0.0 | 最多 50 文件批量提交 |
| 质量检查 | 1.0.0 | 属性完整性、关键字段验证 |
| WebSocket 推送 | 1.0.0 | Redis Pub/Sub 实时进度 |
| Celery Worker | 1.0.0 | 异步任务 + 沙箱预执行 |
| API Key 管理 | 1.0.0 | SHA-256 生成 + 软删除 |
| 前端 SPA | 1.0.0 | Vanilla JS + Leaflet 地图 |
| Docker Compose | 1.0.0 | PG/Redis/API/Worker 四容器 |
| 测试套件 | 1.0.0 | 38 用例（集成+安全+服务层） |

---

## 附录：优化检查清单

每次提交优化时，对照以下清单：

- [ ] 在 `VCS_优化实施记录.md` 添加条目
- [ ] 标注等级（S/A/B/C）
- [ ] 说明：文件、问题、方案
- [ ] 运行 `python -c "import ast, os..."` 语法检查
- [ ] 更新 `.env.example` 如有配置变更
