# Docker 生产加固实现计划（v2.24.0）

> 目标：把既有 Docker 化从「能跑」升级为「生产可用 + 内网 Linux 单机可交付」：
> 修复既有编排缺陷 D1–D7、补齐容器内 DWG/MDB 的 Linux 能力（C1）、
> 引入 Nginx 反代（自签 TLS）、Docker secrets、Makefile/发布脚本。
>
> 规格来源：`docs/superpowers/specs/2026-10-13-docker-production-hardening-design.md`（已批准）。
> 版本：`v2.23.0` → `v2.24.0`。

---

## Global Constraints

1. **目标运行平台**：内网 Linux 单机（Debian/Ubuntu 系），`docker compose` v2（≥2.23，需支持非 Swarm 的 `secrets` 文件）。产物以 `docker save` 导出镜像离线交付。
2. **本机构建/验证能力有限**：开发机为 Windows，**无 docker / 无 openssl / 无 make**。因此：
   - 源码级验证（pytest、`python -c yaml.safe_load`、`bash -n`）在本机执行；
   - `docker build` / `docker compose config` / 起服 / TLS 握手验证标注为「**目标 Linux 机或 CI 执行**」，本机不阻塞。
3. **Windows 行为零回归**：所有代码改动在 `sys.platform == "win32"` 时保持与改动前**完全一致**（驱动名、命令、cwd 不变）。全量回归 `pytest tests/` 必须全绿（基线 1778 passed / 100% 覆盖）。
4. **`Settings` 为 `extra_forbidden` 严格模式**：
   - 只允许注入**已存在字段**对应的 env（`log_level/debug/log_file/database_url/database_url_sync/db_*/redis_url/celery_broker_url/celery_result_backend/admin_init_key/cors_origins/max_upload_size_mb/max_data_store_size_mb/max_single_file_size_mb/max_archive_size_mb/file_chunk_size_mb/enable_file_dedup/max_batch_size/upload_dir/output_dir/quality_report_dir/frontend_dir/sandbox_limit/ws_heartbeat_interval`）。
   - **不存在 `PREVIEW_DIR`**，禁止设置；预览目录由 `Path(settings.upload_dir).parent / "previews"` 派生（`app/services/cad_cache.py:141`、`app/api/v1/cad/_common.py:55`）→ `UPLOAD_DIR=/data/uploads` 自动得 `/data/previews`。
   - `POSTGRES_*` 由 **entrypoint shell** 消费（非 pydantic 字段），设置它们不会触发校验错误。
5. **单一真源**：容器内 `DATABASE_URL`/`DATABASE_URL_SYNC` 由 `docker/entrypoint.sh` 从 `POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB` 派生；compose 不再硬编码连接串。
6. **API 路径契约**：路由前缀 `/api/v1`（`app/api/...` 中 `APIRouter(prefix="/api/v1")`）；前端静态挂在 FastAPI `/app`（`app/main.py:105`）；健康检查 `/health`。Nginx 反代须覆盖 `/api/`、`/ws`、`/health`、`/docs`、`/openapi.json`、`/redoc`，静态 `/app/`。
7. **提交规范**：conventional commits，每次提交仅含本任务涉及文件，范围限定在 `空间质检/`。本计划每个 Task 结尾一次提交（执行阶段落实）。
8. **本机执行命令约定**：命令用 `workdir` 指定目录；PowerShell 5.1 不支持 `&&`，用 `;` 或 `if ($?) {}`。

---

## File Structure

**新增**
- `app/services/_odbc.py` — 平台感知 Access/MDB ODBC 助手（驱动名 + 连接串 + 平台判定）。
- `docker/entrypoint.sh` — 通用入口：派生 DB 连接串、装载 secrets、建目录、`exec "$@"`。
- `docker/scripts/gen_secrets.sh` — 生成 `secrets/*.txt`（POSTGRES_PASSWORD / ADMIN_INIT_KEY / FLOWER_BASIC_AUTH）。
- `docker/scripts/gen_certs.sh` — 用 openssl 生成自签证书到 `certs/`。
- `docker/nginx/Dockerfile` — 构建 nginx 镜像（含反代配置 + 前端静态）。
- `docker/nginx/nginx.conf` — 主配置（worker、日志、TLS 全局）。
- `docker/nginx/conf.d/vcs.conf` — 站点配置（80→443、/api、/ws、/health、静态）。
- `docker-compose.prod.yml` — 生产覆盖层（nginx services、secrets、资源限额、只读、日志轮转）。
- `.env.prod.example` — 生产 env 模板（非敏感项）。
- `Makefile` — 构建/起服/证书/密钥/清理 targets。
- `tests/test_odbc_platform.py` — `_odbc` 单元测试。
- `tests/test_export_mdb_platform.py` — MDB 导出平台守卫单元测试。

