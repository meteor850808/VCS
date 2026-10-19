# VCS 空间质检 — Docker 部署·使用·调试操作手册

> 版本：**v2.24.0** ｜ 适用：内网 Linux 单机（有 `sudo`）｜ 语言：中文
> 关联文档：`docs/revision_v2_24_0.md`（Docker 生产加固 + CI 双架构）、
> `docs/superpowers/specs/2026-10-13-docker-production-hardening-design.md`（设计）
> 本手册所有命令/路径/变量均与仓库内 `Dockerfile`、`docker-compose*.yml`、
> `docker/**`、`Makefile`、`.env*.example`、`.github/workflows/docker-publish.yml` 逐字对齐。

---

## 目录

1. [概述与架构](#1-概述与架构)
2. [快速开始](#2-快速开始)
3. [镜像获取（三条路径）](#3-镜像获取三条路径)
4. [配置（env / secrets / 证书 / entrypoint）](#4-配置env--secrets--证书--entrypoint)
5. [三种部署模式](#5-三种部署模式)
6. [日常使用](#6-日常使用)
7. [调试与排障](#7-调试与排障)
8. [数据管理（备份 / 恢复）](#8-数据管理备份--恢复)
9. [升级与回滚](#9-升级与回滚)
10. [安全加固](#10-安全加固)
11. [附录](#11-附录)

---

## 1. 概述与架构

### 1.1 镜像清单

一次发布产出 **5 个镜像**（同一 `Dockerfile` 的 4 个 target + 1 个 Nginx 镜像）：

| 镜像 | 构建来源 | 入口 / 端口 | 说明 |
|------|----------|-------------|------|
| `vcs-api`     | `Dockerfile` target `api`     | `uvicorn app.main:app` · 8000 | FastAPI 主服务 |
| `vcs-worker`  | `Dockerfile` target `worker`  | `celery worker` · —           | 任务处理（队列 `vector_conversion,celery`） |
| `vcs-beat`    | `Dockerfile` target `beat`    | `celery beat` · —             | 定时调度 |
| `vcs-flower`  | `Dockerfile` target `flower`  | `celery flower` · 5555        | 任务监控 UI |
| `vcs-nginx`   | `docker/nginx/Dockerfile`     | `nginx` · 80/443              | 反代 + 前端静态 + TLS |

> `Dockerfile` 为多阶段：`libredwg-builder`（源码编译 LibreDWG 0.13.3）→ `base`
> （`python:3.11.11-slim` + GDAL/ODBC/MDBTools）→ `api`/`worker`/`beat`/`flower`。
> 运行用户为非 root 的 `appuser`，入口脚本 `docker/entrypoint.sh`。

### 1.2 服务拓扑

```
                          ┌───────────────────────────┐
   80/443 ──►  nginx  ──►  │  api (FastAPI :8000)      │
  (TLS 自签)  反代/静态      └────────────┬──────────────┘
                              │           │
                     /api /ws │           │ SQL
                              │           ▼
                              │     ┌───────────┐   ┌─────────┐
                              │     │ postgres  │   │  redis  │
                              │     │ 15+PostGIS│   │   7     │
                              │     └───────────┘   └────┬────┘
                              │                          │ broker
                     ┌────────┴───────┬──────────────┬───┴────────┐
                     ▼                ▼              ▼            ▼
                worker(×2)          beat          flower        migrate
                (Celery)          (调度)        (:5555)     (一次性迁移)
```

- **数据流**：请求经 nginx → api；异步任务经 Redis broker → worker；beat 定时投递；迁移由 `migrate` 一次性 Job 完成。
- **前端**：`app/frontend/` 由 nginx 静态托管于 `/app/`。
- **网络**：所有服务在自定义 bridge 网络 `vcs_net` 内以服务名互访。

### 1.3 端口 / 卷 / 网络总表

| 端口 | 基线 compose | 生产（prod / release） |
|------|--------------|------------------------|
| `45679:8000` | api 对外 | **关**（仅经 nginx） |
| `80` / `443` | — | nginx 对外 |
| `5432` | postgres 对外 | **关** |
| `6379` | redis 对外 | **关** |
| `5555` | flower 对外 | **关**（默认） |

| 命名卷 | 挂载点 | 内容 |
|--------|--------|------|
| `pg_data`         | `/var/lib/postgresql/data` | 数据库 |
| `redis_data`      | `/data`（redis） | AOF 持久化 |
| `vector_uploads`  | `/data/uploads` | 上传文件 |
| `vector_outputs`  | `/data/outputs` | 转换产物 |
| `vector_reports`  | `/data/quality_reports` | 质检报告 |
| `vector_logs`     | `/data/logs` | 应用日志 `vcs.log` |

网络：`vcs_net`（bridge）。

### 1.4 主机前置条件

| 组件 | 要求 |
|------|------|
| Docker Engine | ≥ 24（推荐 26+） |
| Docker Compose | **≥ 2.24**（生产覆盖层用到 `ports: !reset`） |
| 架构 | `linux/amd64` 或 `linux/arm64`（镜像均双架构） |
| 资源 | 建议 ≥ 4 vCPU / 8 GB RAM / 40 GB 磁盘 |
| 其他 | `openssl`（生成 secrets/证书）、`bash`/`sh` |

```bash
docker version
docker compose version   # 期望 v2.24+
```

---

## 2. 快速开始

内网**能访问 Docker Hub** 时，5 步起服（生产模式，推荐）：

```bash
# 1) 取编排与配置模板（项目根目录）
cp .env.prod.example .env

# 2) 生成 secrets（postgres_password / admin_init_key / flower_basic_auth）
sh docker/scripts/gen_secrets.sh

# 3) 生成自签 TLS 证书（certs/vcs.crt, certs/vcs.key）
sh docker/scripts/gen_certs.sh

# 4) 起服（基于已发布镜像，不本地构建）——见 §5 模式三
REGISTRY=docker.io NAMESPACE=meteor850808 TAG=2.24.0 \
  docker compose -f docker-compose.release.yml up -d

# 5) 验证
docker compose -f docker-compose.release.yml ps
curl -k https://localhost/health
```

访问：

| 入口 | 地址 |
|------|------|
| 前端 SPA | `https://<主机>/app/`（自签证书，浏览器需信任/放行） |
| Swagger  | `https://<主机>/docs` |
| ReDoc    | `https://<主机>/redoc` |
| 健康检查 | `https://<主机>/health` |

> 只想最快看到效果（非生产）？用**基线模式**（§5 模式一）：
> `cp .env.example .env && docker compose up -d --build`，访问 `http://localhost:45679/app/`。

---

## 3. 镜像获取（三条路径）

镜像的**来源**有两条：① CI 自动构建（双架构），② 本地 `build_push.sh` / `make push`。
镜像的**分发**有三条路径：A 在线拉取、B 离线 `save/load`、C 内网 registry。

### 3.1 镜像从哪来

**① CI（推荐，双架构）** — `.github/workflows/docker-publish.yml`

- 触发：Actions 页 `Run workflow`（可填 tag），或 `git tag v2.24.0 && git push origin v2.24.0`。
- 矩阵：`linux/amd64`（`ubuntu-latest`）× `linux/arm64`（`ubuntu-24.04-arm` 原生）各构建 5 镜像，
  再由 `merge-manifest` 合并为 **multi-arch manifest**。
- 必需仓库 Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`；可选 Variable：`DOCKERHUB_REGISTRY`（默认 `docker.io`）。
- 输出：`<registry>/<namespace>/vcs-{api,worker,beat,flower,nginx}:<tag>`。

**② 本地构建并推送** — `docker/scripts/build_push.sh`（或 `make push`）

```bash
# 登录（推荐 PAT）
echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin

# 方式一：脚本（单架构 = 当前机器架构）
REGISTRY=docker.io NAMESPACE=meteor850808 TAG=2.24.0 sh docker/scripts/build_push.sh

# 方式二：Makefile
make push        NAMESPACE=meteor850808 REGISTRY=docker.io TAG=2.24.0
make push-latest NAMESPACE=meteor850808 REGISTRY=docker.io TAG=2.24.0
```

> 脚本环境变量：`REGISTRY`(默认 docker.io) / `NAMESPACE`(必填) / `TAG`(默认 2.24.0) /
> `PLATFORM`(如 `linux/amd64`) / `LATEST=1`(额外打推 latest) / `SKIP_PUSH=1`(只构建)。
> 多架构本地构建需 `buildx`；**最省事是交给 CI**。

### 3.2 路径 A — 在线拉取（Docker Hub）

```bash
docker pull meteor850808/vcs-api:2.24.0
docker pull meteor850808/vcs-worker:2.24.0
docker pull meteor850808/vcs-beat:2.24.0
docker pull meteor850808/vcs-flower:2.24.0
docker pull meteor850808/vcs-nginx:2.24.0
```

多架构 manifest 会**自动按主机架构**拉取对应层。校验：

```bash
docker buildx imagetools inspect meteor850808/vcs-api:2.24.0
```

### 3.3 路径 B — 离线交付（docker save / load）

用于目标机**完全隔离**、无法访问任何 registry 的场景。

**联网机**（与目标机同架构；跨架构需 `--platform`）：

```bash
# 拉取全部镜像
for t in api worker beat flower nginx; do docker pull meteor850808/vcs-$t:2.24.0; done

# 导出为单包（跨架构场景：先 docker pull --platform linux/amd64 <img> 再 save）
docker save -o vcs-2.24.0-images.tar \
  meteor850808/vcs-api:2.24.0 meteor850808/vcs-worker:2.24.0 \
  meteor850808/vcs-beat:2.24.0 meteor850808/vcs-flower:2.24.0 \
  meteor850808/vcs-nginx:2.24.0

# 同时拷贝项目中的编排与脚本（compose + docker/ + .env.prod.example）
```

**目标机**：

```bash
docker load -i vcs-2.24.0-images.tar
docker images | grep vcs-          # 确认已导入
# 随后按 §5 模式三（docker-compose.release.yml）起服
```

> `docker save` 导出的是**本机已解析架构**的镜像。目标机架构不同时，需在联网机用
> `docker pull --platform linux/arm64 ...` 拉取后再 `save`。

### 3.4 路径 C — 内网 registry（`registry:2` / Harbor）

内网自建仓库（仓库内已附 `docker/registry/`）：

```bash
# 1) 起一个私有 registry（本地 :5000）
docker compose -f docker/registry/docker-compose.yml up -d

# 2)（可选）启用基础认证：生成 htpasswd 后按提示改 compose 的 REGISTRY_AUTH 并挂载 ./auth
sh docker/registry/gen_htpasswd.sh

# 3) 构建并推送到内网 registry
REGISTRY=<repo-host>:5000 NAMESPACE=vcs TAG=2.24.0 sh docker/scripts/build_push.sh
```

目标机 `docker-compose.release.yml` 中设 `REGISTRY=<repo-host>:5000 NAMESPACE=vcs TAG=2.24.0`
即可拉取。若用 **Harbor**，`NAMESPACE` 填 Harbor 的**项目名**，并先 `docker login <repo-host>`。

### 3.5 切换仓库（Docker Hub ↔ Harbor ↔ 内网 registry）

全流程只依赖三个变量，无需改任何文件：

| 变量 | 含义 | 示例 |
|------|------|------|
| `REGISTRY`  | 仓库地址 | `docker.io` / `harbor.intra:443` / `registry.intra:5000` |
| `NAMESPACE` | 命名空间/用户名/项目 | `meteor850808` / `vcs` |
| `TAG`       | 版本标签 | `2.24.0` |

CI 侧对应 Variable `DOCKERHUB_REGISTRY`。

---

## 4. 配置（env / secrets / 证书 / entrypoint）

### 4.1 两级 env

| 文件 | 用途 | 复制自 |
|------|------|--------|
| `.env`（基线/开发） | 端口全开、明文密码默认值 | `.env.example` |
| `.env.prod.example` | 生产模板（敏感项走 secrets） | 生产时 `cp .env.prod.example .env` |

`.env.prod.example` 内容（非敏感项）：

```ini
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=["https://vcs.local"]
MAX_UPLOAD_SIZE_MB=500
POSTGRES_DB=vcs_db
POSTGRES_USER=vcs
# POSTGRES_PASSWORD 由 secrets/postgres_password.txt 提供（勿在此处明文）
```

> ⚠️ `env_file: .env` 会把 `.env` 里的 `DATABASE_URL`（指向 localhost）注入容器；
> 但 `entrypoint.sh` 会**始终**用 `POSTGRES_*` 重新派生连接串覆盖它（见 §4.4），所以无碍。
> 反之，`.env` 必须存在（compose 引用了它），否则 `up` 报错。

### 4.2 secrets（`docker/scripts/gen_secrets.sh`）

```bash
sh docker/scripts/gen_secrets.sh            # 默认目录 secrets/，幂等（已存在则跳过）
```

生成（`chmod 600`）：

| 文件 | 内容 | 映射到 env |
|------|------|-----------|
| `secrets/postgres_password.txt` | `openssl rand -base64 48` | `POSTGRES_PASSWORD` |
| `secrets/admin_init_key.txt`    | `openssl rand -base64 48` | `ADMIN_INIT_KEY` |
| `secrets/flower_basic_auth.txt` | `admin:<随机>`            | `FLOWER_BASIC_AUTH` |

> `admin_init_key` 为**引导密钥，必填且需强随机**（弱口令将拒绝启动）。
> `flower_basic_auth` 必须是 `user:password` 形式（Flower `--basic-auth` 要求），默认用户 `admin`。

### 4.3 自签证书（`docker/scripts/gen_certs.sh`）

```bash
sh docker/scripts/gen_certs.sh                  # 默认目录 certs/，CN=vcs.local
sh docker/scripts/gen_certs.sh certs vcs.intra  # 指定目录与 CN
```

生成 `certs/vcs.crt`（10 年有效期，SAN = `DNS:<CN>,DNS:localhost,IP:127.0.0.1`）与 `certs/vcs.key`。
`docker-compose.prod.yml` / `docker-compose.release.yml` 把 `./certs` 以只读挂到 `/etc/nginx/certs`。

> 浏览器会提示自签证书不受信，属正常；生产可换成受信 CA 签发的 `vcs.crt`/`vcs.key`。

### 4.4 entrypoint 语义（`docker/entrypoint.sh`）—— 重要的"单一真源"

容器启动时依次执行：

1. **装载 secrets**：若 `/run/secrets/{postgres_password,admin_init_key,flower_basic_auth}` 存在，
   读出并覆盖同名 env（`POSTGRES_PASSWORD` / `ADMIN_INIT_KEY` / `FLOWER_BASIC_AUTH`）。
2. **派生连接串**：只要 `POSTGRES_HOST` 有值（容器内恒有），就**始终**用 `POSTGRES_*`
   拼出 `DATABASE_URL`（`postgresql+asyncpg://`）与 `DATABASE_URL_SYNC`（`postgresql://`），
   覆盖 `.env` 注进来的 localhost 串。
3. **兜底建数据目录**（`UPLOAD_DIR/OUTPUT_DIR/QUALITY_REPORT_DIR/LOG_FILE` 的父目录）。
4. `exec "$@"` 启动真正命令。

> 这解释了常见困惑：**改数据库地址请改 `POSTGRES_*`，而不是改 `.env` 的 `DATABASE_URL`**。

### 4.5 关键配置项（摘）

| 变量 | 默认 | 说明 |
|------|------|------|
| `POSTGRES_DB` / `POSTGRES_USER` | `vcs_db` / `postgres` | 数据库名/用户（prod 模板用 `vcs`） |
| `POSTGRES_PASSWORD(_FILE)` | — | 密码或其 secret 文件（**二者互斥**，见 §7） |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | `redis://redis:6379/0`（backend `/1`） | 由 compose 覆盖 |
| `ADMIN_INIT_KEY` | — | **必填**，强随机 |
| `MAX_UPLOAD_SIZE_MB` | `500` | 需与 nginx `client_max_body_size 512m` 协调 |
| `CORS_ORIGINS` | `["*"]` | 生产应设为真实来源 |
| `UPLOAD_DIR` 等 | 容器内 `/data/...` | compose 显式覆盖 |

完整清单见 [附录 11.2](#112-环境变量参考)。

---

## 5. 三种部署模式

### 模式一：基线（开发 / 冒烟）—— `docker-compose.yml`

本地构建、端口全开，适合开发与快速验证。

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

- 访问：`http://localhost:45679/app/`，Swagger `http://localhost:45679/docs`，Flower `http://localhost:5555`。
- 特点：`api` 对宿主 `45679`；`postgres 5432`、`redis 6379`、`flower 5555` 均对宿主开放（**勿用于生产**）。

### 模式二：生产覆盖层（本地构建）—— `docker-compose.yml` + `docker-compose.prod.yml`

```bash
cp .env.prod.example .env
sh docker/scripts/gen_secrets.sh
sh docker/scripts/gen_certs.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

覆盖层带来的变化：

- **api 不再对外端口**（`ports: !reset []`），仅 nginx 可达；postgres/redis/flower 同样关闭外发。
- **Docker secrets**：`postgres_password` / `admin_init_key` / `flower_basic_auth`。
- **Nginx**：`80 → 301 → 443`，托管 `/app/`，反代 `/api/`、`/ws`、`/health`、`/docs`。
- **日志轮转**（`json-file`, 10m×5）与**资源限额**（compose v2 非 Swarm 需 `--compatibility` 生效）。
- 在**本地**基于源码构建镜像。

访问：`https://<主机>/app/`。

### 模式三：发布编排（已发布镜像，生产/离线推荐）—— `docker-compose.release.yml`

不本地构建，直接用仓库/离线包里的镜像；已内置 Nginx + TLS + secrets + 端口收敛。

```bash
cp .env.prod.example .env
sh docker/scripts/gen_secrets.sh
sh docker/scripts/gen_certs.sh

# 三参数决定镜像来源
REGISTRY=docker.io NAMESPACE=meteor850808 TAG=2.24.0 \
  docker compose -f docker-compose.release.yml up -d
```

- 也可把 `REGISTRY/NAMESPACE/TAG` 写进 `.env` 后直接 `up -d`（`NAMESPACE` 未设置会明确报错）。
- `migrate` 作为一次性 Job 自动在 api 前跑完（`alembic upgrade head && python scripts/init_db.py`）。
- 升级 = 改 `TAG` → `pull` → `up -d`（见 §9）。

访问：`https://<主机>/app/`。

### 模式对比

| 维度 | 模式一 基线 | 模式二 生产覆盖层 | 模式三 发布编排 |
|------|------------|------------------|----------------|
| 镜像来源 | 本地构建 | 本地构建 | **仓库/离线** |
| 对外端口 | 45679/5432/6379/5555 | 仅 80/443 | 仅 80/443 |
| Nginx + TLS | ✗ | ✓ | ✓ |
| Docker secrets | ✗ | ✓ | ✓ |
| 离线可用 | 需源码+网络 | 需源码+网络 | ✓（save/load） |
| 适用 | 开发/冒烟 | 生产（源码在手） | **生产/内网交付** |

### Makefile 快捷方式（`make help`）

| target | 等价命令 | 用途 |
|--------|----------|------|
| `secrets` | `sh docker/scripts/gen_secrets.sh` | 生成 secrets |
| `certs` | `sh docker/scripts/gen_certs.sh` | 生成证书 |
| `build` | `docker compose build` | 构建（基线） |
| `up` / `down` | `docker compose up -d` / `down` | 起/停（基线） |
| `prod-up` / `prod-down` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` / `down` | 生产层 |
| `migrate` | `docker compose run --rm migrate` | 手动迁移 |
| `restart` | `docker compose restart api worker beat` | 重启应用服务 |
| `logs` / `ps` | `docker compose logs -f --tail=200` / `ps` | 日志/状态 |
| `clean` | `docker compose -f ...prod... down -v` | **清空含卷**（危险） |
| `push` / `push-latest` | `… sh docker/scripts/build_push.sh` | 构建并推镜像 |

---

## 6. 日常使用

以下命令中，生产/发布模式请在末尾加 `-f docker-compose.release.yml`
（或模式二的 `-f docker-compose.yml -f docker-compose.prod.yml`）。为避免重复，下文用
`<COMPOSE>` 代指你的编排参数。

```bash
# 起服 / 停服（保留数据卷）
docker compose <COMPOSE> up -d
docker compose <COMPOSE> down

# 查看状态（含健康）
docker compose <COMPOSE> ps

# 查看日志（跟随，最近 200 行）
docker compose <COMPOSE> logs -f --tail=200
docker compose <COMPOSE> logs -f --tail=200 api      # 单服务

# 重启应用服务（不重建）
docker compose <COMPOSE> restart api worker beat

# 重建单个服务（改了配置后）
docker compose <COMPOSE> up -d --build api

# 手动跑数据库迁移
docker compose <COMPOSE> run --rm migrate

# Worker 水平扩展（基线/发布：worker 无固定 host 端口，可 scale）
docker compose <COMPOSE> up -d --scale worker=4
```

### 6.1 访问入口

| 项目 | 基线模式 | 生产/发布模式 |
|------|----------|---------------|
| 前端 SPA | `http://<host>:45679/app/` | `https://<host>/app/` |
| Swagger  | `http://<host>:45679/docs` | `https://<host>/docs` |
| ReDoc    | `http://<host>:45679/redoc` | `https://<host>/redoc` |
| 健康检查 | `http://<host>:45679/health` | `https://<host>/health` |
| Flower   | `http://<host>:5555` | 默认不外发（`docker compose port flower 5555` 查映射，或临时开放） |

### 6.2 进入容器

```bash
docker compose <COMPOSE> exec api sh
docker compose <COMPOSE> exec api python -c "import fiona, shapely, pyproj; print('GIS OK')"
docker compose <COMPOSE> exec api dwg2dxf --version        # LibreDWG 运行时
docker compose <COMPOSE> exec postgres psql -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-vcs_db} -c '\dt'
docker compose <COMPOSE> exec redis redis-cli ping
```

### 6.3 常用运维脚本（非 Docker）

- `restart.sh`（Linux，端口 `45679`）/ `restart.bat`（Windows，端口 `45681`）：裸机 venv 方式的一键重启
  （启动前会清理旧端口 `45678/45679/45680`）。**与 Docker 部署互斥**——同一台机二选一，勿同时占用端口。
- `deploy_linux.sh`：裸机（venv + 本机 PG/Redis）一键部署，仅作对照，Docker 场景**不用**。

---

## 7. 调试与排障

### 7.1 分层诊断法（自上而下）

```
① 容器是否在跑？      docker compose ps            → 看 State / Health
② 服务健康吗？        curl -k https://localhost/health
③ 日志报什么？        docker compose logs --tail=200 <svc>
④ 依赖连通吗？        exec api → ping postgres/redis
⑤ 配置对吗？          docker compose config        → 渲染后的最终配置
```

`docker compose config` 会把多文件合并、变量替换后的**最终 YAML** 打出来，是排查"覆盖层没生效"的第一武器。

### 7.2 常见故障速查表

#### A. 构建期

| 症状 | 可能原因 | 排查 / 修复 |
|------|----------|-------------|
| `curl: (28) Failed to connect to github.com port 443` | 构建机网络抖动/受限，下载 LibreDWG 超时 | 已加 `--retry 5 --retry-all-errors`；受限网络请改离线构建或预置 tar |
| `configure: error: no suitable Python interpreter found` | LibreDWG 源码构建缺 Python（旧版 Dockerfile） | 已用 `--disable-python` 并装 `python3`（现版本无需处理） |
| `Package 'libmdbodbc1' has no installation candidate` | bookworm 无该包（旧版 Dockerfile） | 已改用 `odbc-mdbtools`（现版本无需处理） |
| arm64 构建极慢 | 本机 `docker build` 走了 QEMU 模拟 | CI 用**原生 arm64 runner**；本机多架构用 `buildx` 且耐心等待或改单架构 |
| `pip install` 失败（网络） | 无 PyPI 出口 | 配镜像源或离线 wheel；用预构建镜像规避 |

#### B. 启动期

| 症状 | 可能原因 | 排查 / 修复 |
|------|----------|-------------|
| `api` 反复重启 / unhealthy | 应用启动异常 | `docker compose logs api`；常见为 DB 未就绪或 `ADMIN_INIT_KEY` 问题 |
| **`ADMIN_INIT_KEY` 缺失/弱** | 未设或太短 | `sh docker/scripts/gen_secrets.sh`；生产经 secret 注入 |
| **DB 连接失败** | postgres 未健康 / 密码不符 / `file_env` 冲突 | `docker compose ps postgres` 是否 `healthy`；确认**只用** `POSTGRES_PASSWORD_FILE`，**不要**同时设 `POSTGRES_PASSWORD`（官方镜像二者并存会报错退出） |
| `unable to read secret` / `permission denied` | secrets 文件权限/归属 | secrets 目录与文件可被 Docker 读取；`chmod 600 secrets/*.txt`；检查 SELinux/上下文 |
| `port is already allocated` | 45679/80/443/5432/6379/5555 被占 | `ss -ltnp`（或 `netstat -tlnp`）定位并释放；改映射端口 |
| `ports: !reset`/`!override` 语法错误 | Compose < 2.24 | 升级 Compose，或退回"基线不发布端口 + env 注入"方案 |
| `env file .env not found` | 未 `cp` 模板 | `cp .env.example .env`（或 `.env.prod.example`） |
| nginx `bind() 443 failed` | 443 被占/特权限制 | 释放端口或以 root/`CAP_NET_BIND_SERVICE` 运行 |
| 数据目录权限报错 | 命名卷首挂为空、属主不符 | entrypoint 已兜底建目录；仍报错检查卷属主（`appuser`） |

#### C. 运行期

| 症状 | 可能原因 | 排查 / 修复 |
|------|----------|-------------|
| 上传后任务**一直不执行** | worker 未连 broker / 队列不匹配 | `docker compose logs worker`；`docker compose exec redis redis-cli ping`；确认队列 `vector_conversion,celery` 与 broker `redis://redis:6379/0` |
| worker 正常但**不消费** | 任务投到别的队列/别的 vhost | 用 Flower 看 `vector_conversion` 队列积压；核对 `CELERY_BROKER_URL` 一致 |
| **beat 不触发定时任务** | beat 模块路径错误或未起 | `docker compose logs beat`；路径 `app.workers.celery_app.celery_app` |
| Flower 打开 **401** | `--basic-auth` 未生效/凭据不符 | 容器内 `echo $FLOWER_BASIC_AUTH`；与 `secrets/flower_basic_auth.txt`（`user:pass`）一致 |
| **大文件上传 413** | `MAX_UPLOAD_SIZE_MB` 与 nginx `client_max_body_size` 不一致 | 二者对齐（现 nginx 为 `512m`，应对应 `MAX_UPLOAD_SIZE_MB≤512`） |
| 浏览器证书告警 | 自签证书 | 正常；导入 `certs/vcs.crt` 到信任库，或换受信 CA 证书 |
| `/api/` 502 | api 未健康或 nginx 先于 api 起 | `docker compose ps api`；`docker compose up -d --force-recreate nginx` |
| **MDB 导出失败** | Linux 平台限制（写 MDB 依赖 ACE/ADOX） | 预期行为，接口返回明确错误；MDB **读取**正常（MDBTools） |
| DWG 转换返回空 | LibreDWG 运行时缺失 | `docker compose exec api dwg2dxf --version` 是否可用 |
| 磁盘涨满 | 上传/产物/日志累积 | 清理 `vector_*` 卷内旧文件；日志已轮转（10m×5） |

> 冒烟/接口自测：见 `scripts/smoke_convert.py`、`scripts/smoke_imp_exp_ui.py`。

### 7.3 日志位置

| 来源 | 位置 |
|------|------|
| 容器 stdout/stderr | `docker compose logs`（`json-file` 轮转 10m×5） |
| 应用文件日志 | 卷 `vector_logs` → 容器内 `/data/logs/vcs.log` |
| nginx | `docker compose logs nginx` |
| postgres/redis | `docker compose logs postgres` / `redis` |

---

## 8. 数据管理（备份 / 恢复）

### 8.1 数据库备份（逻辑）

```bash
# 导出 SQL 到本机
docker compose <COMPOSE> exec -T postgres \
  pg_dump -U ${POSTGRES_USER:-postgres} ${POSTGRES_DB:-vcs_db} \
  | gzip > vcs_db_$(date +%F_%H%M).sql.gz
```

### 8.2 卷备份（物理，含上传/产物/报告）

命名卷实际名带 **compose 项目前缀**（通常是目录名），先用 `docker volume ls` 确认：

```bash
docker volume ls | grep vector_
# 示例卷名：空间质检_vector_uploads

# 逐个打包（示例：uploads）
docker run --rm \
  -v 空间质检_vector_uploads:/data:ro \
  -v "${PWD}:/backup" \
  alpine tar czf /backup/vector_uploads.tgz -C /data .
```

> 数据库物理卷 `pg_data` 建议用 §8.1 的逻辑备份，不要直接拷物理文件（版本/一致性风险）。

### 8.3 恢复

```bash
# 数据库
gunzip -c vcs_db_2026-10-16.sql.gz | docker compose <COMPOSE> exec -T postgres \
  psql -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-vcs_db}

# 卷
docker run --rm -v 空间质检_vector_uploads:/data -v "${PWD}:/backup" \
  alpine sh -c "cd /data && tar xzf /backup/vector_uploads.tgz"
```

### 8.4 清理（谨慎）

```bash
docker compose <COMPOSE> down              # 停服，保留卷
docker compose <COMPOSE> down -v           # ⚠️ 删除所有卷（数据全失）
docker image prune                          # 清理悬空镜像
```

---

## 9. 升级与回滚

### 9.1 升级（发布编排模式）

```bash
# 1) 备份（§8.1）
# 2) 改 TAG（命令行或 .env）
export TAG=2.24.1

# 3) 拉新镜像
docker compose -f docker-compose.release.yml pull

# 4) 起服（migrate 会先跑 alembic upgrade head）
docker compose -f docker-compose.release.yml up -d

# 5) 验证
docker compose -f docker-compose.release.yml ps
curl -k https://localhost/health
```

### 9.2 升级（本地构建模式）

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### 9.3 回滚

```bash
# 回退到上一 tag
export TAG=2.24.0
docker compose -f docker-compose.release.yml up -d
```

> ⚠️ **数据库迁移不可自动回退**。若新版本含破坏性迁移，请先确认对应 `alembic downgrade` 步骤，
> 或在升级前留存 §8.1 备份；必要时用备份恢复。

---

## 10. 安全加固

1. **secrets 不入库**：`.gitignore` 忽略 `.env`、`secrets/`、`certs/`；`gen_secrets.sh` 以 `600` 落盘。
2. **端口收敛**：生产仅暴露 `80/443`，DB/Redis/API/Flower 一律内网。
3. **TLS**：nginx 强制 `80 → 301 → 443`；生产建议换受信 CA 证书。
4. **Flower 认证**：`FLOWER_BASIC_AUTH` 必设（默认 `admin:<随机>`），且默认不对外端口。
5. **引导密钥**：`ADMIN_INIT_KEY` 必须强随机，弱口令拒绝启动。
6. **CORS**：生产将 `CORS_ORIGINS` 设为真实来源，勿用 `["*"]`。
7. **凭据管理**：CI 用 `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`（PAT）；**PAT 一旦泄露须立即 revoke**。
8. **运行用户**：容器内以非 root 的 `appuser` 运行。

---

## 11. 附录

### 11.1 关键文件清单

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 多阶段构建（LibreDWG + 4 个 target） |
| `docker/nginx/Dockerfile` | Nginx 镜像（含前端静态） |
| `docker/nginx/nginx.conf` + `conf.d/vcs.conf` | 反代/TLS/上传大小 |
| `docker/entrypoint.sh` | secrets 装载 + 连接串派生 + 建目录 |
| `docker-compose.yml` | 基线编排 |
| `docker-compose.prod.yml` | 生产覆盖层（叠加基线） |
| `docker-compose.release.yml` | 发布编排（已发布镜像，独立可用） |
| `docker/scripts/gen_secrets.sh` / `gen_certs.sh` | secrets / 证书生成 |
| `docker/scripts/build_push.sh` | 本地构建并推送镜像 |
| `docker/registry/*` | 内网 `registry:2` + htpasswd |
| `Makefile` | 快捷 targets |
| `.env.example` / `.env.prod.example` | env 模板 |
| `.github/workflows/docker-publish.yml` | CI 双架构构建发布 |
| `.github/workflows/ci.yml` | CI 测试/静态校验 |

### 11.2 环境变量参考

| 变量 | 默认 | 归属 | 说明 |
|------|------|------|------|
| `POSTGRES_DB` | `vcs_db` | app/db | 库名 |
| `POSTGRES_USER` | `postgres` | app/db | 用户 |
| `POSTGRES_PASSWORD` | `postgres` | db | 密码（**与 `_FILE` 互斥**） |
| `POSTGRES_PASSWORD_FILE` | — | db | secret 文件路径（生产用） |
| `POSTGRES_HOST`/`POSTGRES_PORT` | `postgres`/`5432` | app | entrypoint 据此派生连接串 |
| `DATABASE_URL` / `DATABASE_URL_SYNC` | 由上面派生 | app | **勿手改**（会被覆盖） |
| `REDIS_URL` | `redis://redis:6379/0` | app | 缓存 |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` / `/1` | worker/beat/flower | 队列 |
| `ADMIN_INIT_KEY` | — | app | **必填**强随机引导密钥 |
| `FLOWER_BASIC_AUTH` | — | flower | `user:password` |
| `CORS_ORIGINS` | `["*"]` | app | 生产收紧 |
| `DEBUG` / `LOG_LEVEL` | `false` / `INFO` | app | 运行级别 |
| `MAX_UPLOAD_SIZE_MB` | `500` | app | 与 nginx `client_max_body_size` 协调 |
| `UPLOAD_DIR`/`OUTPUT_DIR`/`QUALITY_REPORT_DIR`/`LOG_FILE` | `/data/...`（容器内） | app | 卷挂载点 |
| `REGISTRY`/`NAMESPACE`/`TAG` | `docker.io`/—/`2.24.0` | 发布编排 | 镜像来源 |

### 11.3 Makefile 速查

```
分开执行： make help
生成密钥： make secrets       生成证书： make certs
基线：     make build | make up | make down | make logs | make ps | make migrate | make restart
生产：     make prod-up | make prod-down | make clean(危险，含 -v)
推送：     make push NAMESPACE=xxx TAG=2.24.0 | make push-latest NAMESPACE=xxx TAG=2.24.0
```

### 11.4 镜像 tag 与 digest（v2.24.0）

multi-arch（`linux/amd64` + `linux/arm64`）manifest digest：

| 镜像 | manifest digest |
|------|-----------------|
| `vcs-api`    | `sha256:1a17dec0237627baf9d22478c9769f599bee2644ebe11f73ce7305db4698b423` |
| `vcs-worker` | `sha256:2536642d07931a28db536b10cfbaf4517e418be7454758c865d1c3a7c7bf8319` |
| `vcs-beat`   | `sha256:416582e4ccfd7f4b9b306418cc70d5df2ae84b0174698b3dfff7a301e22b00ef` |
| `vcs-flower` | `sha256:550c9786e35ce2c0e419eee3c66f0280e124145a56233cb54e557d9cd17a42a0` |
| `vcs-nginx`  | `sha256:fc5b17b0005a37c3a5b6a89ca349265760716e94d5a88378344bfbb5ee847298` |

> 由 Run `38900892791` 产出于 Docker Hub 命名空间 `meteor850808`；校验：
> `docker buildx imagetools inspect meteor850808/vcs-api:2.24.0`。

### 11.5 一分钟自检清单

```bash
docker compose <COMPOSE> ps                 # 全部 running/healthy
curl -k https://localhost/health            # {"status":"ok"...}
docker compose <COMPOSE> exec api dwg2dxf --version
docker compose <COMPOSE> exec redis redis-cli ping
docker compose <COMPOSE> exec postgres pg_isready
docker compose <COMPOSE> logs --tail=50 api
```

---

*本手册随版本更新维护；Docker 相关变更请同步更新 `BASELINE.md` 与 `docs/revision_vX_Y.md`。*
