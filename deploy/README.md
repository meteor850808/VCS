# VCS 空间质检 — Windows 离线部署

> 本目录承载 VCS 的 **Windows 离线一键部署子系统**（v2.23.0）。面向实施人员，
> 下文按「构建 → 交付 → 安装 → 凭证 → 续跑/干跑 → 排障 → 重装」顺序说明。

---

## 本目录用途

```
deploy/
├── build_bundle.ps1      # 资源包构建 + manifest 校验清单（零参数即可，来源默认在 offline/ 内）
├── deploy_offline.bat    # 人工入口：UAC 自提升 + 末尾暂停（支持 --from / --only / --dry-run）
├── deploy_silent.bat     # 静默入口：要求已提权，零交互、无暂停（计划任务/远程用）
├── README.md             # 本文件
├── offline/              # 离线资源包（唯一的真源；交付时整目录拷贝）
│   ├── manifest.json     # ← 离线资源校验清单（由 build_bundle.ps1 生成，交付的关键确认点）
│   ├── installers/       # 安装包：Python 3.13.3 / PostgreSQL 15.15 / PostGIS 3.6.1 /
│   │                     #            VC++2019 (MSVBCRT_AIO) / ACE2016 (AccessDatabaseEngine)
│   ├── redis/            # Redis win64 二进制（redis-server.exe + redis.conf，仅载荷）
│   ├── libredwg/         # LibreDWG win64 二进制（dwg2dxf.exe 等）
│   ├── wheels/           # Python 离线 wheel 包（cp313 win_amd64，--only-binary=:all:）
│   └── seed/             # 数据标准种子 JSON（一厅八室参照物）
├── scripts/              # 部署阶段脚本（00..99 十二阶段 + common.ps1 公共函数 + deploy.ps1 编排器）
├── tests/                # 部署脚本自测（run_tests.ps1，256 例；阶段在临时镜像树中执行，绝不触碰本仓库）
├── diagnostics/          # 失败诊断包（自动生成，保留最近 5 份）
├── last_result.json      # 机器可读结论（每次运行覆盖，原子写入）
└── logs/                 # 部署运行日志（deploy_YYYYMMDD_HHMMSS.log）
```

> **自测的隔离约定**：`run_tests.ps1` 会真实执行部署阶段脚本，而阶段用自身
> `$PSScriptRoot` 往上两级定位项目根。因此所有阶段调用都通过 `Invoke-Stage -ProjectRoot`
> 在**临时镜像树**里跑 —— 否则 `40_setup_venv` 会把**开发机的 `.venv` 重建掉**
> （v2.23.4 之前确实发生过，见 `docs/revision_v2_23_4.md`）。
> `venv-build` 修复动作也加了护栏：只有存在 `deploy\offline`（部署机特征）才允许删 venv。

> `offline/redis/` 只放 Redis 载荷（`*.exe` / `*.dll` / `redis.conf`）。
> `build_bundle.ps1` 每次构建都会清掉 `dump.rdb`、`*.pid`、`*.aof`、`appendonlydir/` 等运行期产物——
> 它们不在 manifest 中，`00_preflight` 校验不到，混进包里会让目标机 Redis 带脏数据启动。

**离线资源 `offline/` 就是唯一的真源**：`build_bundle.ps1` 的每一个来源默认都指向
`deploy/offline/` 自身——Python/PostgreSQL/PostGIS/VC++/ACE 安装包取 `offline/installers/`
的同名文件，Redis 兜底取仓库的 `Redis8/`，libredwg 兜底取 `tools/libredwg/`，种子与 wheel
兜底取 `offline/seed`、`offline/wheels`。因此**在已备好资源的机器上，不带任何参数运行它
只是重新校验并重写 `manifest.json`，不会改动交付物**（实测 127/127 文件按名称与内容完全一致）。