**修改**
- `app/services/mdb_processor.py`、`app/services/esri_shapebin.py`、`app/services/export_service.py` — 使用 `_odbc`，MDB 写加平台守卫。
- `app/services/dwg_converter.py` — 平台感知定位 `dwg2dxf`。
- `tests/test_dwg_converter.py`、`tests/test_export_service_branches.py`、`tests/test_data_download_export.py` — 平台无关化（Linux CI 友好）。
- `Dockerfile` — 加 LibreDWG 构建阶段 + mdbtools/unixodbc + entrypoint + 固定基础镜像 + 修 Flower CMD + 建数据目录。
- `docker-compose.yml` — 删 `version`、修 beat 命令、目录 env/卷、去硬编码连接串、透传 `POSTGRES_*`、修 healthcheck。
- `.dockerignore` — 补排 `deploy/ docs/ tools/ secrets/ certs/ Redis8/ offline/ *.md(除README)` 等。
- `.env.example` — 补目录变量说明（`/data/*`）。
- `README.md`、`BASELINE.md`、`app/main.py`(version)、`docs/revision_v2_24_0.md`(新建) — 版本与文档同步。
- `.github/workflows/ci.yml` — 增加 prod compose 语法/构建校验步骤。

---

## Task 1 — 平台感知 ODBC 助手 `app/services/_odbc.py`

**Files**
- Create: `app/services/_odbc.py`
- Test: `tests/test_odbc_platform.py`

**Step 1 写失败测试** `tests/test_odbc_platform.py`
```python
# -*- coding: utf-8 -*-
"""_odbc：平台感知的 Access/MDB ODBC 助手。"""
import sys

from app.services import _odbc


def test_driver_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _odbc.is_windows() is True
    assert _odbc.access_driver() == "Microsoft Access Driver (*.mdb, *.accdb)"


def test_driver_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _odbc.is_windows() is False
    assert _odbc.access_driver() == "MDBTools"


def test_conn_str_win32_has_trailing_semicolon(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _odbc.access_conn_str(r"C:\a\x.mdb") == (
        "DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\a\\x.mdb;")


def test_conn_str_posix_no_trailing_semicolon(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _odbc.access_conn_str("/data/a.mdb") == "DRIVER={MDBTools};DBQ=/data/a.mdb"
```

**Step 2 运行确认失败**：`python -m pytest tests/test_odbc_platform.py -q` → 期待 `ModuleNotFoundError/ImportError`。

**Step 3 实现** `app/services/_odbc.py`
```python
# -*- coding: utf-8 -*-
"""平台感知的 MS Access (MDB) ODBC 连接助手。

- Windows: 官方 Microsoft Access Driver（pyodbc 读写）。
- POSIX:   mdbtools 提供的 MDBTools 驱动（**只读**；写见 export_service 守卫）。
"""
from __future__ import annotations

import sys

ACCESS_DRIVER_WIN = "Microsoft Access Driver (*.mdb, *.accdb)"
ACCESS_DRIVER_POSIX = "MDBTools"


def is_windows() -> bool:
    """当前是否为 Windows 平台（运行时判定，便于测试 monkeypatch）。"""
    return sys.platform == "win32"


def access_driver() -> str:
    """返回当前平台可用的 Access ODBC 驱动名。"""
    return ACCESS_DRIVER_WIN if is_windows() else ACCESS_DRIVER_POSIX


def access_conn_str(path: str) -> str:
    """构造 pyodbc 连接串（保持 Windows 旧形态：含尾分号）。

    POSIX 的 MDBTools 驱动会把尾分号拼进文件名，故不加尾分号。
    """
    if is_windows():
        return f"DRIVER={{{ACCESS_DRIVER_WIN}}};DBQ={path};"
    return f"DRIVER={{{ACCESS_DRIVER_POSIX}}};DBQ={path}"
```

**Step 4 跑测试**：`python -m pytest tests/test_odbc_platform.py -q` → 4 passed。

**Step 5 提交**
```
feat(docker): 新增平台感知 MDB/ODBC 助手 _odbc
```

---

## Task 2 — MDB 只读接入 `_odbc`（mdb_processor + esri_shapebin）

**Files**
- Modify: `app/services/mdb_processor.py:46`
- Modify: `app/services/esri_shapebin.py:249-250`

**Step 1 改 `mdb_processor.py`**
- 顶部导入区（第 17 行 `from app.services.esri_shapebin import decode_esri_shapebin` 下方）新增：
  ```python
  from app.services._odbc import access_conn_str
  ```
- 第 46 行：
  ```python
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={mdb_path};"
  ```
  改为：
  ```python
        conn_str = access_conn_str(mdb_path)
  ```

**Step 2 改 `esri_shapebin.py`（仅 `__main__` 调试块，第 249-250 行）**
- 在 `if __name__ == "__main__":` 块内（`import pyodbc` 附近）新增 `from app.services._odbc import access_conn_str`。
- 第 249-250 行：
  ```python
      conn = pyodbc.connect(
          f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};")
  ```
  改为：
  ```python
      conn = pyodbc.connect(access_conn_str(path))
  ```

**Step 3 回归**：`python -m pytest tests/ -k "mdb or shapebin or esri" -q` → 全绿；再跑 `python -m pytest tests/test_service_gdb_spatial.py -q`。

