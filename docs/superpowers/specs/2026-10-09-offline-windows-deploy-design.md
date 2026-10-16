# 设计规格：Windows 离线一键部署（deploy 子系统）

- 文档类型：设计规格（Design Spec，brainstorming 产物）
- 创建日期：2026-10-09
- 状态：已评审通过（用户确认），待转实施计划（writing-plans）
- 关联版本：v2.22.3 之后（部署能力增强，不改变业务行为）

---

## 1. 背景与问题

项目（VCS 空间质检微服务）当前 Windows 部署依赖联网与手工步骤，无法在**完全离线**环境落地：

| 现状问题 | 影响 |
|---|---|
| `deploy_windows.bat` 中文注释乱码（编码/换行不一致） | 可读性差，实施人员误操作 |
| `pip install -r requirements.txt` 直连 PyPI | 离线机失败 |
| 引用 `scripts\install_postgres.ps1`，该脚本**从 enterprisedb 联网下载** PG，且**不安装 PostGIS** | 离线失败；PostGIS 缺失导致空间功能不可用 |
| `scripts\start_redis.bat` 假设 `C:\Program Files\Redis`，**未使用项目自带 `Redis8/`** | 目标机无 Redis 时启动失败 |
| `tools/libredwg/` 被 `.gitignore` 排除 | 整目录拷贝交付时易遗漏，DWG 转换失效 |
| 无离线安装程序、无依赖 wheel 缓存、无种子数据 | 无法"开箱即用" |

## 2. 目标与非目标

### 目标
- 在**无网络**的 Win x64 机器上，**有管理员权限**、**目录任意**（路径自适应），通过**一次入口调用**完成：环境安装 → 依赖 → 数据库 → 种子 → 服务启动 → 开箱即用。
- 交付终点（验收等级**丙**）：预置**参照标准/字典种子数据**，安装后 API、Celery、前端、空间与导入导出功能均可用。
- 全程**幂等可重入**、**SHA256 校验**、**阶段化日志**、**可断点重跑**。

### 非目标（本期不做）
- 不做 Linux 离线包（`deploy_linux.sh` 保持不变，不在本期范围）。
- 不做 Docker 交付（方案 C 已排除）。
- 不做静默卸载/回滚安装器（仅提供服务停止与重装的排障指引）。
- 不改变任何业务代码与 API 行为。

## 3. 版本矩阵（锁定）

| 组件 | 版本 | 来源 | 静默安装要点 |
|---|---|---|---|
| Python | 3.13.3 | 自带 `python-3.13.3-amd64.exe` | `InstallAllUsers=1 PrependPath=1 Include_pip=1` |
| PostgreSQL | 15.15 | 自带 `postgresql-15.15-2-windows-x64.exe` | `--mode unattended --superpassword <pwd>`，服务名 `postgresql-x64-15` |
| PostGIS | 3.6.1（bundle pg15） | 自带 `postgis-bundle-pg15x64-setup-3.6.1-1.exe` | 静默装入 PG15，`CREATE EXTENSION` 时生效 |
| Redis | 8（项目 `Redis8/`） | 项目内 | 优先注册 Windows 服务，fallback 前台进程 |
| LibreDWG | 项目 `tools/libredwg/` | 项目内 | 复制进 `deploy/offline/libredwg/` |
| VC++ 运行库 | AIO 2019 x64 | 自带 `MSVBCRT_AIO_..._X64.exe` | `/quiet /norestart` |
| Access Database Engine | ACE 2016 x64 | **联网机下载进离线包** | `/quiet`（失败告警不中断） |
| Python 依赖 | cp313 / win_amd64 wheels | 联网机 `pip download` 预取 | `--no-index --find-links` |

> 偏差记录：开发机 PostgreSQL 为 16.4，交付为 15.15。实施阶段须核查迁移脚本与原生 SQL 不含 PG16 专属特性（见 §9 验证）。PostGIS 文档记 3.4，bundle 实为 3.6.1（向后兼容）。

## 4. 目录布局

```
空间质检/
├── deploy/
│   ├── README.md                # 面向实施人员的交付/安装/排障说明
│   ├── deploy_offline.bat       # 入口：自提升管理员 → 调 PS 主控
│   ├── build_bundle.ps1         # 联网机构建/刷新 offline 资源（一次性）
│   ├── offline/                 # 离线资源（随包交付，git 忽略）
│   │   ├── manifest.json        #   文件名 / 大小 / SHA256
│   │   ├── installers/          #   python / PG15 / PostGIS / ACE / VC++
│   │   ├── redis/               #   从 Redis8/ 复制
│   │   ├── libredwg/            #   从 tools/libredwg/ 复制
│   │   ├── wheels/              #   cp313 全量 wheel
│   │   └── seed/                #   参照标准种子包（v2 ZIP）
│   └── scripts/
│       ├── common.ps1               # 日志/哈希/检测/幂等 辅助函数
│       ├── 00_preflight.ps1
│       ├── 10_install_python.ps1
│       ├── 20_install_postgres.ps1
│       ├── 21_install_postgis.ps1
│       ├── 22_install_ace.ps1
│       ├── 25_install_vcredist.ps1
│       ├── 30_setup_redis.ps1
│       ├── 40_setup_venv.ps1
│       ├── 50_init_database.ps1
│       ├── 60_seed_and_key.ps1
│       ├── 90_start_services.ps1
│       └── 99_verify.ps1
```

**版本控制**：`deploy/offline/` 体积约 1.1–1.2 GB，加入 `.gitignore`（沿用 `tools/libredwg` 处置），但**物理上随整目录交付**；由 `build_bundle.ps1` 生成。脚本与文档（`deploy/*.bat`、`deploy/*.ps1`、`deploy/scripts/*`、`deploy/README.md`）纳入版本控制。

