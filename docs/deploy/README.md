# 部署（Deploy）文件索引

> 本目录仅做索引说明，实际部署脚本与离线资源包均保留在项目 `deploy/` 目录中，不做移动，以保持现有脚本与一键部署流程稳定。
> 本文件位于 `docs/deploy/`，故指向**仓库根**的文件需 `../../`，指向 **`docs/`** 的文件需 `../`。
> Windows 离线部署的**运维手册**（交付、安装、排障、凭证）见 [`../../deploy/README.md`](../../deploy/README.md)，本文件只做文件索引。

## 根目录一键部署入口

| 文件 | 说明 |
|---|---|
| `../../deploy_windows.bat` | Windows 部署脚本（v2.23.0 起薄封装，转调 `deploy\deploy_offline.bat`） |
| `../../deploy_linux.sh` | Linux 部署脚本 |
| `../../restart.bat` | Windows 一键重启脚本 |
| `../../restart.sh` | Linux 一键重启脚本 |

## `deploy/` 目录（Windows 离线一键部署子系统 v2.23.0）

> 下表路径**相对仓库根的 `deploy/` 目录**（即实际位置为 `deploy/<名称>`）。

### 入口与编排

| 文件 | 说明 |
|---|---|
| `deploy_offline.bat` | 离线部署入口（UAC 自提升，支持 `--from N` 幂等续跑、`--dry-run` 干跑） |
| `deploy_silent.bat` | 静默部署入口（若存在） |
| `build_bundle.ps1` | 联网机离线资源包生成器（生成离线资源包并写入 `offline/manifest.json`，逐文件 SHA-256 校验） |
| `README.md` | Windows 离线一键部署子系统说明文档（部署/交付/排障） |

### 阶段脚本（12 阶段编排）

| 文件 | 说明 |
|---|---|
| `scripts/common.ps1` | 通用函数库（日志、校验、环境检测等） |
| `scripts/deploy.ps1` | 编排器（按阶段顺序调用各脚本） |
| `scripts/00_preflight.ps1` | 00：预检（清单校验、环境检查） |
| `scripts/10_install_python.ps1` | 10：安装 Python 3.13.3 |
| `scripts/20_install_postgres.ps1` | 20：安装 PostgreSQL 15.15 |
| `scripts/21_install_postgis.ps1` | 21：安装 PostGIS 3.6.1 |
| `scripts/22_install_ace.ps1` | 22：安装 ACE（非关键） |
| `scripts/25_install_vcredist.ps1` | 25：安装 VC++2019（非关键） |
| `scripts/30_setup_redis.ps1` | 30：设置 Redis（离线包自带二进制） |
| `scripts/40_setup_venv.ps1` | 40：创建虚拟环境并离线安装依赖 |
| `scripts/50_init_database.ps1` | 50：初始化数据库、运行 Alembic 迁移 |
| `scripts/60_seed_and_key.ps1` | 60：种子数据与初始密钥配置（落 `deploy/.install_state.json`） |
| `scripts/90_start_services.ps1` | 90：启动服务（uvicorn/celery 等） |
| `scripts/99_verify.ps1` | 99：部署后验证 |

### 离线资源包（联网机生成）

| 目录 | 说明 |
|---|---|
| `offline/` | 离线资源包根目录（由 `build_bundle.ps1` 生成） |
| `offline/installers/` | 安装包：Python 3.13.3、PostgreSQL 15.15、PostGIS 3.6.1、VC++2019、ACE2016 等 |
| `offline/redis/` | Redis 8.10 win64 二进制（`redis-server.exe`、`redis.conf`） |
| `offline/libredwg/` | LibreDWG win64 二进制（`dwg2dxf.exe` 等） |
| `offline/wheels/` | Python 离线 wheel（`cp313 win_amd64`，`--only-binary=:all:`） |
| `offline/seed/` | 数据标准种子 JSON（一厅八室参照物 `SHGT_*.json`） |
| `offline/manifest.json` | 资源清单（SHA-256 校验哈希） |

### 日志与自测

| 文件/目录 | 说明 |
|---|---|
| `logs/` | 部署日志目录（`deploy_YYYYMMDD_HHMMSS.log`） |
| `tests/run_tests.ps1` | 部署脚本自测（17 例） |

## 相关文档

- [Windows 离线一键部署子系统说明](../../deploy/README.md)（位于仓库根 `deploy/README.md`）
- [v2.23.0 修订记录](../revision_v2_23_0.md) —— Windows 离线一键部署子系统完整设计与实现
- [项目基准线](../../BASELINE.md) —— 部署子系统章节
- [Docker 运维手册](../docker_ops_manual_v2_24_0.md) —— 容器化部署路径

> **说明**：根目录 `deploy_windows.bat` 薄封装转调 `deploy\deploy_offline.bat`，保持向后兼容。Linux 路径详见 `deploy_linux.sh` 及项目说明。