**Step 4 提交**
```
refactor(docker): MDB 只读改用平台感知连接串
```

---

## Task 3 — MDB 导出 Windows 守卫 + 连接串收敛（export_service）

**Files**
- Modify: `app/services/export_service.py`（`_ensure_mdb_export_supported` 新增；`_create_empty_mdb` 前调用；`_drop_all_user_tables:594`；`export_mdb:635`；`export_mdb` 顶部守卫）
- Modify: `tests/test_export_service_branches.py`（两例加 Windows 跳过）
- Modify: `tests/test_data_download_export.py`（MDB 相关测试强制 windows）
- Test: `tests/test_export_mdb_platform.py`

**Step 1 写失败测试** `tests/test_export_mdb_platform.py`
```python
# -*- coding: utf-8 -*-
"""export_service MDB 写入的平台守卫。"""
import sys

import pytest

from app.services import export_service


def test_guard_raises_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="仅支持 Windows"):
        export_service._ensure_mdb_export_supported()


def test_guard_passes_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    export_service._ensure_mdb_export_supported()  # 不抛
```

**Step 2 运行确认失败**：`python -m pytest tests/test_export_mdb_platform.py -q` → AttributeError（函数不存在）。

**Step 3 实现**
- 顶部导入区（`from typing import ...` 之后）新增：
  ```python
  from app.services._odbc import access_conn_str, is_windows
  ```
- 在 `_create_empty_mdb` 定义**之前**新增：
  ```python
  def _ensure_mdb_export_supported() -> None:
      """MDB 写入依赖 ADOX/ACE OLEDB + Access ODBC 驱动，仅 Windows 可用。"""
      if not is_windows():
          raise RuntimeError(
              "MDB 导出仅支持 Windows（ADOX/ACE OLEDB + Access ODBC 写入）；"
              "当前平台不支持，请改用 GeoJSON/Shapefile/GeoPackage/CSV。"
          )
  ```
- `export_mdb` 函数体首行（第 618 行 docstring 之后）插入：
  ```python
      _ensure_mdb_export_supported()
  ```
- `_drop_all_user_tables`（第 594-597 行）：
  ```python
      conn = pyodbc.connect(
          r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + path,
          autocommit=True,
      )
  ```
  改为：
  ```python
      conn = pyodbc.connect(access_conn_str(path), autocommit=True)
  ```
- `export_mdb`（第 635-638 行）：`pyodbc.connect(r"DRIVER={...};DBQ=" + mdb_path, autocommit=True)` → `pyodbc.connect(access_conn_str(mdb_path), autocommit=True)`。

**Step 4 平台无关化既有测试**
- `tests/test_export_service_branches.py`：给 `test_create_empty_mdb_adox_fallback`(165) 与 `test_create_empty_mdb_import_error_raises`(180) 各加装饰器（文件顶部已 `import sys`）：
  ```python
      @pytest.mark.skipif(sys.platform != "win32", reason="ADOX/win32com 仅 Windows")
  ```
  理由：这两例本质 Windows-only（`patch("win32com.client.Dispatch")` 依赖已安装 pywin32），在 Linux CI 上必然失败；此前为潜在红。守卫设在 `export_mdb` 而非 `_create_empty_mdb`，故模板复制用例（197）不受影响。
- `tests/test_data_download_export.py`：找到调用 `export_mdb` 的用例（第 195 行附近 monkeypatch `_create_empty_mdb`），在其自签名参数中加入 `monkeypatch` 并插入：
  ```python
      monkeypatch.setattr(export_service, "is_windows", lambda: True)
  ```
  若用例签名不含 `monkeypatch`，则补上；`import app.services.export_service as export_service` 若缺则补。执行时用 Step 5 的回归确认无遗漏。

**Step 5 回归**：`python -m pytest tests/test_export_mdb_platform.py tests/test_export_service_branches.py tests/test_data_download_export.py -q` → 全绿（本机 win32）。

**Step 6 提交**
```
feat(docker): MDB 导出加平台守卫并收敛连接串
```

---

## Task 4 — dwg_converter 平台感知定位

**Files**
- Modify: `app/services/dwg_converter.py`
- Modify: `tests/test_dwg_converter.py`

**Step 1 改 `dwg_converter.py`**
- 顶部（`import tempfile` 后）新增 `import sys`。
- 第 23-27 行保留 `_TOOLS_DIR`/`_DWG2DXF`/`CONVERT_TIMEOUT_SECONDS`，在其后新增：
  ```python
  def _resolve_dwg2dxf() -> str | None:
      """定位 dwg2dxf 可执行文件，找不到返回 None。

      优先级：
      1) 模块属性 `_DWG2DXF` 指向真实文件（打包的 win64 exe / 测试覆盖）；
      2) Windows：仅用打包工具，忽略 PATH（避免误用未知版本）；
      3) POSIX：系统安装（apt/源码 install 后位于 PATH）。
      """
      if _DWG2DXF.is_file():
          return str(_DWG2DXF)
      if sys.platform == "win32":
          return None
      return shutil.which("dwg2dxf")
  ```
