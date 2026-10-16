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
