# 修订记录 v2.23.0 — Windows 离线一键部署子系统（deploy/）

> **日期**: 2026-10-09
> **前版**: v2.22.3（CSV 无损归档往返 + 字段-字典绑定模板往返）
> **性质**: 交付部署能力增强——新增 `deploy/` Windows 离线一键部署子系统；业务代码零改动、**无数据库变更**
> **验收**: **丙**（交付部署能力，不改变业务行为；线上验收以目标机真装为准）

---

## §零 本版要解决的问题

v2.22.3 及以前，Windows 部署靠 `deploy_windows.bat` 单脚本就地执行：依赖机器**联网**
（`pip install`、下载安装成果）和**人力记忆步骤**；对无法触网的目标机（政务/内网隔离）
完全不可用。本版将部署工作拆成「**联网机预构建离线资源包 → 目标机一键安装**」，
并把 PG 交付目标从「任意版本」钉死为 **PostgreSQL 15 + PostGIS 3.6.1**（与
v2.22.3 之前的开发环境 PG16 解耦，见 §五）。

旧入口 `deploy_windows.bat` 退役为**薄封装**，转调 `deploy\deploy_offline.bat`，
既有调用方零破坏。

---

## §一 方案与架构

### 双机角色

| 角色 | 机器 | 执行 | 产出/动作 |
|------|------|------|-----------|
| 构建机 | 联网 Windows | `deploy\build_bundle.ps1` | `deploy\offline\`（安装包 + Redis/LibreDWG 二进制 + 全量 wheel + 种子）+ `offline\manifest.json`（每文件 SHA-256 + 体量） |
| 目标机 | 离线/内网 Windows | `deploy\deploy_offline.bat` | 十二阶段编排安装，全程不联网 |

### 十二阶段编排（`deploy\scripts\deploy.ps1`）

`00_preflight`(校验 manifest 全量哈希+管理员) → `10_install_python` →
`20_install_postgres` → `21_install_postgis` → `22_install_ace`(**非关键**) →
`25_install_vcredist`(**非关键**) → `30_setup_redis` → `40_setup_venv`(离线 wheel) →
`50_init_database`(建库+PostGIS+alembic 迁移到 head) → `60_seed_and_key`(写 .env +
ADMIN_INIT_KEY + 种子导入, 凭证落 `deploy\.install_state.json`) →
`90_start_services`(uvicorn 45679 + celery worker + flower 5555) → `99_verify`。

### 关键设计

- **manifest 校验前置**：`00_preflight` 对 `offline/` 全部 127 个文件做 SHA-256 比对，
  任一路径缺失/被篡改立即失败，杜绝「拷了一半交付」。
- **UAC 自提升**：`deploy_offline.bat` 检测非管理员时 `Start-Process -Verb RunAs`
  自提权重启；也可右键以管理员运行。
- **幂等续跑**：`--from N` 跳过 `< N` 阶段；各阶段先探测现状（已装/已监听/venv 就绪
  则 SKIP）。重装 venv = 删 `.venv` + `--from 40`；重建库 = `--from 50`。
- **干跑**：`--dry-run` 只走检测/校验分支，不真装；常用于目标机交付前的体检。
- **失败分级**：ACE(`22`)/VC++(`25`) 属非关键，失败记 WARN 继续；其余阶段失败
  即中断。每阶段日志写 `deploy\logs\deploy_YYYYMMDD_HHMMSS.log`。
- **凭证落盘**：`ADMIN_INIT_KEY`/`API_KEY` 由 `60_seed_and_key` 生成并写
  `deploy\.install_state.json`，安装末尾打印；`99_verify` 再次回显。
- **PG 口令**：PG 超级用户口令经环境变量 `VCS_PG_SUPERPASSWORD` 注入，
  未设置回落到 `postgres`。

---

## §二 版本矩阵（交付目标）

| 组件 | 版本 | 来源 | manifest 键 |
|------|------|------|-------------|
| Python | 3.13.3 (win64) | `offline/installers/python-3.13.3-amd64.exe` | `python` |
| PostgreSQL | 15.15-2 (win64) | `offline/installers/postgresql-15.15-2-windows-x64.exe` | `postgres` |
| PostGIS | 3.6.1 (pg15 bundle) | `offline/installers/postgis-bundle-pg15x64-setup-3.6.1-1.exe` | `postgis` |
| Redis | 8.10.0 (win64) | `offline/redis/redis-server.exe`（自 `Redis8/` 收集，实测 `redis-server --version` 输出 `v=8.10.0`） | — |
| LibreDWG | 0.14 (win64) | `offline/libredwg/dwg2dxf.exe` 等（自 `tools/libredwg/`） | — |
| VC++ 运行库 | 2019 (MSVBCRT AIO) | `offline/installers/MSVBCRT_AIO_x64.exe` | — |
| ACE | 2016 (Access 数据库引擎 X64) | `offline/installers/accessdatabaseengine_X64.exe`（未捕获时安装阶段 SKIP） | — |
| Celery/Flower/API 依赖 | 全量 wheel（cp313 win_amd64） | `offline/wheels/*.whl`（`pip download`，`--only-binary=:all:`，无 sdist） | — |

> 备注：wheel 内含 Redis Python 客户端 8.1.0 与 Redis **服务端** 8.10.0 是两回事，
> 前者随 requirements 下发，后者是 MSYS 移植二进制，属自维护项（见 §七）。

---

## §三 文件清单（本版新增/变更）

| 文件 | 变更 |
|------|------|
| `deploy/README.md` | **新增**：构建/交付/安装/凭证/续跑干跑/排障/重装全流程说明（中文） |
| `deploy/build_bundle.ps1` | **新增**：联网机离线资源包生成器（v2.22.3 之后的 Task 3 构建） |
| `deploy/deploy_offline.bat` | **新增**：离线部署入口（UAC 自提升 + `--from/--dry-run`） |
| `deploy/scripts/common.ps1` | **新增**：公共函数（Get-DeployRoot/Assert-Manifest/New-RandomKey/检测九件套等） |
| `deploy/scripts/00..99_*.ps1` | **新增**：十二阶段部署脚本 |
| `deploy/scripts/deploy.ps1` | **新增**：阶段编排器（`--from/--only/--dry-run`，失败分级） |
| `deploy/tests/run_tests.ps1` | **新增**：部署脚本自测（17 例，无 Pester 依赖） |
| `deploy/offline/` | **新增**：离线资源包（127 文件，含 manifest.json 校验清单） |
| `deploy_windows.bat` | **变更**：全文替换为 ASCII 薄封装，转调 `deploy\deploy_offline.bat` |
| `BASELINE.md` | §2 目录结构加 `deploy/` 树；§6 脚本清单加 3 行；§15 版本管理 → v2.23.0；顶部最后更新 → 2026-10-09 |
| `CONTEXT.md` | 第 4 行版本 → v2.23.0 |
| `app/main.py` | `version="2.22.3"` → `version="2.23.0"` |

---

## §四 集成干跑验证（2026-10-10，开发机）

> 干跑命令退回 `deploy\scripts\deploy.ps1` 直连（绕过 UAC 自提升，便于 CI 化）。

### (a) 全链路干跑（From=0）

```
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\scripts\deploy.ps1 -DryRun
```

- 00..40 全部 OK（10/22/25/40 现状满足 SKIP；20/21 DRY-RUN would install）
- **50_init_database FAILED（exit 1）**：`psql not found: C:\Program Files\PostgreSQL\15\bin\psql.exe`
  —— 本机无 PG15，**按设计失败**（不动此阶段凑绿）
- summary: 9 stages, 1 failed → `RESULT: FAILED`
- **退出码 1**

### (b) 绿色路径端到端（From=55）

```
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\scripts\deploy.ps1 -DryRun -From 55
```

- 60/90/99 全部 OK（99 内 psql 缺失被捕获为 PostGIS installed=False，不影响本阶段）
- summary: 3 stages, 0 failed → `RESULT: SUCCESS`
- **退出码 0**

### (c) deploy 脚本自测

```
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\tests\run_tests.ps1
```

- **Passed: 17  Failed: 0，退出码 0**

### ASCII 校验

`deploy_windows.bat` 替换后逐字节检查：215 bytes、0 个非 ASCII 字节、无 BOM → 纯 ASCII。

---

## §五 已知偏差与风险

| 项 | 说明 | 处置 |
|----|------|------|
| **开发环境 PG16 vs 交付目标 PG15** | 开发机为 PostgreSQL 16；本版交付目标钉死 PG15.15 + PostGIS 3.6.1 bundle。`50_init_database` 用的 alembic 迁移 head 与 PG 版本无关，**实际迁移在目标机 PG15 上执行即验证** | §四(a) 呈现「按设计的 dev 失败」，不改 50 凑绿；目标机真装为终验 |
| **本机无 PG15，`50_init_database` 真跑延后至目标机** | 干跑覆盖 00..40 + 60..99（(a)(b) 两段）；50 建库/迁移只在目标机执行一次 | 目标机安装后按 99_verify 报告核对 |
| `deploy_linux.sh` / `restart.bat` 版本锚 | 本版交付时由 TC（`f680ad8`）同步至 **v2.23.0**（`deploy_linux.sh:27`、`restart.bat:2/20`），BASELINE §15 已如实更新 | 已同步 |
| `docs/revision_history.md` 版本锚 | 「最后更新/当前版本」已同步至 **2026-10-09 / v2.23.0**，并追加 `## v2.23.0 批` 条目（TC `f680ad8`）；修订详情以 `docs/revision_v2_23_0.md` 为准 | 已同步 |
| 自定义 PG 超级用户口令（`VCS_PG_SUPERPASSWORD`） | 交付路径锁定 `postgres/postgres`；该环境变量**仅**作用于安装/建库阶段的 psql 调用，alembic 迁移与应用 `.env` 连接串仍为默认口令，**自定义口令会致 50 阶段鉴权失败** | v2.23.0 不支持自定义口令，见 `deploy/README.md` §六 |
| ACE 安装包未捕获 | `-AceInstaller` 未给时 `22` 阶段 SKIP，仅影响 MDB 导出（`pyodbc` + ADOX 链路） | 交付目标机按需补装 |
| wheel 的 `--only-binary=:all:` | 规避 sdist 在目标机现场编译问题；个别包无 win amd64 wheel 时构建会失败 | 构建时换源或补 wheel，见 `deploy/README.md` |

---

## §六 测试

- `deploy/tests/run_tests.ps1`：17/17 通过（命令检测、目录定位、SHA-256、
  随机密钥、端口探测、manifest 好/坏夹具、六个检测函数布尔契约）。
- 集成干跑 (a)(b)(c)：见 §四。
- 业务回归：本版业务代码仅改 `app/main.py` version 字段，不影响应用行为；应用层
  pytest 全量回归（1778 passed / 100%）为 v2.22.3 基线，未随本版变化。

---

## §七 未做 / 已知债

- **Redis 服务端二进制自维护**：`Redis8/`（MSYS 移植 8.10.0）不在官方安装包体系内，
  无数字签名渠道；`build_bundle.ps1` 原样收集，后续如需官方渠道可换 Memurai 等替代。
- **卸载脚本**：`deploy_offline.bat` 只做安装，卸载/重置依靠「删 .venv / 删库 / 控制
  面板卸载」的手动流程（见 `deploy/README.md` §七），未脚本化。
- **Linux 离线部署**：未做，`deploy_linux.sh` 保持原在线语义。