- `is_dwg_converter_available()` 函数体：
  ```python
      return _DWG2DXF.is_file()
  ```
  改为：
  ```python
      return _resolve_dwg2dxf() is not None
  ```
- `dwg_to_dxf`：把「工具缺失」检查与命令/cwd 改为平台感知：
  ```python
      exe = _resolve_dwg2dxf()
      if exe is None:
          logger.warning("LibreDWG 未部署（dwg2dxf 不可用），跳过 DWG 转换")
          return None

      workdir = tempfile.mkdtemp(prefix="dwg_conv_")
      dxf_path = os.path.join(workdir, "out.dxf")
      try:
          proc = subprocess.run(
              [exe, "-y", "-o", dxf_path, str(dwg_path)],
              capture_output=True,
              text=True,
              errors="replace",
              timeout=timeout,
              cwd=str(_TOOLS_DIR) if sys.platform == "win32" else None,
          )
  ```
  （其余分支逻辑不变。）

**Step 2 平台无关化 `tests/test_dwg_converter.py`**
- `test_converter_missing_returns_none`(45) 与 `test_false_when_missing`(39)：在 monkeypatch `_DWG2DXF` 之外，追加 `monkeypatch.setattr(dc.shutil, "which", lambda *a, **k: None)`，保证 POSIX 分支确定性返回 None。
- `test_success_returns_dxf_path`(50)：cwd 断言(65)改为：
  ```python
          assert calls["cwd"] == (str(dc._TOOLS_DIR) if sys.platform == "win32" else None)
  ```
  并在文件顶部 `import sys`。
- 列位置断言(64)：`assert str(dc._DWG2DXF) in str(calls["cmd"][0])` 仍成立（`_DWG2DXF` 指向刚创建的 fake exe）。

**Step 3 回归**：`python -m pytest tests/test_dwg_converter.py -q` → 全绿。

**Step 4 提交**
```
feat(docker): dwg2dxf 定位改为平台感知
```

---

## Task 5 — `docker/entrypoint.sh`（连接串派生 + secrets + 目录）

**Files**
- Create: `docker/entrypoint.sh`

**Step 1 内容**
```sh
#!/bin/sh
# 通用入口：派生 DB 连接串 → 装载 secrets → 建数据目录 → exec 实际命令。
# 设计：非敏感项走 .env；敏感项优先 /run/secrets/*（存在才覆盖，保证本地/CI 无 secrets 仍可跑）。
set -eu

_read_secret() {
    # $1=secret 文件名  $2=目标 env 名；文件存在则以文件内容覆盖
    if [ -f "/run/secrets/$1" ]; then
        _v="$(cat "/run/secrets/$1")"
        export "$2=$_v"
    fi
}

# ── secrets → env ────────────────────────────────────────────────
_read_secret postgres_password POSTGRES_PASSWORD
_read_secret admin_init_key     ADMIN_INIT_KEY
_read_secret flower_basic_auth  FLOWER_BASIC_AUTH

# ── 由 POSTGRES_* 派生 DATABASE_URL（单一真源）─────────────────────
# 容器内 POSTGRES_HOST 恒有值 → **始终**派生，覆盖 env_file(.env) 注入的 localhost 连接串。
if [ -n "${POSTGRES_HOST:-}" ]; then
    : "${POSTGRES_PORT:=5432}"
    : "${POSTGRES_USER:=postgres}"
    : "${POSTGRES_DB:=vcs_db}"
    : "${POSTGRES_PASSWORD:=postgres}"
    export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
    export DATABASE_URL_SYNC="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

# ── 数据目录（命名卷首挂为空，兜底建目录）─────────────────────────
if [ -d /data ]; then
    mkdir -p "${UPLOAD_DIR:-/data/uploads}" \
             "${OUTPUT_DIR:-/data/outputs}" \
             "${QUALITY_REPORT_DIR:-/data/quality_reports}" \
             "$(dirname "${LOG_FILE:-/data/logs/vcs.log}")" 2>/dev/null || true
fi

exec "$@"
```
> 注意：作为 `/usr/local/bin/entrypoint.sh` 部署，LF 行尾（见 Task 10 的 `.gitattributes` 说明）。

**Step 2 语法检查**：`bash -n docker/entrypoint.sh` → 无输出即通过。（本机若无 bash，记录并在 CI/target 执行。）

**Step 3 提交**
```
feat(docker): 新增 entrypoint（DB 派生/secrets/目录）
```

---

## Task 6 — `docker/scripts/gen_secrets.sh` 与 `gen_certs.sh`

**Files**
- Create: `docker/scripts/gen_secrets.sh`
- Create: `docker/scripts/gen_certs.sh`

