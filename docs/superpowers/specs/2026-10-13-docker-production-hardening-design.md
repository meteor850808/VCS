# VCS 容器化生产加固 — 设计方案 (C1 + 反代/secrets/发布脚本)

> **状态**: 设计已批准，待实现
> **日期**: 2026-10-13
> **范围**: 将 `空间质检/` (VCS 矢量数据质检微服务) 的既有 Docker 化配置升级为
> 「生产可用 + 反代 + 密钥管理 + 可发布」形态；并补齐容器内 DWG/MDB 的 Linux 适配。
> **决策记录**: 目标环境=内网 Linux 单机 + 离线交付；Nginx=反代+静态托管(80→443)；
> 密钥=混合(.env 非敏感 + compose secrets 敏感三项)。

---

## 1. 背景与现状

仓库**已有**一套 Docker 化配置：多阶段 `Dockerfile`（base/api/worker/flower）、
`docker-compose.yml`（postgres/redis/migrate/api/worker×2/flower/beat）、`.dockerignore`、
CI `smoke` job。本次不是「从零容器化」，而是**修复既有缺陷 + 生产加固 + 平台适配**。

### 1.1 已发现的缺陷（本次必须修）

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| D1 | 🔴 | 数据卷路径不匹配：config 默认 `./data/uploads`（=/app/data/uploads），卷挂在 `/data/uploads`；compose 未设 `UPLOAD_DIR/OUTPUT_DIR/QUALITY_REPORT_DIR` → 上传/输出/报告落容器临时层，重建即丢；`quality_reports` 无卷 | `config.py:69-71` / `docker-compose.yml:16-18` |
| D2 | 🔴 | Flower 启动失败：exec 形式 `CMD` 不展开 `$FLOWER_BASIC_AUTH`；`--broker_api` 在 Flower 2.x 已移除 | `Dockerfile:56-58` |
| D3 | 🟠 | `beat` 服务命令 `-A app.workers.celery_app` 找不到 Celery 实例（应 `.celery_app.celery_app`） | `docker-compose.yml:131` |
| D4 | 🟠 | Linux 镜像丢失 DWG/MDB 能力（详见 §4） | 见 §4 |
| D5 | 🟡 | `.dockerignore` 漏排 `data/ Redis8/ deploy/ docs/` 等 | `.dockerignore` |
| D6 | 🟡 | compose `version: "3.9"` 已废弃；`deploy.replicas` 非 swarm 下被忽略 | `docker-compose.yml:1,103` |
| D7 | 🟡 | `pkg` 环境沿用浮动基础镜像 tag；无镜像体积约束 | `Dockerfile:2` |

### 1.2 关键前置约束

- **LibreDWG**：Debian bookworm **无** `libredwg-utils` 包（Debian 至 2026-03 仍为 ITP）→ 必须**从源码构建**或 vendored 二进制。
- **MDB 读**：bookworm 有 `mdbtools` + `odbc-mdbtools`（提供 `libmdbodbc.so` 与 `[MDBTools]` 驱动）→ Linux 可读。
- **MDB 写**：依赖 ADOX/ACE OLEDB + `pywin32`（Windows 专属）→ Linux **不可行**，需明确报错。
- 代码中 3 处硬编码 Windows Access 驱动名（`mdb_processor.py:46`、`esri_shapebin.py:249`、`export_service.py:594/635`），必须改为平台感知。

---

## 2. 目标与非目标

### 2.1 目标
1. 修复 §1.1 全部缺陷，容器可稳定构建、启动、持久化。
2. 引入 **Nginx 反代 + 静态托管**，对外 80→443（自签 TLS），应用端口不直接暴露。
3. **密钥最小化**：敏感项走 compose secrets + entrypoint 派生，非敏感项走 `.env`。
4. 容器内**功能对齐**：DWG→DXF（LibreDWG 源码构建）、MDB **读**（mdbtools ODBC）可用；MDB **写**给出可读错误。
5. 生产加固：只读根文件系统、资源限额、日志轮转、非 root（已有）、健康检查。
6. 一键发布脚本（Makefile）+ 文档同步（README/BASELINE/revision）。

### 2.2 非目标
- 不引入 Kubernetes/Swarm（单机 compose）。
- 不做公网 ACME/Let's Encrypt（内网自签）。
- 不实现 Linux 上的 MDB **写**（技术上不可行）。
- 不改动业务逻辑与 API 契约（除平台适配必需的驱动名字符串）。

---

## 3. 架构

