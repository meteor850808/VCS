# VCS 空间API服务 — 项目总览

## 版本 2.9.1

矢量数据格式转换、坐标系变换、质量检查与空间数据存储一体化微服务。

### 技术栈
- **框架**: FastAPI + SQLAlchemy 2.0 async + PostgreSQL/PostGIS
- **任务队列**: Celery 5 + Redis 3+（RESP2 兼容，Pub/Sub 进度推送）
- **GIS 核心**: Fiona / Shapely / pyproj / geoalchemy2 / ogr2ogr
- **前端**: 单文件 HTML SPA (Vanilla JS + Leaflet 1.9)
- **监控**: Prometheus / structlog / slowapi

### 核心能力
| 模块 | 数量 | 说明 |
|------|------|------|
| REST 端点 | 47+ | 10 个路由模块（含 Group CRUD / 密钥启用重生成 / 全局字典），覆盖全部业务功能 |
| WebSocket | 1 | 实时任务进度推送（Redis Pub/Sub） |
| 服务模块 | 18+ | 质检/规则引擎/转换/修复/空间/存储/同步/版本控制 |
| 数据库表 | 15+ | 含 PostGIS 空间分表 + 质检规则/违规/快照表 + key_groups |
| 测试用例 | 38 | 集成 + 安全 + 服务层全覆盖 |

### 项目结构
- `app/` — 主应用（FastAPI 入口 + API 路由 + 服务 + 模型 + 前端）
- `alembic/` — 数据库迁移（4 个版本）
- `scripts/` — 初始化脚本 + 一键重启 + 迁移工具
- `tests/` — Python 测试套件
- `.github/workflows/` — CI 流水线

### 关键设计决策
1. **空间要素分表存储**: 按几何类型拆为 point_features / line_features / polygon_features，每表独立 GIST 空间索引，跨表 UNION ALL 查询
2. **API Key 安全**: SHA-256 哈希存储 + O(1) 索引查询 + is_active 软删除 + 永久硬删除
3. **分组权限隔离 (v2.5)**: KeyGroup 分组模型 + AuthContext 认证上下文，同组 Key 自动共享数据访问权限；`ADMIN_INIT_KEY` 可作为 X-API-Key 查看全量数据
4. **密钥生命周期 (v2.7)**: 吊销后可重新启用，遗失可重新生成（重算哈希、旧钥立即失效）
5. **文件名安全**: 白名单扩展名验证 + 路径遍历检查 + 空字节拦截
6. **结构化日志**: structlog JSON 行输出 + trace_id 请求追踪
7. **流式上传**: 1MB 分块读取，边上传边校验大小限制
8. **质检规则引擎**: 从数据标准字段定义自动生成规则，支持 4 种检查类型（NOT_NULL / VALUE_IN_SET / TYPE_MATCH / LENGTH_LIMIT），可扩展
9. **五维评分**: 完整性 + 准确性 + 一致性 + 唯一性 + 及时性，各维度可配置权重
10. **Excel 标准导入 (v2.7)**: 4-sheet 模板（标准/层组/数据表/字段）一次导入建全层级
11. **数据字典双维度 (v2.9)**: 字典展示拆分为「全局字典」（`GET /dictionaries`，不与任何标准/表/字段关联）与「对应数据标准字典」（`GET /{stdId}/dictionaries`，仅依赖选中标准）；不变量——`scope='global'` 的字典 `standard_id` 恒为 NULL、`scope='standard'` 必须归属标准，全局重名保护在应用层实现（DB 唯一约束对 NULL standard_id 不生效）

### 启动方式
```bash
# 一键重启
./restart.sh     # Git Bash
restart.bat      # Windows CMD

# 手动
uvicorn app.main:app --host 127.0.0.1 --port 45679 --reload
```

### 访问地址
| 服务 | 地址 |
|------|------|
| SPA 前端 | http://localhost:45679/app/ |
| API 测试台 | http://localhost:45679/app/test.html |
| Swagger | http://localhost:45679/docs |
| Redoc | http://localhost:45679/redoc |