**Step 1 `gen_secrets.sh`**
```sh
#!/bin/sh
# 生成生产 secrets（幂等：已存在则跳过）。用法: sh docker/scripts/gen_secrets.sh
set -eu
DIR="${1:-secrets}"
mkdir -p "$DIR"

_gen() {  # $1=文件名
    f="$DIR/$1"
    if [ -f "$f" ]; then echo "skip   $f (已存在)"; return; fi
    openssl rand -base64 48 | tr -d '\n' > "$f"
    echo "create $f"
}

_gen postgres_password.txt
_gen admin_init_key.txt
_gen flower_basic_auth.txt   # 生成后请手工改为 user:password 形式
chmod 600 "$DIR"/*.txt
echo "完成。注意: flower_basic_auth.txt 需为 user:password 格式，请自行编辑。"
```
> `ADMIN_INIT_KEY` 需避开弱口令集合（`app/config.py:7`），`openssl rand -base64 48` 满足。

**Step 2 `gen_certs.sh`**
```sh
#!/bin/sh
# 生成自签证书（内网单机，无域名）。用法: sh docker/scripts/gen_certs.sh [CN]
set -eu
DIR="${1:-certs}"
CN="${2:-vcs.local}"
mkdir -p "$DIR"
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$DIR/vcs.key" -out "$DIR/vcs.crt" \
    -subj "/C=CN/O=VCS/CN=${CN}" \
    -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:127.0.0.1"
chmod 600 "$DIR/vcs.key"
echo "已生成 $DIR/vcs.crt 与 $DIR/vcs.key（CN=${CN}）"
```

**Step 3 语法检查**：`bash -n docker/scripts/gen_secrets.sh; bash -n docker/scripts/gen_certs.sh`。

**Step 4 提交**
```
feat(docker): 新增密钥与自签证书生成脚本
```

---

## Task 7 — 重写 `Dockerfile`

**Files**
- Modify: `Dockerfile`（整体重写）

**Step 1 内容**
```dockerfile
# syntax=docker/dockerfile:1.7

# ── LibreDWG 构建阶段（Debian bookworm 无 libredwg-utils 包，须源码构建）──
FROM debian:bookworm-slim AS libredwg-builder
ARG LIBREDWG_VERSION=0.13.3
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl pkg-config libpcre2-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/libredwg.tar.gz \
        "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.gz" \
    && mkdir -p /src && tar -xzf /tmp/libredwg.tar.gz -C /src --strip-components=1 \
    && cd /src && ./configure --disable-bindings --disable-docs --disable-dependency-tracking \
    && make -j"$(nproc)" \
    && make install DESTDIR=/out

# ── 运行时基础阶段 ────────────────────────────────────────────────────────────
FROM python:3.11.11-slim AS base

# GDAL 运行时 + ODBC/MDB + LibreDWG 运行依赖 + 构建工具（构建后移除）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev libgeos-dev libproj-dev libpq-dev \
        unixodbc unixodbc-dev mdbtools libmdbodbc1 libpcre2-8-0 \
        gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal \
    PYTHONUNBUFFERED=1

# LibreDWG 运行时（dwg2dxf + libredwg.so）
COPY --from=libredwg-builder /out/usr/local/ /usr/local/
RUN ldconfig

# MDBTools ODBC 驱动注册（pyodbc 经 odbcinst.ini 解析 DRIVER={MDBTools}）
RUN printf '[MDBTools]\nDescription=MDB Tools ODBC\nDriver=/usr/lib/x86_64-linux-gnu/odbc/libmdbodbc.so\n' \
        > /etc/odbcinst.ini \
    && dwg2dxf --version 2>/dev/null || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc g++ libgdal-dev unixodbc-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p /data/uploads /data/outputs /data/quality_reports /data/logs \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app /data \
    && chmod +x /app/docker/entrypoint.sh \
    && cp /app/docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER appuser
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# ── API 服务入口 ───────────────────────────────────────────────────────────────
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]

# ── Celery Worker 入口 ────────────────────────────────────────────────────────
FROM base AS worker
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=4", "-Q", "vector_conversion,celery"]

# ── Celery Beat 入口 ─────────────────────────────────────────────────────────
FROM base AS beat
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "beat", "--loglevel=info"]

# ── Flower 监控（shell 形式以展开 $FLOWER_BASIC_AUTH）──────────────────────────
FROM base AS flower
EXPOSE 5555
CMD ["sh", "-c", "celery -A app.workers.celery_app.celery_app flower --port=5555 --broker_api=redis://redis:6379/0 --basic-auth=$FLOWER_BASIC_AUTH"]
```

> 修复点：D2（Flower `$FLOWER_BASIC_AUTH` 在 exec 形式不展开 → 改 `sh -c`）、D3（新增 beat 阶段）、D4（LibreDWG + mdbtools）、D7（python 基础镜像固定 `3.11.11-slim`）。
> **构建期注意**：LibreDWG tag/URL 须在构建机校验存在（`curl -I` 200）；如 tag 变更，改 `ARG LIBREDWG_VERSION`。离线交付走「联网机 build → `docker save` → 目标机 `docker load`」。

**Step 2 本机可做**：静态核对（无 docker）。`docker build` 在目标/CI 执行。

**Step 3 提交**
```
feat(docker): Dockerfile 加 LibreDWG/mdbtools 并修 Flower/Beat
```

---

