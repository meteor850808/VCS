# v2.24.0 — Docker 生产加固

> 日期：2026-10-13
> 版本：v2.23.0 → v2.24.0
> 范围：容器化生产部署（内网 Linux 单机、自签 TLS、离线交付）；**0 项数据库变更**。

## 1. 背景与目标

既有 `Dockerfile`/`docker-compose.yml` 停留在「能跑」阶段，存在 7 处缺陷，且镜像缺失
Windows 侧才有的 DWG/MDB 能力。本版目标：

1. 修复 D1–D7 编排/构建缺陷；
2. 补齐容器内 DWG/MDB 的 Linux 适配（C1）；
3. 引入 Nginx 反代（自签 TLS）、Docker secrets、Makefile/发布脚本。

设计规格与取舍见 `docs/superpowers/specs/2026-10-13-docker-production-hardening-design.md`，
实现计划见 `docs/superpowers/plans/2026-10-13-docker-production-hardening.md`。

## 2. 缺陷修复清单

| 编号 | 缺陷 | 修复 |
|------|------|------|
| D1 | 卷挂载路径与 `UPLOAD_DIR/OUTPUT_DIR/...` env 不一致（`/data/uploads` vs `./data/uploads`） | compose 显式设 `UPLOAD_DIR=/data/uploads` 等 + 新增 `vector_reports`/`vector_logs` 卷 |
| D2 | Flower `CMD` exec 形式下 `$FLOWER_BASIC_AUTH` 不展开 | 改 `CMD ["sh","-c","celery ... --basic-auth=$FLOWER_BASIC_AUTH"]` |
| D3 | `beat` 命令缺 `.celery_app`，启动即崩 | `celery -A app.workers.celery_app.celery_app beat` |
| D4 | 镜像无 LibreDWG / mdbtools，DWG/MDB 能力丢失 | 多阶段源码构建 LibreDWG + 安装 mdbtools/unixodbc |
| D5 | `.dockerignore` 漏排（docs/offline/deploy/tools 等） | 补齐排除项 |
| D6 | `version: "3.9"` 已废弃 | 删除 |
| D7 | 基础镜像浮动 tag | `python:3.11.11-slim` / `nginx:1.27-alpine` 固定 |

## 3. 架构与新增组件

- **Nginx（`docker/nginx/`）**：反代 `/api/`、`/ws`、`/health`、`/docs`、`/redoc`、`/openapi.json`；
  托管前端静态 `/app/`；80 → 301 → 443（自签证书挂载 `./certs`）。
- **生产覆盖层（`docker-compose.prod.yml`）**：与基线叠加使用；api/pg/redis 默认不外发端口；
  secrets 文件；`json-file` 日志轮转；资源限额（`--compatibility` 下生效）。
- **entrypoint（`docker/entrypoint.sh`）**：从 `/run/secrets/*` 装载 `POSTGRES_PASSWORD`/
  `ADMIN_INIT_KEY`/`FLOWER_BASIC_AUTH`；容器内（`POSTGRES_HOST` 存在）**始终**从 `POSTGRES_*`
  派生 `DATABASE_URL`/`DATABASE_URL_SYNC`（单一真源，覆盖 `env_file: .env` 注入的 localhost 串）；
  兜底建数据目录；`exec "$@"`。
- **发布脚本**：`Makefile`（secrets/certs/build/up/logs/prod-up/...）、
  `docker/scripts/gen_secrets.sh`、`docker/scripts/gen_certs.sh`；`.gitattributes` 钉脚本为 LF。

## 4. Linux 适配（C1）

- 新增 `app/services/_odbc.py`：`is_windows()` / `access_driver()` / `access_conn_str(path)`。
  - Windows 保持旧串 `DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=...;`；
  - POSIX 使用 `DRIVER={MDBTools};DBQ=...`（无尾分号），驱动经 `/etc/odbcinst.ini` 注册。
- `mdb_processor.py`、`esri_shapebin.py`（`__main__` 调试块）、`export_service.py`
  统一改用 `access_conn_str()`。
- `export_service._ensure_mdb_export_supported()`：非 Windows 抛 `RuntimeError`（MDB 写入依赖
  ADOX/ACE + Access ODBC，Linux 不可用），在 `export_mdb()` 入口调用。
- `dwg_converter._resolve_dwg2dxf()`：平台感知定位 —— Windows 用打包 `_DWG2DXF`，POSIX 用
  `shutil.which("dwg2dxf")`；`cwd` 仅 Windows 设为 `_TOOLS_DIR`（DLL 依赖）。

## 5. 关键约束

- **`Settings` 严格模式**：只注入既有字段；**不存在 `PREVIEW_DIR`**（预览由 `UPLOAD_DIR`
  父目录 + `/previews` 派生）。
- **`ADMIN_INIT_KEY`** 必填且需强随机（弱口令拒绝启动）。
- **Windows 行为零回归**：所有平台分支在 win32 下与改动前一致。
- **Compose 合并语义**：`ports` 跨 `-f` 是**拼接**而非替换 → 覆盖层移除端口必须用 `ports: !reset []`
  （需 Compose ≥ 2.24）；`environment`/`volumes`/`secrets` 为深合并。