```
                内网客户端
                    │ :80/:443
                    ▼
            ┌───────────────┐   自签 TLS(挂载), 80→443 跳转
            │     nginx     │   /app/* 静态(frontend, 只读挂载)
            │ alpine,pinned │   /api /ws /docs /health /openapi → api:8000
            └───────┬───────┘
                    │ vcs_net (internal)
   ┌────────────────┼───────────────┬───────────────┐
   ▼                ▼               ▼               ▼
┌──────┐      ┌─────────┐     ┌─────────┐     ┌────────┐
│ api  │      │postgres │     │ redis   │     │flower* │  *默认不对外
│uvicorn│◄────►│ postgis │     └────┬────┘     └────────┘
└──┬───┘      └─────────┘          │
   │        ┌──────────┐  ┌────────┴──────┐
   └───────►│ worker×N │  │ beat          │
            └──────────┘  └───────────────┘
        migrate（一次性 job，跑完退出）
```

- 对外仅 **80/443**；`postgres/redis/api/flower` 不映射宿主端口（生产）。
- 网络 `vcs_net`（bridge）。
- 数据卷：`/data/{uploads,outputs,quality_reports,previews,logs}` + `pg_data` + `redis_data`。
- **Flower 默认内网不对外**（可选经 nginx `/flower/` 暴露，自带 basic-auth）。

### 3.1 编排选型

采用 **base `docker-compose.yml` + `docker-compose.prod.yml` 覆盖层**：
- 开发/CI 用 base（含宿主端口，便于调试）。
- 生产用 `-f docker-compose.yml -f docker-compose.prod.yml`（去宿主端口、加 secrets/limits/read_only/nginx）。

（否决：独立全量 prod 文件=双份漂移；compose profiles=对环境切换表达力弱。）

---

## 4. 应用代码改动（Linux 适配）

### 4.1 新增 `app/services/_odbc.py`
```python
ACCESS_DRIVER_WIN = "Microsoft Access Driver (*.mdb, *.accdb)"
ACCESS_DRIVER_POSIX = "MDBTools"

def access_driver() -> str:  # 按 sys.platform 返回
def access_conn_str(path: str) -> str:  # DRIVER={..};DBQ=<path>
```

### 4.2 替换硬编码驱动名
- `mdb_processor.py:46`、`esri_shapebin.py:249`、`export_service.py:594/635` → 使用 `access_conn_str()`。

### 4.3 MDB 导出守卫（`export_service.py`）
- ADOX 建库（`win32com`）在非 Windows 时抛**可读错误**：`"MDB 导出仅支持 Windows（ADOX/ACE OLEDB）；当前平台无该能力"`，而非仅 ImportError。
- 保留 Windows 现有行为不变。

### 4.4 DWG 工具定位（`dwg_converter.py`）
- 现状：`_DWG2DXF = tools/libredwg/dwg2dxf.exe`（硬编码 Windows）。
- 改为平台感知：
  - Windows：`tools/libredwg/dwg2dxf.exe`；
  - POSIX：`shutil.which("dwg2dxf")` 或 `/usr/local/bin/dwg2dxf`。
- `is_available()` 同步；工具缺失时保持既有「优雅降级为空」。

### 4.5 测试
- 新增平台适配单测：monkeypatch `sys.platform` / `shutil.which`，覆盖 `access_driver()`/`access_conn_str()`/`_resolve_dwg2dxf()`。
- **约束**：现有 1778 用例全绿；Windows 行为零回归。

---

## 5. Dockerfile 改动

- 基础镜像 `python:3.11-slim-bookworm`（固定 tag，不用浮动）。
- `ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1`。
- **新增 LibreDWG 构建阶段** `libredwg-builder`：
  - `apt-get install -y --no-install-recommends build-essential autoconf automake libtool bison flex libiconv-dev pkg-config`；
  - 源码优先取 `docker/vendor/libredwg-*.tar.gz`（离线兜底），否则下载 release；
  - `./configure --disable-man && make && make install` → 产出 `dwg2dxf`；拷入运行时 `/usr/local/bin/`。
- 运行时 apt（base）：`gdal-bin libgeos-c1v5 libproj25 libpq5 mdbtools odbc-mdbtools unixodbc curl`；
  构建依赖（gcc/g++/libgdal-dev）在 `pip install` 后移除。
- 写 `/etc/odbcinst.ini`：
  ```
  [MDBTools]
  Description = MDBTools Driver
  Driver = libmdbodbc.so
  Setup = libmdbodbc.so
  FileUsage = 1
  UsageCount = 1
  ```