## Task 8 — 修复基线 `docker-compose.yml`

**Files**
- Modify: `docker-compose.yml`

**Step 1 逐项修改**
1. 删除首行 `version: "3.9"`（D6）。
2. `x-app-common.environment`（第 10-15 行）改为**去掉硬编码连接串**、新增 `POSTGRES_*` 透传：
   ```yaml
   environment: &app-env
     POSTGRES_HOST: postgres
     POSTGRES_PORT: "5432"
     POSTGRES_DB: ${POSTGRES_DB:-vcs_db}
     POSTGRES_USER: ${POSTGRES_USER:-postgres}
     POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
     REDIS_URL: redis://redis:6379/0
     CELERY_BROKER_URL: redis://redis:6379/0
     CELERY_RESULT_BACKEND: redis://redis:6379/1
     UPLOAD_DIR: /data/uploads
     OUTPUT_DIR: /data/outputs
     QUALITY_REPORT_DIR: /data/quality_reports
     LOG_FILE: /data/logs/vcs.log
   ```
3. `x-app-common.volumes`（第 16-18 行）改为：
   ```yaml
     volumes:
       - vector_uploads:/data/uploads
       - vector_outputs:/data/outputs
       - vector_reports:/data/quality_reports
       - vector_logs:/data/logs
   ```
   （D1：目录 env 与挂载路径一致；新增 reports/logs 卷。）
4. `migrate.command`（第 70-71 行）保持 shell 语义但显式化：
   ```yaml
     command: ["sh", "-c", "alembic upgrade head && python scripts/init_db.py"]
   ```
5. `beat`（第 126-131 行）：
   - `build.target: base` → `beat`（用新阶段）。
   - `command` 修正为：`["celery", "-A", "app.workers.celery_app.celery_app", "beat", "--loglevel=info"]`（D3：原缺 `.celery_app`，会直接崩）。
6. `flower.environment`（第 121-123 行）保留；其 CMD 已由 Task 7 修。
7. 顶部 `volumes:`（第 140-144 行）新增 `vector_reports:` 与 `vector_logs:`。
8. `api.ports` 保留 `45679:8000`（与 `restart.bat` 一致）；`api.healthcheck` 保留 curl（base 镜像含 curl）。

**Step 2 校验**：`python -c "import yaml;yaml.safe_load(open('docker-compose.yml',encoding='utf-8'));print('ok')"`（缺 PyYAML 则 `python -m pip install pyyaml`）。
另在目标/CI：`docker compose config -q`。

**Step 3 提交**
```
fix(docker): 修 compose 卷路径/beat 命令并去硬编码连接串
```

---

## Task 9 — 生产覆盖层 + Nginx

**Files**
- Create: `docker-compose.prod.yml`
- Create: `docker/nginx/Dockerfile`
- Create: `docker/nginx/nginx.conf`
- Create: `docker/nginx/conf.d/vcs.conf`

**Step 1 `docker/nginx/nginx.conf`**
```nginx
user  nginx;
worker_processes auto;
error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" "$http_user_agent"';
    access_log  /var/log/nginx/access.log  main;
    sendfile        on;
    keepalive_timeout  65;
    client_max_body_size 512m;      # 与 MAX_UPLOAD_SIZE_MB 对齐（留余量）

    # TLS 全局
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    include /etc/nginx/conf.d/*.conf;
}
```

**Step 2 `docker/nginx/conf.d/vcs.conf`**
```nginx
# 80 → 443
server {
    listen 80;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/certs/vcs.crt;
    ssl_certificate_key /etc/nginx/certs/vcs.key;

    # 前端静态（COPY app/frontend → /usr/share/nginx/html/app/）
    root /usr/share/nginx/html;
    location = / { return 302 /app/; }
    location /app/ { try_files $uri $uri/ /app/index.html; }

    # API
    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_request_buffering off;   # 大文件上传流式
    }

    # WebSocket
    location /ws {
        proxy_pass http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    # 文档与健康检查
    location = /health     { proxy_pass http://api:8000/health; }
    location /docs         { proxy_pass http://api:8000/docs; }
    location /redoc        { proxy_pass http://api:8000/redoc; }
    location /openapi.json { proxy_pass http://api:8000/openapi.json; }
}
```

**Step 3 `docker/nginx/Dockerfile`**
```dockerfile
FROM nginx:1.27-alpine
COPY docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx/conf.d/ /etc/nginx/conf.d/
COPY app/frontend/ /usr/share/nginx/html/app/
```
> 构建上下文为仓库根，故 `docker/nginx/...` 与 `app/frontend/` 路径可用。