收集完成后为 `offline/` 下**每个文件**写 SHA-256 与体量到 `manifest.json`；目标机的
`00_preflight` 会逐文件校验哈希 —— 任一文件缺失或内容被篡改都会在第一步被拦截。

> **不再依赖任何机器专属路径**。早期版本的默认来源是作者本机的软件归档目录
> （`D:\软件\...`）与桌面上的数据标准目录，换一台机器就必须手改脚本。现已全部改为
> `deploy/offline/` 内部兜底，仅在你**显式传入路径**时才从外部取件。

---

## 一、重新生成 manifest（通常需要）

只要 `deploy/offline/` 已经齐备（本仓交付状态即是），在**任意**机器上运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\build_bundle.ps1
```

- **零参数即可**：来源默认全部落在 `deploy/offline/` 内，不需要联网、不需要外部目录。
- 成功后末尾打印 `manifest.json written: N files, X MB`。
- **要换版本或补组件**时显式传参（此时才需要外部来源）：
  `-PythonInstaller` / `-PgInstaller` / `-PostgisInstaller` / `-VcInstaller` /
  `-AceInstaller` / `-SeedDir` / `-RedisSrc` / `-LibreDwgSrc` / `-WheelsDir`。
- `-SkipWheels`：跳过去 `pip download`（**已打包 wheel 时会自动跳过**，无需手加）。
- 缺件会**直接报错并列出缺什么**，不会产出半成品包；ACE 缺失只告警（安装阶段降级，见「排障」）。
- 全部脚本站净 ASCII，中文路径只可能来自你显式传入的参数。

---

## 二、交付

1. **整目录拷贝**：把整个 `空间质检\`（含 `.venv` 可选的**源码目录**）拷到目标机，
   保持目录结构不变。**不要只拷 `deploy/`** —— 部署脚本向上两级定位项目根
   （`scripts\common.ps1` 的 `Get-ProjectRoot`），`deploy/` 必须位于 `空间质检\deploy\`。
2. **确认 manifest 存在**：拷贝完成后核对
   `deploy\offline\manifest.json` 存在且非空。它既是资源清单，也是
   `00_preflight` 校验的目标 —— 缺失时安装会在第 0 阶段直接失败。
3. **体积提示**：`offline/` 含数百 MB 安装包与 wheel，整目录拷贝预计
   700MB~1GB，建议用 U 盘/内网共享，勿走低带宽通道。

---

## 三、目标机安装

> 前置：目标机为 64 位 Windows（Win10/Server 2019+），关闭再装 PG 无关干扰。

**方式 A — 人工（会提示、会暂停）**

```bat
rem 右键「以管理员身份运行」，或直接双击（会自动经 UAC 自提升）
deploy\deploy_offline.bat
```

**方式 B — 静默 / 计划任务 / 远程（零交互）**

```bat
rem 必须已在提权会话中运行；未提权会立刻报错退出，而不是弹 UAC 挂住
deploy\deploy_silent.bat
echo %errorlevel%          rem 0 成功 / 1 失败
type deploy\last_result.json
```

两种方式都支持同一批参数（`--from N`、`--only N`、`--dry-run`、`--max-retries N`）。
`deploy_silent.bat` 额外固定 `--silent --unattended`，因此**不暂停、不弹浏览器**；
`deploy_offline.bat --silent` 同样不暂停，但仍会替你处理 UAC。

安装按 12 个阶段顺序执行（编号即顺序，先校验后安装）：

| 阶段 | 内容 | 可重试 |
|------|------|--------|
| `00_preflight` | 校验 `offline/manifest.json` 全量哈希 + 管理员权限 | 否 |
| `10_install_python` | 静默装 Python 3.13.3（已装则跳过） | 否 |
| `20_install_postgres` | 静默装 PostgreSQL 15.15 + 自动配服务（**已装其它大版本会中止并给处置指引，绝不自动卸载**，见「排障」） | 是（≤3） |
| `21_install_postgis` | 装 PostGIS 3.6.1（与 PG15 匹配的 bundle） | 否 |
| `22_install_ace` | 装 ACE2016（**非关键**，失败仅告警不中断） | 是 |
| `25_install_vcredist` | 装 VC++2019 运行库（**非关键**，同上） | 是 |
| `30_setup_redis` | 启动 Redis + 注册开机自启计划任务 | 是（≤3） |
| `40_setup_venv` | 建 `.venv`，离线安装 wheel（不联网） | 是 |
| `50_init_database` | 建库 + 启用 PostGIS + alembic 迁移到 head | 是 |
| `60_seed_and_key` | 写 `.env`（生成 `ADMIN_INIT_KEY`）+ 导入种子标准 + 建 API Key | 是（≤3） |
| `90_start_services` | 启动 uvicorn(45679) + celery worker + flower(5555) | 是（≤3） |
| `99_verify` | 打印组件探测结果与入口地址 | 否 |

取期末端会打印汇总：`RESULT: SUCCESS`（绿）或 `RESULT: FAILED`（红，见日志与「自动修正」一节）。

> 双击运行时若触发 UAC 自提升，入口会**等待被提升的进程结束**再回显退出码并 `pause`，
> 因此失败时原始窗口不会一闪而过；详细阶段日志始终在 `deploy\logs\`。

### 3.1 静默模式到底静了什么

`-Silent` / `--silent`（或 `VCS_DEPLOY_QUIET=1`）**只压噪，不压错**：

| 级别 | 控制台 | `deploy\logs\deploy_*.log` |
|------|--------|----------------------------|
| INFO / OK / SKIP | 静默 | **照常记录** |
| WARN / ERROR | **照常显示** | 照常记录 |

因此计划任务日志既不刷屏，出问题又不会漏。`VCS_DEPLOY_QUIET` 会经环境变量传给每个阶段子进程，
全程一致。

### 3.2 机器可读结论 `deploy\last_result.json`

无需解析日志即可判定（**原子写入**，计划任务可直接读）：

```json
{
  "version": "2.23.3", "result": "SUCCESS", "finished_at": "2026-10-12T16:25:07",
  "failed_stage": "", "reason": "", "exit_code": 0,
  "diagnostics": "", "deploy_log": "…\\deploy\\logs\\deploy_20261012_162507.log",
  "stages": [ {"Stage":"00_preflight.ps1","Exit":0,"Ok":true,"Attempts":1,"Repairs":0} ],
  "admin_init_key": "…", "api_key": "…", "dry_run": false
}
```

- `result`：`SUCCESS` / `FAILED`。
- `failed_stage` + `reason`：失败位置与原因。
- `stages[].Attempts` / `Repairs`：该阶段试了几次、自动修正了几次 ——
  **`Attempts: 3` 就是"重试后才成功"的直接证据**。
- `diagnostics`：失败诊断包路径（见下）。
- 失败时 `deploy\diagnostics\<stamp>\` 里另有完整证据链。

---

## 四、凭证（重要）

安装末尾（`60_seed_and_key` / `99_verify`）会报告两个密钥的**指纹**，而不是值：

```
ADMIN_INIT_KEY : fp=4e72e67cd078  (full value: .install_state.json)
API_KEY        : fp=3a91c0b7ee42  (full value: .install_state.json)
```

- **`ADMIN_INIT_KEY`**：引导密钥，首次登录系统管理员使用。
- **`API_KEY`**：调用 API 的访问密钥（x-api-key）。
- **完整值只存在一个文件里：`deploy\.install_state.json`**（安装时已把它 ACL 收紧为
  仅 Administrators / SYSTEM / 当前用户）。

### 4.1 为什么日志和结果文件里只有指纹（v2.23.5 起）

`deploy\logs\` 是运维**第一个会贴进工单**的东西，`deploy\diagnostics\` 更被本 README
明确说明「**可直接发给排障人**」。因此这两个地方都**不得包含可用密钥**：

| 位置 | 现在包含 | 以前包含 |
|------|----------|----------|
| `deploy\logs\deploy_*.log` | 指纹 | ⚠️ **明文两个密钥** |
| `deploy\last_result.json` | `credentials.admin_init_key_fp` / `api_key_fp` / `source` | ⚠️ **明文两个密钥** |
| `deploy\diagnostics\<stamp>\` | 指纹 + 一句「未拷贝 .install_state.json」 | ⚠️ **`.install_state.json` 副本** |

指纹是 SHA-256 前 12 位十六进制：**足以核对"现场用的是哪把密钥"，但无法反推出密钥**。

**取密钥的唯一方式**：

```powershell
Get-Content deploy\.install_state.json | ConvertFrom-Json | Select-Object admin_init_key, api_key
```

> **请勿把 `.install_state.json` 随系统一起对外分发**；诊断包已刻意不含它。
> `.gitignore` 已覆盖 `.install_state.json`、`last_result.json`、`diagnostics/`。

### 4.2 交付前必做：别把打包机的密钥带走

`deploy\README.md` §二 要求**整目录拷贝**，而仓库根的 `.env` 里可能就有一把**可用的**
`ADMIN_INIT_KEY`；同时 `60_seed_and_key` 阶段在「`.env` **与** `.install_state.json` 同时存在」
时会**直接跳过生成**（`60_seed_and_key.ps1:12`）。

**⇒ 交付前请确认待交付树中不存在这两个文件**：

```powershell
Remove-Item .\.env, .\deploy\.install_state.json -ErrorAction SilentlyContinue
```

删掉后目标机首次部署会**重新生成**新的引导密钥（`--from 60`）。

---

## 五、续跑 / 干跑

部署是**幂等**的：中断后可续跑，已完成的阶段会检测到并跳过。

```bat
deploy_offline.bat --from 40     rem 从 40 阶段（建 venv）续跑
deploy_offline.bat --from 50     rem 从 50 阶段（建库）续跑
deploy_offline.bat --dry-run     rem 干跑：只走校验不真装
deploy_offline.bat --dry-run --from 55
deploy_offline.bat --max-retries 5   rem 放宽自动重试次数（默认 2，即最多 3 次尝试）
```

- `--from N`：跳过编号 `< N` 的阶段直接续跑（`--from 40` 常用于重装 venv）。
- `--dry-run`：各阶段只执行「检测/校验」分支，不做实际安装；用于安装前体检，
  或在目标机上验证资源包完整性。干跑**不重试**。
  **缺依赖只告警不判红**（例如没有 PG15 时 `50` 报 `DRY-RUN: psql not found` 的 WARN 后仍返回 0）。
  但 `20` 若发现目标机已有**其它大版本** PostgreSQL，会明确报 `conflict` 并以 1 结束 ——
  这是有意的：干跑的目的就是提前暴露出这个必须先人工处理的阻塞点（见「排障」）。
- `--only N`：只跑某一个阶段（排障用）。
- `--max-retries N`：放宽自动重试次数（默认 2，即最多 3 次尝试）。

### 5.1 参数写法与退出码

**两种写法都接受**：面向运维的 `--from 40 --dry-run`，以及 PowerShell 原生的
`-From 40 -DryRun`。另外 `--from=40` 等号形式也支持，选项名大小写不敏感。

> **为什么专门做这件事**：PowerShell 在 `-File` 模式下**不会把双横线参数绑定到 `param()`**，
> 它只落进 `$args`。早期版本没有解析 `$args`，于是运维照文档敲 `--dry-run`
> 时该参数被**静默忽略、直接开始真装** —— 这是"静默错误行为"，比报错更危险。
> 现在 `deploy.ps1` 会先归一化命令行。

**不认识的参数会被拒绝**（exit 2）并列出支持的选项，**绝不猜你的意图**。

| 退出码 | 含义 |
|--------|------|
| `0` | 成功 |
| `1` | 某个阶段失败（失败阶段与处置提示见日志 / `diagnostics`；`last_result.json` 有结构化记录） |
| `2` | 命令行参数无法识别（打错字） |

---

## 六、自动修正与问题提示

### 6.1 能自动修的，脚本自己修

失败会**先自动重试**，重试之间执行一个**白名单修正动作**：

| 阶段 | 自动修正动作 |
|------|--------------|
| 20 PG | 停掉半注册的 `postgresql-x64-15`；删残留 `postmaster.pid`；等 10s 重试 |
| 21 PostGIS | 拉起 `postgresql-x64-15` 并等 3s |
| 30 Redis | 杀掉**带 `redis.conf` 命令行**的 `redis-server.exe`；删残留 `redis_6379.pid` / `dump.rdb.tmp` |
| 40 venv | **仅在 `uvicorn.exe` 缺失时**删除 `.venv`（wheel 可重建）；`py -3.13 -m ensurepip --upgrade` |
| 50 DB | 拉起 `postgresql-x64-15` 并等 3s |
| 60 凭证/种子 | 释放 45679 —— **仅当占用者是 `python`/`python3`/`uvicorn`**，否则只告警不动手 |
| 90 起服 | 同上释放 45679；清理 `celery*worker*` / `*flower*` 僵尸进程 |

**关键阶段默认最多 3 次尝试**（`--max-retries N` 即最多 N+1 次）。
`00 / 10 / 21 / 99` **不重试**：这些阶段的失败不具备瞬时性，重试只会让运维在
「PG16 已占 5432」这类确定性问题上白等两个 15s 周期。

> **红线**：修正动作**只清理本部署自己创建的东西**（我们注册的 PG15 服务、`offline\redis`、
> 我们建的 `.venv`、我们的 python/uvicorn/celery 进程），
> **绝不卸载或重配它没装的软件**。占用 45679 的若是无关进程，脚本只告警不动手。

### 6.2 修不了的，明确告诉你是什么问题

无法自动修正时，脚本**停在可复现的位置**，并把处置步骤同时打进日志与控制台。例如 `20` 阶段：

```
[20_postgres] incompatible PostgreSQL 16.4-1 found (service 'postgresql-x64-16'); this deploy requires 15.x on port 5432
[20_postgres] this script never removes an existing PostgreSQL automatically.
[20_postgres] do this on the target machine, then re-run: deploy_offline.bat --from 20
[20_postgres]   1) Control Panel > Programs and Features > uninstall the PostgreSQL entry above
[20_postgres]   2) delete the leftover directory if any: 'C:\Program Files\PostgreSQL\<major>'
[20_postgres]   3) remove the leftover registry key: HKLM\SOFTWARE\PostgreSQL\Installations\<key>
[20_postgres]   4) also clear: sc delete postgresql-x64-<major>
[20_postgres]   5) confirm port 5432 is free: netstat -ano | findstr :5432
```

其它阶段也各有针对性提示（wheel 集不全、缺 py launcher、自定义口令须改 `.env`、
去哪个文件看 traceback 等）。

### 6.3 失败诊断包 `deploy\diagnostics\<时间戳>\`

失败时自动生成，**只读采集、绝不抛异常**，可直接发给排障人：

| 文件 | 内容 |
|------|------|
| `summary.txt` | 失败阶段、原因、生效的处置提示、主机信息（OS / PowerShell 版本 / 是否管理员 / 系统码页 / 磁盘余量） |
| 同上 | 组件探测：python、psql、**PostgreSQL 探测结果（版本 + 服务名 + 来源）**、PostGIS、ACE、VC++、venv |
| 同上 | 端口归属 5432 / 6379 / 45679 / 5555（含**占用进程名与 pid**） |
| 同上 | 服务状态、`VCS-Redis` 计划任务状态、Application 事件日志近一天含 `postgres` 的记录 |
| `deploy.log` | 本次完整阶段日志 |
| `manifest.json` / `.install_state.json` | 交付物与凭证快照 |
| `server_err.log` / `worker_err.log` / `flower_err.log` | 各末 200 行 |
| `install-postgresql.log` / `bitrock_installer*.log` | 安装器自带日志末 200 行 |

**只保留最近 5 份**，反复重试不会把磁盘堆满。

---

## 七、排障

### 6.1 目标机已经装了别的 PostgreSQL（最常见阻塞）

`20_install_postgres` 的判定规则：

| 目标机现状 | 行为 |
|------------|------|
| 没有 PostgreSQL | 安装 PG 15.15 |
| 有**健康的** PostgreSQL 15.x（有 `bin\psql.exe`、服务在） | 直接复用，跳过安装 |
| 只有 15 的残留（服务或 `bin\psql.exe` 已缺） | 视为坏安装，重新安装 |
| 有**其它大版本**（PG16 / PG17 …） | **中止**，打印处置指引，退出码 1 |
| 探测到 PG 但主版本判定不出 | **中止**（不猜） |

中止时日志（`deploy\logs\` 与控制台）会给出这 5 步：

```
[20_postgres] incompatible PostgreSQL 16.4-1 found (service 'postgresql-x64-16'); this deploy requires 15.x on port 5432
[20_postgres] this script never removes an existing PostgreSQL automatically.
[20_postgres] do this on the target machine, then re-run: deploy_offline.bat --from 20
[20_postgres]   1) Control Panel > Programs and Features > uninstall the PostgreSQL entry above
[20_postgres]   2) delete the leftover directory if any: 'C:\Program Files\PostgreSQL\<major>'
[20_postgres]   3) remove the leftover registry key: HKLM\SOFTWARE\PostgreSQL\Installations\<key>
[20_postgres]   4) also clear: sc delete postgresql-x64-<major>
[20_postgres]   5) confirm port 5432 is free: netstat -ano | findstr :5432
```

> **为什么不自动卸载**：卸载是破坏性且不可逆的，而 EDB 的 `--mode unattended` 卸载器
> 通常会**残留** `HKLM\SOFTWARE\PostgreSQL\Installations\<key>`。结果就是「库被删了、
> 部署仍因 `still present after uninstall` 中止」。因此脚本一律要求人工确认清理。
> v2.23.1 及更早版本会提示 `Uninstall PostgreSQL <ver> and continue? [y/N]` 并可能静默卸载，
> 已废弃。

### 6.2 端口被占

`20` 在调用安装器**之前**检查 5432：

```
[20_postgres] port 5432 is already in use (pid 4128, postgres) - PostgreSQL 15 cannot create its cluster
```

请先停掉占用者（往往是另一处 PostgreSQL 服务）再重跑 `--from 20`。

### 6.3 其它

- **日志**：每次运行写 `deploy\logs\deploy_YYYYMMDD_HHMMSS.log`（每阶段一行，
  附时间戳与 INFO/OK/WARN/ERROR/SKIP 级别）。报错定位先看这里；干跑也照记。
- **PG 口令**：v2.23.0 交付路径锁定 PG 默认账号 `postgres`（安装/建库/迁移/`.env` 连接串
  全部一致）。`VCS_PG_SUPERPASSWORD` 环境变量**仅**作用于 `20_install_postgres` 与
  `50_init_database` 的 psql 安装/建库调用，**自定义口令会造成 50 阶段 alembic 迁移
  鉴权失败**（应用与 alembic 连接串仍为默认口令）——**本版不支持自定义口令**，请保持默认。
  `50` 现在会先用 `SELECT 1` 探针验证口令，不符时直接报
  `cannot authenticate as 'postgres' on localhost:5432`，不会再把含糊的堆栈丢给 alembic。
- **同步数据库 URL 必须带驱动名**（v2.23.4）：`DATABASE_URL_SYNC` 写成
  `postgresql+psycopg2://…`。SQLAlchemy 2.1 起 `postgresql://` 的默认驱动变成 psycopg(v3)，
  而 `requirements.txt` 只装 `psycopg2-binary` → 裸写会让 `app/db/session.py` 在 **import
  阶段**抛 `ModuleNotFoundError: No module named 'psycopg'`，应用与 `alembic upgrade head`
  全部起不来。`app/config.py` 的校验器会把裸 URL 自动补成 `+psycopg2`，因此**目标机已有的
  旧 `.env` 也能直接跑**；但 `.env.example` 已是正确写法，新部署不会再产生裸 URL。