- `COPY docker/entrypoint.sh /app/docker/entrypoint.sh`（chmod +x）；
  `ENTRYPOINT ["/app/docker/entrypoint.sh"]`，各 stage 设 `CMD`。
- **Flower CMD 修复**：改 shell 形式以展开变量，删 `--broker_api`：
  `CMD ["sh","-c","celery -A app.workers.celery_app.celery_app flower --port=5555 --basic-auth=${FLOWER_BASIC_AUTH}"]`
- api stage 增 `HEALTHCHECK`（curl `/health`）。

---

## 6. docker-compose

### 6.1 基线 `docker-compose.yml`（开发）
- 删除 `version: "3.9"`。
- 修 `beat` 命令：`-A app.workers.celery_app.celery_app`。
- 环境变量补：`UPLOAD_DIR=/data/uploads`、`OUTPUT_DIR=/data/outputs`、
  `QUALITY_REPORT_DIR=/data/quality_reports`、`LOG_FILE=/data/logs/vcs.log`。
  - **【self-review 修正】Settings 为 `extra_forbidden` 严格模式，只能设置「已存在字段」对应的 env。**
    `PREVIEW_DIR` **无对应字段** → **不得设置**（否则容器启动即崩）。
    CAD 预览路径由代码派生：`Path(settings.upload_dir).parent / "previews"`
    （`cad_cache.py:141`、`cad/_common.py:55`）→ 当 `UPLOAD_DIR=/data/uploads` 时预览自动落 `/data/previews`。
    因此预览卷挂 `/data/previews` 即可，无需新 env。
  - **`DATABASE_URL` 不再由 compose 硬编码**：改为设置 `POSTGRES_USER/POSTGRES_DB/POSTGRES_HOST`
    （开发态 `POSTGRES_PASSWORD` 来自 `.env`），由 `entrypoint.sh` 统一派生
    `DATABASE_URL`/`DATABASE_URL_SYNC`（生产态密码来自 secret）——单一真源、dev/prod 一致。
- 卷补：`vector_reports`(→/data/quality_reports)、`vector_previews`(→/data/previews)、`vector_logs`(→/data/logs)。
- postgres/redis 宿主端口绑 `127.0.0.1`。

### 6.2 生产覆盖 `docker-compose.prod.yml`
- `api/worker/flower/beat`：`read_only: true` + `tmpfs: [/tmp, /app/.cache]`、`cap_drop:[ALL]`、
  `security_opt:[no-new-privileges:true]`、`deploy.resources.limits`(cpus/memory)、
  日志 `logging: json-file {max-size, max-file}`。
- **移除 api 宿主端口映射**（流量走 nginx）。⚠️ Compose 对 `ports` 是 **拼接**语义，覆盖层里
  `ports: []` 无效，须用 `ports: !reset []`（compose-spec ≥ 2.24）。
- `postgres`：`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password` 且
  `POSTGRES_PASSWORD: !reset null`（官方镜像 `file_env` 二选一，二者并存会报错退出）；无宿主端口。
- 新增 `nginx` 服务：镜像 `nginx:1.27-alpine`（固定）；挂载 `docker/nginx/nginx.conf`、
  `docker/nginx/conf.d/vcs.conf`、`certs/`(只读)、`app/frontend`(只读)；
  `ports: ["80:80","443:443"]`；`depends_on: api`。
- 顶层 `secrets:`：`admin_init_key` / `postgres_password` / `flower_basic_auth`（`file:` 指向 `./secrets/*`）。

---

## 7. 密钥管理 + entrypoint

- `secrets/`（gitignore）存放：`admin_init_key`、`postgres_password`、`flower_basic_auth`。
- `.env` 仅非敏感：`POSTGRES_USER/DB/HOST`、目录、池大小、`CORS_ORIGINS`、`MAX_*` 等。
- `docker/entrypoint.sh`：
  1. 若 `/run/secrets/<f>` 存在，读取并 `export` 到 `ADMIN_INIT_KEY`/`POSTGRES_PASSWORD`/`FLOWER_BASIC_AUTH`；
  2. 容器内（`POSTGRES_HOST` 存在）时**始终**由 `POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_HOST/POSTGRES_DB`
     **派生** `DATABASE_URL`(asyncpg) 与 `DATABASE_URL_SYNC`（覆盖 `env_file: .env` 注入的 localhost 连接串），
     单一真源，避免密码重复；
  3. `exec "$@"`。

---

## 8. Nginx 配置