**Step 4 `docker-compose.prod.yml`**
```yaml
# 生产覆盖层：与 docker-compose.yml 叠加使用
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# 变更：api 不外发端口（仅经 nginx）、secrets 文件、资源限额、日志轮转、只读根。
x-app-common-prod: &app-common-prod
  secrets:
    - postgres_password
    - admin_init_key
  logging:
    driver: json-file
    options: { max-size: "10m", max-file: "5" }
  deploy:
    resources:
      limits: { cpus: "4", memory: 4g }   # compose v2 非 Swarm 下需 `docker compose --compatibility` 才生效

services:
  api:
    <<: *app-common-prod
    ports: !reset []               # 真正清空基线 45679:8000（见下：ports 是「拼接」语义）
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password

  worker:
    <<: *app-common-prod
  beat:
    <<: *app-common-prod
  migrate:
    <<: *app-common-prod
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
  flower:
    secrets:
      - flower_basic_auth
    ports: !reset []               # 经 nginx 或内网直连另配；默认不外发

  postgres:
    environment:
      POSTGRES_PASSWORD: !reset null   # 必须清掉基线明文，否则官方镜像 file_env 报错退出
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    ports: !reset []               # 生产不外发数据库端口
    secrets:
      - postgres_password

  redis:
    ports: !reset []               # 生产不外发

  nginx:
    build:
      context: .
      dockerfile: docker/nginx/Dockerfile
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./certs:/etc/nginx/certs:ro
    networks:
      - vcs_net
    restart: unless-stopped

secrets:
  postgres_password: { file: ./secrets/postgres_password.txt }
  admin_init_key:    { file: ./secrets/admin_init_key.txt }
  flower_basic_auth: { file: ./secrets/flower_basic_auth.txt }
```
> `POSTGRES_PASSWORD_FILE` 由 postgres 官方镜像原生支持。**注意**：官方镜像的 `file_env` 在
> `POSTGRES_PASSWORD` 与 `POSTGRES_PASSWORD_FILE` **同时存在时会报错并退出**（不是以 FILE 为准！），
> 所以 prod 里必须用 `POSTGRES_PASSWORD: !reset null` 清掉基线继承的明文来源。应用侧则由 entrypoint
> 从 `/run/secrets/postgres_password` 读取并覆盖 env，无此限制。
>
> **关键合并语义（两条，均已实测于官方文档）**：
> ① `ports`/`expose`/`dns`… 是 **list 拼接**语义，不是替换 —— 故 prod 用 `ports: []` **无法**移除基线端口，
>    必须用 compose-spec 的 `!reset []` YAML 标签（需 Compose ≥ 2.24）。
> ② `environment`/`volumes`/`secrets` 等 **map/list 深合并**，故 prod 中 `api.environment` 只写
>    `POSTGRES_PASSWORD_FILE` 不会丢掉基线锚点的 `POSTGRES_HOST/UPLOAD_DIR/...`；文件内 `<<: *anchor`
>    是 YAML 浅合并（键级覆盖）——因此 `x-app-common-prod` 锚点**不得**含 `environment`/`build`。
> 执行时务必以目标机 `docker compose config` 展开结果核对（本机无 docker 无法验证）。

**Step 5 校验**：prod 含 `!reset` 自定义标签，`yaml.safe_load` 会报错，需注册多构造器忽略 `!`：
`python -c "import yaml;L=yaml.SafeLoader;L.add_multi_constructor('!',lambda l,t,n: None);yaml.load(open('docker-compose.prod.yml',encoding='utf-8'),Loader=L);print('ok')"`。
目标/CI：`docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q`。

**Step 6 提交**
```
feat(docker): 新增生产覆盖层与 Nginx 反代(自签TLS)
```

---

## Task 10 — `.dockerignore` / env 模板 / Makefile

**Files**
- Modify: `.dockerignore`
- Modify: `.env.example`
- Create: `.env.prod.example`
- Create: `Makefile`
- Create/Modify: `.gitattributes`（脚本 LF）

**Step 1 `.dockerignore` 追加**
```
# Offline deploy & docs & vendored tools（构建不需要）
deploy/
offline/
docs/
tools/
Redis8/
secrets/
certs/
*.log
```
（保留既有条目；`*.md` 已排除、`!README.md` 例外保留。）

**Step 2 `.env.example` 补目录说明**（在「应用」段 `OUTPUT_DIR` 附近追加注释，不改默认值）：
```
# 容器内（docker-compose）会被覆盖为 /data/... 绝对路径；本地开发保持 ./data/...
# 预览目录不在配置中（由 UPLOAD_DIR 的父目录 + /previews 派生）
```

**Step 3 `.env.prod.example`**
```
# 生产 env 模板（敏感项在 secrets/*.txt，不在此文件）
#   cp .env.prod.example .env 后：sh docker/scripts/gen_secrets.sh && sh docker/scripts/gen_certs.sh
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=["https://vcs.local"]
MAX_UPLOAD_SIZE_MB=500
POSTGRES_DB=vcs_db
POSTGRES_USER=vcs
# POSTGRES_PASSWORD 由 secrets/postgres_password.txt 提供
```