- **非关键阶段不阻塞**：ACE（`22`）与 VC++（`25`）失败只记 **WARN 并继续** ——
  它们影响的是 MDB 导出（ACE）与部分二进制依赖（VC++），不阻断 HTTP 主链路；
  需要时事后单独补装即可。
- **psql not found**：`50_init_database` 依赖 PG15 的 `psql`。若本机只有新版 PG
  （如 PG16）而无 PG15，该阶段会失败——这是**预期行径**，请先按 6.1 处理目标机的 PG，
  或确认 `20` 阶段真的装上了 PG15（`20` 现在会校验服务 + `bin\psql.exe` + 5432 监听三连）。
- **端口占用**：默认 45679（API）、5555（Flower）、6379（Redis）。`30/90/99`
  阶段会检测占用。
- **Redis 脏数据**：`build_bundle.ps1` 每次构建都会剔除 `dump.rdb` / `*.pid` / `*.aof` /
  `appendonlydir/`。这些文件**不在 `manifest.json` 里**，`00_preflight` 无法发现，
  所以不要在构建后手工往 `offline/redis/` 里添加运行期文件。

---

## 八、重装 / 重置

| 目标 | 做法 |
|------|------|
| 重建 Python 环境 | 删 `.venv` → `deploy_offline.bat --from 40`（重建并装 wheel） |
| 重建数据库/种子 | 停服务 → 删空 PG 库 → `deploy_offline.bat --from 50`（建库+迁移+种子） |
| 彻底重装 | 卸载 PG15/Redis/PostGIS → 删 `.venv`、`data\`、`deploy\.install_state.json` → 从 `--from 10` 重跑 |
| 只重置凭证 | 删 `deploy\.install_state.json` 与 `.env` → `deploy_offline.bat --from 60`（重新生成并打印） |

> 卸载第三方组件请走「控制面板 → 程序」，不要手工删目录；PG 服务由 `20` 阶段
> 以 `postgresql-x64-15` 注册，卸载后如残留服务可用 `sc delete postgresql-x64-15` 清理。

---

## 附：版本矩阵

| 组件 | 版本 | 来源 |
|------|------|------|
| Python | 3.13.3 (win64) | `offline/installers/python-3.13.3-amd64.exe` |
| PostgreSQL | 15.15-2 (win64) | `offline/installers/postgresql-15.15-2-windows-x64.exe` |
| PostGIS | 3.6.1 (pg15 bundle) | `offline/installers/postgis-bundle-pg15x64-setup-3.6.1-1.exe` |
| Redis | 8.10.0 (win64) | `offline/redis/redis-server.exe` |
| LibreDWG | 0.14 (win64) | `offline/libredwg/dwg2dxf.exe` 等 |
| VC++ 运行库 | 2019 (MSVBCRT AIO) | `offline/installers/MSVBCRT_AIO_x64.exe` |
| ACE | 2016 (Access 2016 db engine) | `offline/installers/accessdatabaseengine_X64.exe` |

矩阵字段写入 `offline/manifest.json` 顶部（`python` / `postgres` / `postgis`）。