## 5. 部署流程（阶段，可单独重跑）

| 阶段 | 动作 | 幂等判据（满足即 SKIP） |
|---|---|---|
| 00 preflight | 校验管理员 / x64 / 磁盘 ≥ 3GB / manifest SHA256 | — |
| 10 python | 静默装 Python 3.13.3（AllUsers + PrependPath） | `py -3.13 --version` 可用 |
| 20 postgres | 静默装 PG15，服务 `postgresql-x64-15` | 服务已存在 |
| 21 postgis | 静默装 PostGIS bundle 进 PG15 | PG15 目录存在 `postgis.control` |
| 22 ace | 静默装 ACE 2016 x64 | 注册表存在 ACE 驱动条目 |
| 25 vcredist | 静默装 VC++ AIO | VC143 运行库已注册 |
| 30 redis | 用自带 redis 注册服务（fallback 前台进程） | 服务 `Redis` 运行中 |
| 40 venv | 建 `.venv` → 离线装依赖 → `pywin32_postinstall` | `.venv\Scripts\uvicorn.exe` 存在 |
| 50 db | 建库 `vcs_db` + `CREATE EXTENSION postgis` + `alembic upgrade head` | `alembic_version` = head |
| 60 seed+key | 写 `.env` → 建首个管理员 Key（打印）→ 导入种子标准 | `.env` 存在且 Key 已建 |
| 90 start | 起 API + Celery（+Flower）→ `/health` 自检 → 开浏览器 | 端口监听 + health ok |
| 99 verify | 汇总报告（各组件版本 / 服务状态 / 入口 URL / 打印的 Key） | — |

执行方式：`deploy_offline.bat` 调 `deploy/scripts/` 顺序执行；每阶段独立、可 `--from <阶段号>` 续跑。

## 6. 健壮性设计

- **幂等**：每阶段先检测是否已满足，满足即打印 `SKIP` 并继续；重复运行整体安全。
- **校验**：`manifest.json` 记录每个资源文件的 SHA256；00 阶段逐项校验，损坏即报错并定位文件。
- **日志**：`deploy/logs/deploy_<yyyyMMdd_HHmmss>.log`，每阶段记录 `BEGIN / END / 耗时 / 结果`。
- **重跑**：`deploy_offline.bat --from 40` 从指定阶段续跑；`--dry-run` 仅检测不安装。
- **自提升**：入口检测管理员，非管理员则以 UAC 重新拉起自身。
- **失败策略**：关键阶段（10/20/30/40/50/60/90）失败即中断并给出排障提示；非关键阶段（22 ACE / 25 VC++）失败**告警但不中断**（对应功能降级）。

## 7. 数据库 / 密钥 / 种子（验收=丙）

- **建库**：`CREATE DATABASE vcs_db;` → `CREATE EXTENSION postgis;` → `alembic upgrade head`（复用项目 alembic）。
- **密钥**：生成强随机 `ADMIN_INIT_KEY` 写入 `.env` 并**醒目打印**；调用建 Key 接口生成首个可用 API Key 并**打印**（供首次登录/调用）。
- **种子**：把**本机全部参照标准**经现有 v2 导出端点导出为 ZIP 归档，存 `offline/seed/`；60 阶段自动导入（若种子为空则跳过并提示）。
- **`.env`**：写入 `DATABASE_URL`、`REDIS_URL`、`ADMIN_INIT_KEY` 等（依据 `.env.example`）。

## 8. 交付与体积

- 交付物 = **整个 `空间质检/` 目录**（含 `deploy/`）。实施人员整目录拷贝至目标机，双击 `deploy\deploy_offline.bat`。
- 体积预算：installers ≈ 576 MB + `redis` 37 MB + `libredwg` 35 MB + `wheels` ≈ 450 MB + `seed`（视数据量）。**合计 ≈ 1.1–1.2 GB**。

## 9. 验证策略

- **构建侧**：`pip download --only-binary=:all:` 确保依赖全 wheel 化；`--dry-run` 全阶段体检；逐项验证各安装器静默参数（重点 PostGIS / ACE）。
- **安装侧**：在隔离目录/干净账号模拟目标机跑通全流程；侧重验证幂等（二次运行全 SKIP）。
- **兼容性**：核查迁移脚本与原生 SQL 无 PG16 专属特性（开发 16 vs 交付 15）。
- **冒烟**：安装后验证 `/health`、前端 `/app/`、导入导出一条链路、Celery `inspect ping`。

## 10. 默认决策（用户已确认）

1. **PG 超级用户口令**：部署时由用户输入（提供脚本内置默认值的 fallback）。
2. **Flower 监控（:5555）**：默认**包含**。
3. **服务端口**：保持 `45679`。
4. **ACE / pywin32 静默安装失败**：默认**告警但不中断**（MDB 功能降级，其余正常）。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| PostGIS bundle 静默参数与目标 PG15 路径不匹配 | 构建侧先验证参数；失败给出明确日志 |
| `pip download` 存在仅 sdist 的包，离线安装需编译 | 用 `--only-binary=:all:` 预检并锁定；确保 `setuptools/wheel` 就绪 |
| 开发/交付 PG 主版本差（16 vs 15） | §9 兼容性核查；必要时对齐 |
| 目标机已有其他 Redis / 端口占用 | 30 阶段检测并给出提示；支持复用现有 Redis |
| ACE 静默安装需要先装/后装顺序 | 独立阶段 + 失败降级，不阻塞主流程 |
| 整目录拷贝遗漏 `deploy/offline`（体积大） | README 明确清单；00 阶段校验 manifest 缺项即报错 |