**Step 4 `Makefile`**（tab 缩进）
```makefile
# VCS 生产部署 Makefile（Linux 目标机）
COMPOSE      = docker compose
COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help secrets certs build up down logs ps prod-up prod-down migrate restart clean
help:
	@echo "secrets certs build up down logs ps prod-up prod-down migrate restart clean"

secrets:      ; sh docker/scripts/gen_secrets.sh
certs:        ; sh docker/scripts/gen_certs.sh
build:        ; $(COMPOSE) build
up:           ; $(COMPOSE) up -d
down:         ; $(COMPOSE) down
logs:         ; $(COMPOSE) logs -f --tail=200
ps:           ; $(COMPOSE) ps
prod-up:      ; $(COMPOSE_PROD) up -d --build
prod-down:    ; $(COMPOSE_PROD) down
migrate:      ; $(COMPOSE) run --rm migrate
restart:      ; $(COMPOSE) restart api worker beat
clean:        ; $(COMPOSE_PROD) down -v
```

**Step 5 `.gitattributes`**（确保脚本 LF，避免 Windows CRLF 进入镜像导致 `bad interpreter`）：
```
*.sh text eol=lf
Makefile text eol=lf
docker/** text eol=lf
```

**Step 6 提交**
```
chore(docker): 补 dockerignore/env 模板/Makefile 与 LF 约束
```

---

## Task 11 — CI 增补 + 版本与文档同步

**Files**
- Modify: `.github/workflows/ci.yml`
- Modify: `app/main.py`（version）
- Modify: `CONTEXT.md`、`BASELINE.md`
- Create: `docs/revision_v2_24_0.md`
- Modify: `README.md`

**Step 1 CI 增补**（在 `smoke` job 之后新增 job）
```yaml
  prod-config:
    runs-on: ubuntu-latest
    needs: [test]
    steps:
      - uses: actions/checkout@v4
      - name: Validate prod compose
        run: |
          cp .env.example .env
          sh docker/scripts/gen_secrets.sh || true
          echo "admin:pass" > secrets/flower_basic_auth.txt
          docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q
      - name: Shellcheck scripts
        run: |
          bash -n docker/entrypoint.sh
          bash -n docker/scripts/gen_secrets.sh
          bash -n docker/scripts/gen_certs.sh
```
> 说明：`config -q` 仅校验语法/引用，不拉镜像，快速失败。

**Step 2 版本号**
- `app/main.py:67` → `version="2.24.0",`
- `CONTEXT.md` 顶部版本 → `v2.24.0`，概述一行「Docker 生产加固（Nginx/secrets/LibreDWG+mdbtools Linux 适配）」。
- `BASELINE.md` 顶部「最后更新」改 `2026-10-13 · v2.24.0 …`；按 CLAUDE.md 第 2/3 节更新清单核对：§2 目录结构（新增 `docker/`、`docker-compose.prod.yml`、`Makefile`、`.env.prod.example`、`_odbc.py`、测试）、§5 服务模块（`_odbc`）、§6 脚本（`gen_secrets.sh`/`gen_certs.sh`/`entrypoint.sh`）、§15 版本管理。

**Step 3 `docs/revision_v2_24_0.md`**（新建，追加式）：记录 D1–D7、C1 适配、Nginx/secrets/Makefile、涉及文件与验证方式（本机 pytest + 目标机 docker）。

**Step 4 `README.md`**：新增「Docker 生产部署」小节——`gen_secrets` → `gen_certs` → `make prod-up` → 访问 `https://<host>/app/`；离线交付 `docker save`/`load` 说明。

**Step 5 全量回归**：`python -m pytest tests/ -q`（期望 ~1783 passed，含新增），覆盖率门限 `--cov-fail-under=25` 满足。

**Step 6 提交**
```
docs(docker): v2.24.0 版本与部署文档同步
```

---

## 验收清单（跨任务）

- [ ] `pytest tests/` 全绿（Windows 本机）。
- [ ] `python -c "import yaml;yaml.safe_load(open('docker-compose.yml'));yaml.safe_load(open('docker-compose.prod.yml'))"` 通过。
- [ ] `bash -n` 三个脚本通过。
- [ ] 目标 Linux 机：`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` 全服务 healthy。
- [ ] 目标 Linux 机：`curl -k https://localhost/health` 200；`https://localhost/app/` 返回 SPA。
- [ ] 目标 Linux 机：容器内 `dwg2dxf --version` 有输出；MDB 读取成功、MDB 导出给出「仅支持 Windows」明确错误。
- [ ] secrets 生效：容器内 `echo $ADMIN_INIT_KEY` 非空且等于 `secrets/admin_init_key.txt`。

## 风险与回滚

- **LibreDWG 源码构建失败**（网络/tag/依赖）→ 影响主要在 DWG 能力；应用仍有 DXF/其他格式降级（`dwg_to_dxf` 返回 None），不阻断起服。回滚：移除 builder 阶段与 COPY。
- **compose `secrets` 需 Compose ≥2.23**；低版本需改用 env 注入（保留 `.env.prod.example` 路径）。
- **卷改名**（新增 reports/logs 卷）→ 老数据在 `vector_uploads/vector_outputs` 保留不变，无迁移风险。
- **`.dockerignore` 排除 `tools/`** → 确认无代码在构建期依赖 `tools/`（当前 tools 仅 Windows dwg exe，Linux 走 PATH）。