- **Postgres 密码来源互斥**：官方镜像 `file_env` 在 `POSTGRES_PASSWORD` 与 `POSTGRES_PASSWORD_FILE`
  同时存在时报错退出 → 覆盖层用 `POSTGRES_PASSWORD: !reset null` 清掉基线明文。
- **env_file 连接串陷阱**：`env_file: .env` 会把 `.env` 的 `DATABASE_URL`（localhost）注入容器，
  故 entrypoint 必须**始终**用 `POSTGRES_*` 覆盖，而非「仅当未设置时」派生。
- **密钥/证书不入库**：`.gitignore` 忽略 `secrets/`、`certs/`；`.dockerignore` 同样排除，避免烤进镜像。

## 6. 验证

- 本机（Windows）：`pytest tests/`（含新增用例）+ compose/nginx YAML 解析 + `bash -n` 脚本。
- 目标 Linux / CI：`docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q`、
  构建镜像、起服、`curl -k https://localhost/health`、容器内 `dwg2dxf --version`、
  MDB 读/写行为、secrets 生效。
- CI 新增 `prod-config` job 做静态校验。

## 7. 风险与回滚

- LibreDWG 源码构建失败 → DWG 转换降级返回 None，不阻断起服；回滚移除 builder 阶段。
- compose `secrets` 需 Compose ≥ 2.23、`!reset` 标签需 ≥ 2.24；低版本退回到「基线不发布端口 + env 注入」方案。
- 卷改名（新增 reports/logs）不影响既有 `vector_uploads/vector_outputs` 数据。

## 8. 未做 / 已知限制

- MDB **导出**在 Linux 不可用（平台限制）；已用明确错误提示替代。
- LibreDWG 版本通过 `ARG LIBREDWG_VERSION` 固定，构建机需能访问 GitHub Release；离线交付
  采用「联网机 build → `docker save` → 目标机 `docker load`」。
- `deploy/resources` 限额在 Compose 非 Swarm 下需 `--compatibility` 才生效。

## 9. CI 自动构建与发布（双架构，2026-10-16 追加）

### 9.1 目标
在 GitHub Actions 上构建 5 个镜像的 **multi-arch manifest**（`linux/amd64` + `linux/arm64`）
并推送至 Docker Hub。

### 9.2 工作流
- `.github/workflows/docker-publish.yml`：
  - 触发：`workflow_dispatch`（可填 tag）+ `push tags: v*`；
  - matrix = 2 runner × 5 镜像 = **10 个 build job**，各推 `<tag>-<arch>`；
  - `merge-manifest` job 用 `docker buildx imagetools create` 合并为多架构 manifest
    并 `imagetools inspect` 校验；
  - **架构策略：原生 runner**（公开仓库免费）——`linux/amd64` → `ubuntu-latest`，
    `linux/arm64` → `ubuntu-24.04-arm`（**原生 ARM，无需 QEMU**，快且稳定）；
  - 缓存：`type=gha,scope=<arch>`；`provenance: false`。
- 必需 Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`；可选 Variable：`DOCKERHUB_REGISTRY`（默认 `docker.io`）。
- `.github/workflows/ci.yml`：`test` / `smoke` / `prod-config`（`prod-config` 的 `bash -n` 已纳入
  `build_push.sh`、`gen_htpasswd.sh`）。

### 9.3 构建期修复（CI 暴露）
| 编号 | 现象 | 修复 |
|------|------|------|
| B1 | `libmdbodbc1` 在 bookworm 无候选包 | 改装 `odbc-mdbtools`；`odbcinst.ini` 驱动路径改为动态探测（兼容 arm64 路径） |
| B2 | LibreDWG `configure: no suitable Python interpreter` | `configure` 增 `--disable-python`；builder 阶段装 `python3` |
| B3 | arm64 下载 LibreDWG 偶发 `curl (28) 连接超时` | curl 增 `--retry 5 --retry-delay 5 --retry-all-errors` |
| B4 | QEMU 模拟 arm64 编译 LibreDWG 极慢 | 改用原生 `ubuntu-24.04-arm` runner，弃用 QEMU |

### 9.4 验证结果
- Run `38900892791`：**11/11 job 成功**（10 build + merge-manifest）。
- 5 个镜像 `2.24.0` 均生成 `application/vnd.oci.image.index.v1+json`，含 `linux/amd64` + `linux/arm64`：

| 镜像 | manifest digest |
|------|-----------------|
| `vcs-api`    | `sha256:1a17dec0237627baf9d22478c9769f599bee2644ebe11f73ce7305db4698b423` |
| `vcs-worker` | `sha256:2536642d07931a28db536b10cfbaf4517e418be7454758c865d1c3a7c7bf8319` |
| `vcs-beat`   | `sha256:416582e4ccfd7f4b9b306418cc70d5df2ae84b0174698b3dfff7a301e22b00ef` |
| `vcs-flower` | `sha256:550c9786e35ce2c0e419eee3c66f0280e124145a56233cb54e557d9cd17a42a0` |
| `vcs-nginx`  | `sha256:fc5b17b0005a37c3a5b6a89ca349265760716e94d5a88378344bbfbb5ee847298` |

### 9.5 遗留
- Docker Hub PAT 曾明文暴露，**须 revoke 后重建并更新 `DOCKERHUB_TOKEN` Secret**。
- 目标生产机如无法访问 Docker Hub，需改推内网 registry（设 `DOCKERHUB_REGISTRY`）或 `docker save/load`。