- `docker/nginx/nginx.conf`：`worker_processes auto`；`gzip on`（`text/* application/json ... image/svg+xml`）；`include /etc/nginx/conf.d/*.conf`。
- `docker/nginx/conf.d/vcs.conf`：
  - `server :80 → return 301 https://$host$request_uri`
  - `server :443 ssl http2`：`ssl_certificate /etc/nginx/certs/server.crt`、`ssl_certificate_key .../server.key`。
  - 静态：`location /app/ { alias /usr/share/nginx/html/; try_files $uri $uri/ /app/index.html; }`；
    `location ~* ^/app/(vendor|.*\.(js|css|svg|png|woff2?))$ { expires 30d; }`。
  - 反代：`location / { proxy_pass http://api:8000; proxy_set_header Host/... }`（覆盖 `/api /docs /redoc /health /metrics /openapi.json`）。
  - WebSocket：`location /ws { proxy_pass http://api:8000; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }`。
  - `client_max_body_size 512m`（上传上限 500MB）；`proxy_read_timeout/proxy_send_timeout` 放大（长任务）。
  - 安全头：`Strict-Transport-Security`、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`。

---

## 9. 证书与发布脚本

- `docker/scripts/gen_certs.sh`：
  `openssl req -x509 -newkey rsa:4096 -days 3650 -nodes -addext "subjectAltName=IP:<host-ip>,DNS:vcs.local" -subj "/CN=vcs.local"` → `certs/server.{crt,key}`。
- `docker/scripts/gen_secrets.sh`：`openssl rand -base64 32` 生成三文件到 `secrets/`，权限 600。
- `Makefile` 目标：`certs`、`secrets`、`build`、`up`、`prod`、`down`、`logs`、`ps`、`config`。

---

## 10. .dockerignore / .env

- `.dockerignore` 补排：`data/`、`Redis8/`、`deploy/`、`docs/`、`tools/`、`.venv/`、`secrets/`、`certs/`、`*.log`、`.git`。
- `.env.example` 补目录变量；新增 `.env.prod.example`（敏感项留空并注释）。

---

## 11. 文档与 CI

- `README.md`：重写 Docker 段（dev/prod 命令、证书生成、secrets、排障）。
- `BASELINE.md` §12：更新架构图/服务表/卷/secrets/nginx。
- 新增 `docs/revision_vX_Y.md` 修订记录。
- `.github/workflows/ci.yml`：新增 `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` 语法校验步骤（不真起 prod）。

---

## 12. 验证计划

1. `docker compose ... config` 通过；`gen_secrets.sh`/`gen_certs.sh` 产出正确。
2. `prod up -d --build --wait` 全绿；`curl -k https://localhost/health` = `ok`；`http://` 301→https。
3. 前端 `/app/` 经 nginx 静态加载；`/ws` 握手成功；**>500MB 上传**被 nginx 按 `client_max_body_size` 正确放行/拦截（500MB 上限）。
4. **重启容器后**上传文件/质检报告/CAD 预览仍在（验证 D1 卷修复；预览落 `/data/previews`，由 `UPLOAD_DIR` 派生）。
5. `read_only` 下服务正常运行（含日志轮转到卷、`__pycache__` 关闭）。
6. 容器内 `dwg2dxf` 可用（DWG→DXF 走通）；MDB **读**走通；MDB **写**返回「仅 Windows」可读错误。
7. 容器启动无 `extra_forbidden` 报错（未引入 Settings 中不存在的 env 变量）。
8. 回归：`pytest` 全绿（1778 例）+ 新增平台适配用例通过；Windows 行为零回归。

---

## 13. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LibreDWG 源码构建版本/依赖问题 | 固定版本 tarball；vendor 到 `docker/vendor/` 支持离线；构建阶段独立便于排障 |
| `read_only` 触发未知写路径 | 已识别写路径（/data、日志、/tmp）并挂卷/tmpfs；PYTHONDONTWRITEBYTECODE 关闭 pycache；未识别路径以运行验证暴露 |
| mdbtools ODBC 对 .mdb 兼容性不完整（几何/编码） | 属已知第三方局限；读取失败按既有降级/报错路径处理 |
| 自签证书客户端告警 | 内网预期行为；文档提供信任导入说明 |
| secrets 走 compose file secrets 需 entrypoint | entrypoint 逻辑简单、可单测；dev 缺 secret 时回退 `env_file` |

---

## 14. 交付物清单

见 §2 文件清单（Dockerfile / compose×2 / entrypoint / nginx / scripts / Makefile /
.env(.prod).example / .dockerignore / `_odbc.py` + 3 服务改动 / dwg_converter / 测试 / 文档）。
