# Windows 离线一键部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在项目内新增 `deploy/` 子系统，使目标机（Win x64、无网、有管理员、任意目录）整目录拷贝后双击一个入口即可完成环境安装 → 依赖 → 数据库 → 种子 → 开机自启服务，开箱即用。

**Architecture:** 分「构建侧」（联网机 `build_bundle.ps1` 生成 `deploy/offline/` 资源包 + SHA256 清单）与「安装侧」（离线机 `deploy_offline.bat` → `deploy.ps1` 编排器 → 编号阶段脚本）两层。每个阶段脚本可独立运行、幂等、`--DryRun` 干跑；共享逻辑集中在 `common.ps1`，检测逻辑以纯函数暴露以便单测。

**Tech Stack:** Windows PowerShell 5.1、批处理（自提升）、Python 3.13.3、PostgreSQL 15.15、PostGIS 3.6.1、Redis 8.10.0（msys 构建）、LibreDWG、ACE 2016、VC++ 运行库、Scheduled Tasks。

**Spec:** `docs/superpowers/specs/2026-10-09-offline-windows-deploy-design.md`

## Global Constraints

- 平台：Windows x64；脚本解释器为 **Windows PowerShell 5.1**（`powershell.exe`），不得依赖 PowerShell 7。
- **所有 `.bat` / `.ps1` 文件内容必须为纯 ASCII（英文）**。PS 5.1 无 BOM 时按 ANSI 解码，中文会乱码——这是旧 `deploy_windows.bat` 乱码的根因。唯一允许中文的文件是 `deploy/README.md`（UTF-8，供人阅读）。
- 安装期**零网络**。任何下载只允许发生在构建侧 `build_bundle.ps1`。
- 版本锁定（verbatim）：Python `3.13.3`、PostgreSQL `15.15`、PostGIS `3.6.1`、Redis `8.10.0`、ACE `2016 (x64)`。
- 端口：应用 `45679`、Flower `5555`、PostgreSQL `5432`、Redis `6379`。
- 数据库：库名 `vcs_db`，用户 `postgres`，口令由部署时输入（缺省 fallback `postgres`）。连接串 `postgresql+asyncpg://postgres:<pwd>@localhost:5432/vcs_db`。
- 交付入口：`deploy\deploy_offline.bat`；资源目录 `deploy\offline\` 加入 `.gitignore`（不提交），但物理随整目录交付。
- 每个 Task 结束**必须提交**（conventional commit）。提交范围仅限 `空间质检/`。
- 阶段脚本命名：`NN_*.ps1`（`NN` 两位数字）；编排器与共享库不匹配 `^\d{2}_` 前缀，故不会被当作阶段执行。
- 非关键阶段（`22_install_ace`、`25_install_vcredist`）失败**告警不中断**；其余阶段失败即中断。

---

## File Structure

构建/安装侧全部新增于 `空间质检/deploy/`：

- `deploy/deploy_offline.bat` — 入口：自提升管理员 → 调 `scripts/deploy.ps1`（ASCII，CRLF）。
- `deploy/build_bundle.ps1` — 联网机构建 `offline/` 资源 + `manifest.json`。
- `deploy/scripts/common.ps1` — 共享库：日志 / 路径 / 管理员 / 哈希 / 清单校验 / 端口 / 静默安装 / 各组件检测 / 随机密钥。
- `deploy/scripts/deploy.ps1` — 编排器：解析 `-From/-Only/-DryRun`，按序跑阶段，汇总结果。
- `deploy/scripts/00_preflight.ps1` … `deploy/scripts/99_verify.ps1` — 12 个阶段脚本。
- `deploy/tests/run_tests.ps1` — 轻量测试跑器（不依赖 Pester）。
- `deploy/README.md` — 交付/安装/排障说明（中文，UTF-8）。
- `deploy/offline/`（生成物，gitignore）：`installers/`、`redis/`、`libredwg/`、`wheels/`、`seed/`、`manifest.json`。
- `deploy/logs/`（生成物，gitignore）。

被修改/退役的既有文件：
- 修改 `.gitignore`（新增 `deploy/offline/`、`deploy/logs/`）。
- 修改 `deploy_windows.bat`（改为薄封装：转调 `deploy\deploy_offline.bat`，ASCII 注释）。
- 修改 `BASELINE.md`、`CONTEXT.md`、`docs/revision_v2_23_0.md`（文档同步）。

---

## Task 1: 共享库 common.ps1 + 测试跑器

**Files:**
- Create: `空间质检/deploy/tests/run_tests.ps1`
- Create: `空间质检/deploy/scripts/common.ps1`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces（后续所有阶段脚本依赖的精确签名）：
  - `Get-DeployRoot` → string（`deploy/` 绝对路径）
  - `Get-ProjectRoot` → string（项目根绝对路径）
  - `Get-OfflineDir` → string（`deploy/offline`）
  - `Write-StageLog -Message <string> [-Level INFO|OK|WARN|ERROR|SKIP]`
  - `Test-Admin` → bool；`Assert-Admin`（非管理员抛异常）
  - `Test-CommandExists -Name <string>` → bool
  - `Get-FileSha256 -Path <string>` → string（小写 hex）
  - `Read-Manifest -Path <string>` → PSCustomObject
  - `Assert-Manifest -OfflineDir <string>` → 校验通过返回清单对象，否则抛异常
  - `Invoke-SilentInstaller -FilePath <string> [-ArgumentList <string[]>] [-TimeoutSec <int>]` → int（退出码）
  - `Test-PortListening -Port <int>` → bool
  - `Test-Python313Installed` / `Test-Pg15Installed` / `Test-PostgisInstalled` / `Test-AceInstalled` / `Test-VcRuntimeInstalled` / `Test-RedisRunning` → bool
  - `Test-VenvReady -ProjectRoot <string>` → bool
  - `New-RandomKey [-Bytes <int>=32]` → string（Base64）

- [ ] **Step 1: Write the failing test**

Create `空间质检/deploy/tests/run_tests.ps1`:

```powershell
# Lightweight test runner for deploy scripts (no Pester dependency).
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\scripts\common.ps1"

$script:Passed = 0
$script:Failed = 0

function Assert-True([bool]$cond, [string]$msg) {
    if ($cond) { $script:Passed++ } else { $script:Failed++; Write-Host "FAIL: $msg" -ForegroundColor Red }
}
function Assert-Equal($actual, $expected, [string]$msg) {
    Assert-True ($actual -eq $expected) "$msg (got '$actual' want '$expected')"
}
function Assert-Throws([scriptblock]$sb, [string]$msg) {
    try { & $sb; Assert-True $false "$msg (expected throw, none)" }
    catch { $script:Passed++ }
}

Write-Host "== deploy/scripts/common.ps1 tests =="

# Test-CommandExists
Assert-True (Test-CommandExists -Name 'powershell') "Test-CommandExists powershell"
Assert-True (-not (Test-CommandExists -Name 'definitely_not_a_cmd_xyz')) "Test-CommandExists bogus -> false"

# Get-DeployRoot / Get-ProjectRoot
$dr = Get-DeployRoot
Assert-True (Test-Path -LiteralPath (Join-Path $dr 'scripts')) "Get-DeployRoot has scripts/"
$pr = Get-ProjectRoot
Assert-True (Test-Path -LiteralPath (Join-Path $pr 'requirements.txt')) "Get-ProjectRoot is project root"

# Get-FileSha256
$tmp = Join-Path $env:TEMP ("vcs_hash_" + [guid]::NewGuid().ToString('N') + ".txt")
Set-Content -LiteralPath $tmp -Value 'hello' -NoNewline -Encoding ASCII
$h = Get-FileSha256 -Path $tmp
Assert-Equal $h.Length 64 "sha256 hex length"
Assert-Equal $h ((Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()) "sha256 matches Get-FileHash"
Remove-Item -LiteralPath $tmp -Force

# New-RandomKey
$k = New-RandomKey -Bytes 32
Assert-True ($k.Length -ge 40) "New-RandomKey length"
Assert-True ((New-RandomKey -Bytes 32) -ne $k) "New-RandomKey unique"

# Test-PortListening (port 1 almost certainly not listening)
Assert-True (-not (Test-PortListening -Port 1)) "Test-PortListening 1 -> false"

# Assert-Manifest: good fixture passes, tampered fixture throws
$fix = Join-Path $env:TEMP ("vcs_manifest_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fix -Force | Out-Null
Set-Content -LiteralPath (Join-Path $fix 'a.bin') -Value 'abc' -NoNewline -Encoding ASCII
$entry = [ordered]@{ path='a.bin'; sha256=(Get-FileSha256 -Path (Join-Path $fix 'a.bin')); size=3 }
$mf = [ordered]@{ version='1'; files=@($entry) } | ConvertTo-Json -Depth 5
Set-Content -LiteralPath (Join-Path $fix 'manifest.json') -Value $mf -Encoding UTF8
Assert-True ((Assert-Manifest -OfflineDir $fix).files.Count -eq 1) "Assert-Manifest good fixture passes"
Set-Content -LiteralPath (Join-Path $fix 'a.bin') -Value 'TAMPERED' -NoNewline -Encoding ASCII
Assert-Throws { Assert-Manifest -OfflineDir $fix } "Assert-Manifest tampered -> throws"
Remove-Item -LiteralPath $fix -Recurse -Force

# Detection functions return bool (values depend on machine)
foreach ($fn in 'Test-Python313Installed','Test-Pg15Installed','Test-PostgisInstalled','Test-AceInstalled','Test-VcRuntimeInstalled','Test-RedisRunning') {
    $v = & $fn
    Assert-True ($v -is [bool]) "$fn returns bool"
}

Write-Host ""
Write-Host "Passed: $script:Passed  Failed: $script:Failed"
if ($script:Failed -gt 0) { exit 1 }
exit 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\tests\run_tests.ps1"`
Expected: FAIL — `common.ps1` 尚不存在（`. "$PSScriptRoot\..\scripts\common.ps1"` 报无法找到文件）。

先创建 `common.ps1` 的**抛出桩**（每个函数 `throw 'TODO'`）以便测试可运行但失败？否——直接进入 Step 3 创建完整实现；Step 2 的失败即「文件不存在」。

- [ ] **Step 3: Write common.ps1**

Create `空间质检/deploy/scripts/common.ps1`:

```powershell
# Shared helpers for VCS offline deployment. ASCII-only (PowerShell 5.1 safe).

function Get-DeployRoot {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
function Get-ProjectRoot {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
function Get-OfflineDir {
    Join-Path (Get-DeployRoot) 'offline'
}

function Write-StageLog {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','OK','WARN','ERROR','SKIP')][string]$Level = 'INFO'
    )
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts][$Level] $Message"
    switch ($Level) {
        'ERROR' { Write-Host $line -ForegroundColor Red }
        'WARN'  { Write-Host $line -ForegroundColor Yellow }
        'OK'    { Write-Host $line -ForegroundColor Green }
        'SKIP'  { Write-Host $line -ForegroundColor DarkGray }
        default { Write-Host $line }
    }
    if ($env:VCS_DEPLOY_LOG) {
        Add-Content -LiteralPath $env:VCS_DEPLOY_LOG -Value $line -Encoding UTF8
    }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Assert-Admin {
    if (-not (Test-Admin)) { throw "Administrator privileges required. Re-run elevated." }
}

function Test-CommandExists {
    param([Parameter(Mandatory)][string]$Name)
    [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

function Read-Manifest {
    param([Parameter(Mandatory)][string]$Path)
    (Get-Content -LiteralPath $Path -Raw -Encoding UTF8) | ConvertFrom-Json
}

function Assert-Manifest {
    param([Parameter(Mandatory)][string]$OfflineDir)
    $mp = Join-Path $OfflineDir 'manifest.json'
    if (-not (Test-Path -LiteralPath $mp)) { throw "manifest.json missing: $mp" }
    $m = Read-Manifest -Path $mp
    $bad = @()
    foreach ($e in $m.files) {
        $fp = Join-Path $OfflineDir $e.path
        if (-not (Test-Path -LiteralPath $fp)) { $bad += "missing: $($e.path)"; continue }
        if ((Get-FileSha256 -Path $fp) -ne $e.sha256.ToLower()) { $bad += "hash mismatch: $($e.path)" }
    }
    if ($bad.Count -gt 0) { throw ("manifest validation failed:`n" + ($bad -join "`n")) }
    return $m
}

function Invoke-SilentInstaller {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSec = 1800
    )
    if (-not (Test-Path -LiteralPath $FilePath)) { throw "installer not found: $FilePath" }
    $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -NoNewWindow -Wait -PassThru
    return [int]$p.ExitCode
}

function Test-PortListening {
    param([Parameter(Mandatory)][int]$Port)
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-Python313Installed {
    if (-not (Test-CommandExists -Name 'py')) { return $false }
    & py -3.13 --version *> $null
    return ($LASTEXITCODE -eq 0)
}
function Test-Pg15Installed {
    [bool](Get-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue)
}
function Test-PostgisInstalled {
    Test-Path -LiteralPath 'C:\Program Files\PostgreSQL\15\share\extension\postgis.control'
}
function Test-AceInstalled {
    try {
        [bool](Get-OdbcDriver -ErrorAction Stop | Where-Object { $_.Name -like 'Microsoft Access Driver*' })
    } catch { $false }
}
function Test-VcRuntimeInstalled {
    foreach ($k in @('HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                     'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64')) {
        if (Test-Path $k) { return ((Get-ItemProperty -Path $k).Installed -eq 1) }
    }
    return $false
}
function Test-RedisRunning {
    Test-PortListening -Port 6379
}
function Test-VenvReady {
    param([Parameter(Mandatory)][string]$ProjectRoot)
    Test-Path -LiteralPath (Join-Path $ProjectRoot '.venv\Scripts\uvicorn.exe')
}

function New-RandomKey {
    param([int]$Bytes = 32)
    $buf = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    [Convert]::ToBase64String($buf)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\tests\run_tests.ps1"`
Expected: `Passed: <n>  Failed: 0`，退出码 0。

- [ ] **Step 5: Commit**

```bash
git add 空间质检/deploy/scripts/common.ps1 空间质检/deploy/tests/run_tests.ps1
git commit -m "feat(deploy): add shared helpers and test runner for offline deploy"
```

---

## Task 2: 00_preflight.ps1

**Files:**
- Create: `空间质检/deploy/scripts/00_preflight.ps1`

**Interfaces:**
- Consumes: `Assert-Admin`、`Assert-Manifest`、`Get-OfflineDir`、`Write-StageLog`（Task 1）。
- Produces: 无（仅校验；失败 `exit 1`）。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/00_preflight.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[00_preflight] begin"
$fail = @()

# 1) admin
Assert-Admin
Write-StageLog "admin: OK" 'OK'

# 2) x64
if ([Environment]::Is64BitOperatingSystem) { Write-StageLog "os: 64-bit OK" 'OK' }
else { $fail += "target must be 64-bit Windows" }

# 3) disk space (require >= 3 GB free on project drive)
$pr = Get-ProjectRoot
$drive = (Split-Path -Qualifier $pr)
$freeGB = [math]::Round((Get-PSDrive -Name $drive.TrimEnd(':')).Free / 1GB, 1)
if ($freeGB -ge 3) { Write-StageLog "free space on $drive = ${freeGB} GB OK" 'OK' }
else { $fail += "insufficient disk on $drive ($freeGB GB, need >=3 GB)" }

# 4) manifest integrity
$off = Get-OfflineDir
try { $m = Assert-Manifest -OfflineDir $off; Write-StageLog "manifest: $($m.files.Count) files verified OK" 'OK' }
catch { $fail += "manifest: $($_.Exception.Message)" }

if ($fail.Count -gt 0) {
    foreach ($f in $fail) { Write-StageLog $f 'ERROR' }
    Write-StageLog "[00_preflight] FAILED" 'ERROR'
    exit 1
}
Write-StageLog "[00_preflight] OK" 'OK'
exit 0
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\00_preflight.ps1" -DryRun`
Expected（管理员 + 已构建离线包 + 磁盘充足）：以 `[00_preflight] OK` 结束，退出码 0。若 `offline/manifest.json` 不存在则报 `manifest:` 错误并以 1 退出——这是预期行为，先通过 Task 3 构建离线包后复测。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/00_preflight.ps1
git commit -m "feat(deploy): add 00_preflight stage"
```

---

## Task 3: build_bundle.ps1（构建侧）

**Files:**
- Create: `空间质检/deploy/build_bundle.ps1`
- Modify: `空间质检/.gitignore`

**Interfaces:**
- Consumes: `Get-DeployRoot`、`Get-OfflineDir`、`Get-ProjectRoot`、`Get-FileSha256`、`Write-StageLog`（Task 1）。
- Produces: `deploy/offline/{installers,redis,libredwg,wheels,seed}/` 与 `deploy/offline/manifest.json`（供 Task 2/5–12 消费）。

- [ ] **Step 1: Write build_bundle.ps1**

Create `空间质检/deploy/build_bundle.ps1`:

```powershell
param(
    [string]$PythonInstaller  = 'D:\软件\python-3.13.3-amd64.exe',
    [string]$PgInstaller      = 'D:\软件\PG15\postgresql-15.15-2-windows-x64.exe',
    [string]$PostgisInstaller = 'D:\软件\PG15\postgis-bundle-pg15x64-setup-3.6.1-1.exe',
    [string]$AceInstaller     = '',
    [string]$VcInstaller      = 'D:\软件\3DMGAME-MSVBCRTAIOv2019.01.25X64X86\MSVBCRT_AIO_2019.01.25_X64.exe',
    [string]$SeedDir          = 'C:\Users\Administrator\Desktop\AI项目\数据标准调整\一厅八室数据标准-转换产出\json',
    [switch]$SkipWheels
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\scripts\common.ps1"

$off = Get-OfflineDir
$pr  = Get-ProjectRoot
foreach ($d in 'installers','redis','libredwg','wheels','seed') {
    New-Item -ItemType Directory -Path (Join-Path $off $d) -Force | Out-Null
}

function Copy-To([string]$src, [string]$dstDir, [string]$dstName) {
    if (-not (Test-Path -LiteralPath $src)) { throw "source missing: $src" }
    $dst = Join-Path $dstDir $dstName
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Write-StageLog "copied -> offline/$dstName" 'OK'
}

# 1) installers (canonical names)
Copy-To $PythonInstaller  (Join-Path $off 'installers') 'python-3.13.3-amd64.exe'
Copy-To $PgInstaller      (Join-Path $off 'installers') 'postgresql-15.15-2-windows-x64.exe'
Copy-To $PostgisInstaller (Join-Path $off 'installers') 'postgis-bundle-pg15x64-setup-3.6.1-1.exe'
Copy-To $VcInstaller      (Join-Path $off 'installers') 'MSVBCRT_AIO_x64.exe'
if ($AceInstaller) { Copy-To $AceInstaller (Join-Path $off 'installers') 'accessdatabaseengine_X64.exe' }
else { Write-StageLog "ACE installer not provided (-AceInstaller). ACE stage will be skipped at install time." 'WARN' }

# 2) redis (exclude runtime artifacts)
$redisSrc = Join-Path $pr 'Redis8'
Copy-Item -Path (Join-Path $redisSrc '*.exe') -Destination (Join-Path $off 'redis') -Force
Copy-Item -Path (Join-Path $redisSrc '*.dll') -Destination (Join-Path $off 'redis') -Force
Copy-Item -Path (Join-Path $redisSrc 'redis.conf') -Destination (Join-Path $off 'redis') -Force
Write-StageLog "redis copied from Redis8/" 'OK'

# 3) libredwg
Copy-Item -Path (Join-Path $pr 'tools\libredwg\*') -Destination (Join-Path $off 'libredwg') -Force
Write-StageLog "libredwg copied from tools/libredwg/" 'OK'

# 4) seed (reference standards JSON)
if (Test-Path -LiteralPath $SeedDir) {
    Copy-Item -Path (Join-Path $SeedDir '*.json') -Destination (Join-Path $off 'seed') -Force
    $n = (Get-ChildItem -LiteralPath (Join-Path $off 'seed') -Filter '*.json').Count
    Write-StageLog "seed copied: $n json" 'OK'
} else { Write-StageLog "seed dir not found: $SeedDir" 'WARN' }

# 5) wheels (offline pip download for cp313 / win_amd64)
if (-not $SkipWheels) {
    if (-not (Test-CommandExists -Name 'py')) { throw "py launcher required to download wheels" }
    Write-StageLog "downloading wheels (py -3.13 -m pip download)..." 'INFO'
    & py -3.13 -m pip download -r (Join-Path $pr 'requirements.txt') `
        -d (Join-Path $off 'wheels') --only-binary=:all:
    if ($LASTEXITCODE -ne 0) { throw "pip download failed" }
    $w = (Get-ChildItem -LiteralPath (Join-Path $off 'wheels')).Count
    Write-StageLog "wheels: $w files" 'OK'
}

# 6) manifest (all files under offline/, relative path + sha256 + size)
$files = Get-ChildItem -LiteralPath $off -Recurse -File |
    Where-Object { $_.Name -ne 'manifest.json' } |
    ForEach-Object {
        [ordered]@{
            path   = $_.FullName.Substring($off.Length + 1).Replace('\','/')
            sha256 = Get-FileSha256 -Path $_.FullName
            size   = $_.Length
        }
    }
$manifest = [ordered]@{
    version  = '1'
    created  = (Get-Date).ToString('s')
    python   = '3.13.3'
    postgres = '15.15'
    postgis  = '3.6.1'
    files    = @($files)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $off 'manifest.json') -Encoding UTF8
$totalMB = [math]::Round((($files | Measure-Object -Property size -Sum).Sum) / 1MB, 1)
Write-StageLog "manifest.json written: $($files.Count) files, $totalMB MB" 'OK'
exit 0
```

- [ ] **Step 2: Add gitignore entries**

Modify `空间质检/.gitignore` — append:

```gitignore
# Offline deploy resources (generated by deploy/build_bundle.ps1; shipped physically, not tracked)
deploy/offline/
deploy/logs/
```

- [ ] **Step 3: Build the bundle**

先下载 ACE（供后续阶段使用）：
Run: `powershell -NoProfile -Command "$u='https://download.microsoft.com/download/3/5/9/35980F81-60F4-4DE3-88FC-8F962B97253B/accessdatabaseengine_X64.exe'; Invoke-WebRequest $u -OutFile 'D:\软件\accessdatabaseengine_X64.exe' -UseBasicParsing"`
若该 URL 失效则改用微软官方 ACE 2016 下载页获取，落到 `D:\软件\accessdatabaseengine_X64.exe`。

再构建：
Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\build_bundle.ps1" -AceInstaller "D:\软件\accessdatabaseengine_X64.exe"`
Expected: 各 `copied ->` OK，`manifest.json written: <N> files, <X> MB`，退出码 0。

- [ ] **Step 4: Verify manifest via preflight**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\00_preflight.ps1" -DryRun`
Expected: `manifest: <N> files verified OK` 且 `[00_preflight] OK`，退出码 0。

- [ ] **Step 5: Commit**

```bash
git add 空间质检/deploy/build_bundle.ps1 空间质检/.gitignore
git commit -m "feat(deploy): add offline bundle builder with sha256 manifest"
```

---

## Task 4: 编排器 deploy.ps1 + 入口 deploy_offline.bat

**Files:**
- Create: `空间质检/deploy/scripts/deploy.ps1`
- Create: `空间质检/deploy/deploy_offline.bat`

**Interfaces:**
- Consumes: `Get-DeployRoot`、`Write-StageLog`（Task 1）；调用阶段脚本（Task 2、5–12）。
- Produces: 编排入口；末行打印 `RESULT: SUCCESS|FAILED`，退出码 0/1。

- [ ] **Step 1: Write deploy.ps1**

Create `空间质检/deploy/scripts/deploy.ps1`:

```powershell
[CmdletBinding()]
param(
    [int]$From = 0,
    [int]$Only = -1,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

$deployRoot = Get-DeployRoot
$logDir = Join-Path $deployRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$env:VCS_DEPLOY_LOG = Join-Path $logDir "deploy_$stamp.log"

Write-StageLog "=== VCS offline deploy (DryRun=$DryRun From=$From Only=$Only) ==="
Write-StageLog "log file: $($env:VCS_DEPLOY_LOG)"

$nonCritical = @(22, 25)   # ACE, VC++ -> warn but continue
$stages = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' |
    Where-Object { $_.Name -match '^(\d{2})_.+\.ps1$' } |
    Sort-Object Name

$results = @()
foreach ($f in $stages) {
    if ($f.Name -notmatch '^(\d{2})_') { continue }
    $num = [int]$Matches[1]
    if ($num -lt $From) { Write-StageLog "skip $($f.Name) (stage < From=$From)" 'SKIP'; continue }
    if ($Only -ge 0 -and $num -ne $Only) { continue }

    Write-StageLog ">>> $($f.Name)" 'INFO'
    $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File', $f.FullName)
    if ($DryRun) { $childArgs += '-DryRun' }
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $childArgs -NoNewWindow -Wait -PassThru
    $code = [int]$p.ExitCode
    $ok = ($code -eq 0)
    $results += [pscustomobject]@{ Stage=$f.Name; Exit=$code; Ok=$ok }
    if ($ok) { Write-StageLog "<<< $($f.Name) OK" 'OK' }
    else {
        $level = if ($nonCritical -contains $num) { 'WARN' } else { 'ERROR' }
        Write-StageLog "<<< $($f.Name) FAILED (exit $code)" $level
        if ($nonCritical -notcontains $num) { break }
    }
}

$failed = @($results | Where-Object { -not $_.Ok })
Write-StageLog "=== summary: $($results.Count) stages, $($failed.Count) failed ==="
if ($failed.Count -gt 0) { Write-StageLog "RESULT: FAILED" 'ERROR'; exit 1 }
Write-StageLog "RESULT: SUCCESS" 'OK'
exit 0
```

- [ ] **Step 2: Write deploy_offline.bat**

Create `空间质检/deploy/deploy_offline.bat` (ASCII, CRLF):

```bat
@echo off
setlocal
title VCS Offline Deploy
rem Self-elevate to Administrator if needed.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
set RC=%errorlevel%
if %RC% neq 0 (
    echo.
    echo Deploy FAILED ^(exit %RC%^). See deploy\logs\ for details.
) else (
    echo.
    echo Deploy SUCCESS.
)
pause
exit /b %RC%
```

- [ ] **Step 3: Dry-run full orchestration**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\deploy.ps1" -DryRun`
Expected: 打印每个已存在阶段的 `>>> ... / <<< ... OK`（当前只有 `00_preflight`），末行 `RESULT: SUCCESS`，退出码 0。

- [ ] **Step 4: Commit**

```bash
git add 空间质检/deploy/scripts/deploy.ps1 空间质检/deploy/deploy_offline.bat
git commit -m "feat(deploy): add orchestrator and self-elevating entry point"
```

---

## Task 5: 10_install_python.ps1

**Files:**
- Create: `空间质检/deploy/scripts/10_install_python.ps1`

**Interfaces:**
- Consumes: `Test-Python313Installed`、`Invoke-SilentInstaller`、`Get-OfflineDir`、`Write-StageLog`（Task 1）。
- Produces: 系统级 Python 3.13（`py -3.13` 可用）。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/10_install_python.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[10_python] begin"
if (Test-Python313Installed) { Write-StageLog "[10_python] Python 3.13 already present -> SKIP" 'SKIP'; exit 0 }
if ($DryRun) { Write-StageLog "[10_python] DRY-RUN would install Python 3.13.3" 'INFO'; exit 0 }

Assert-Admin
$exe = Join-Path (Get-OfflineDir) 'installers\python-3.13.3-amd64.exe'
$args = @('/quiet','InstallAllUsers=1','PrependPath=1','Include_pip=1','Include_test=0')
$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList $args
Write-StageLog "[10_python] installer exit code = $code"

# refresh PATH for current process
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
if (Test-Python313Installed) { Write-StageLog "[10_python] OK" 'OK'; exit 0 }
Write-StageLog "[10_python] FAILED - py -3.13 not available after install" 'ERROR'
exit 1
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\10_install_python.ps1" -DryRun`
Expected（本机已有 3.13）：`Python 3.13 already present -> SKIP`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/10_install_python.ps1
git commit -m "feat(deploy): add 10_install_python stage"
```

---

## Task 6: 20_install_postgres.ps1 + 21_install_postgis.ps1

**Files:**
- Create: `空间质检/deploy/scripts/20_install_postgres.ps1`
- Create: `空间质检/deploy/scripts/21_install_postgis.ps1`

**Interfaces:**
- Consumes: `Test-Pg15Installed`、`Test-PostgisInstalled`、`Invoke-SilentInstaller`、`Get-OfflineDir`、`Write-StageLog`（Task 1）。
- Produces: PostgreSQL 15 服务 `postgresql-x64-15`；PostGIS 扩展控制文件装入 PG15。
- 约定：PG 超级用户口令在本阶段由环境变量 `$env:VCS_PG_SUPERPASSWORD` 提供，缺省 `postgres`。

- [ ] **Step 1: Write 20_install_postgres.ps1**

Create `空间质检/deploy/scripts/20_install_postgres.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[20_postgres] begin"
if (Test-Pg15Installed) { Write-StageLog "[20_postgres] postgresql-x64-15 present -> SKIP" 'SKIP'; exit 0 }
if ($DryRun) { Write-StageLog "[20_postgres] DRY-RUN would install PostgreSQL 15.15" 'INFO'; exit 0 }

Assert-Admin
$pwd = if ($env:VCS_PG_SUPERPASSWORD) { $env:VCS_PG_SUPERPASSWORD } else { 'postgres' }
$exe = Join-Path (Get-OfflineDir) 'installers\postgresql-15.15-2-windows-x64.exe'
$args = @(
    '--mode','unattended',
    '--prefix','C:\Program Files\PostgreSQL\15',
    '--datadir','C:\Program Files\PostgreSQL\15\data',
    '--superpassword',$pwd,
    '--serverport','5432',
    '--locale','Chinese_China.936',
    '--enable-components','server,pgAdmin,commandlinetools'
)
$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList $args
# 0 = success, 3010 = success but reboot pending
if ($code -notin @(0,3010)) { Write-StageLog "[20_postgres] installer exit $code" 'ERROR'; exit 1 }

if (Test-Pg15Installed) {
    Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
    Write-StageLog "[20_postgres] OK" 'OK'; exit 0
}
Write-StageLog "[20_postgres] FAILED - service not found after install" 'ERROR'
exit 1
```

- [ ] **Step 2: Write 21_install_postgis.ps1**

Create `空间质检/deploy/scripts/21_install_postgis.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[21_postgis] begin"
if (Test-PostgisInstalled) { Write-StageLog "[21_postgis] PostGIS already present -> SKIP" 'SKIP'; exit 0 }
if ($DryRun) { Write-StageLog "[21_postgis] DRY-RUN would install PostGIS 3.6.1 bundle" 'INFO'; exit 0 }

Assert-Admin
$exe = Join-Path (Get-OfflineDir) 'installers\postgis-bundle-pg15x64-setup-3.6.1-1.exe'
# PostGIS bundle installer is NSIS-based: silent = /S
$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList @('/S')
if ($code -notin @(0,3010)) { Write-StageLog "[21_postgis] installer exit $code" 'ERROR'; exit 1 }

if (Test-PostgisInstalled) { Write-StageLog "[21_postgis] OK" 'OK'; exit 0 }
Write-StageLog "[21_postgis] FAILED - postgis.control not found" 'ERROR'
exit 1
```

- [ ] **Step 3: Dry-run both**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\20_install_postgres.ps1" -DryRun`
Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\21_install_postgis.ps1" -DryRun`
Expected（本机 PG16 存在但无 PG15）：两脚本进入 DRY-RUN 分支，退出码 0。

- [ ] **Step 4: Commit**

```bash
git add 空间质检/deploy/scripts/20_install_postgres.ps1 空间质检/deploy/scripts/21_install_postgis.ps1
git commit -m "feat(deploy): add PostgreSQL 15 and PostGIS install stages"
```

---

## Task 7: 22_install_ace.ps1 + 25_install_vcredist.ps1（非关键）

**Files:**
- Create: `空间质检/deploy/scripts/22_install_ace.ps1`
- Create: `空间质检/deploy/scripts/25_install_vcredist.ps1`

**Interfaces:**
- Consumes: `Test-AceInstalled`、`Test-VcRuntimeInstalled`、`Invoke-SilentInstaller`、`Get-OfflineDir`、`Write-StageLog`（Task 1）。
- Produces: ACE ODBC 驱动；VC++ 运行库。**失败告警但不中断**（编排器已把 22/25 列为 non-critical；此处对「安装器缺失」也自行 `exit 0` + WARN）。

- [ ] **Step 1: Write 22_install_ace.ps1**

Create `空间质检/deploy/scripts/22_install_ace.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[22_ace] begin"
if (Test-AceInstalled) { Write-StageLog "[22_ace] ACE already present -> SKIP" 'SKIP'; exit 0 }
$exe = Join-Path (Get-OfflineDir) 'installers\accessdatabaseengine_X64.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-StageLog "[22_ace] installer not bundled -> SKIP (MDB feature degraded)" 'WARN'; exit 0
}
if ($DryRun) { Write-StageLog "[22_ace] DRY-RUN would install ACE 2016 x64" 'INFO'; exit 0 }

$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList @('/quiet','/norestart')
if ($code -notin @(0,3010)) { Write-StageLog "[22_ace] installer exit $code (non-fatal)" 'WARN'; exit 0 }
if (Test-AceInstalled) { Write-StageLog "[22_ace] OK" 'OK' } else { Write-StageLog "[22_ace] post-check failed (non-fatal)" 'WARN' }
exit 0
```

- [ ] **Step 2: Write 25_install_vcredist.ps1**

Create `空间质检/deploy/scripts/25_install_vcredist.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[25_vcredist] begin"
if (Test-VcRuntimeInstalled) { Write-StageLog "[25_vcredist] VC++ runtime already present -> SKIP" 'SKIP'; exit 0 }
$exe = Join-Path (Get-OfflineDir) 'installers\MSVBCRT_AIO_x64.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-StageLog "[25_vcredist] installer not bundled -> SKIP" 'WARN'; exit 0
}
if ($DryRun) { Write-StageLog "[25_vcredist] DRY-RUN would install VC++ runtime AIO x64" 'INFO'; exit 0 }

$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList @('/quiet','/norestart')
if ($code -notin @(0,3010)) { Write-StageLog "[25_vcredist] installer exit $code (non-fatal)" 'WARN'; exit 0 }
Write-StageLog "[25_vcredist] OK" 'OK'
exit 0
```

- [ ] **Step 3: Dry-run both**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\22_install_ace.ps1" -DryRun`
Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\25_install_vcredist.ps1" -DryRun`
Expected: 各自 SKIP 或 DRY-RUN 提示，退出码 0。

- [ ] **Step 4: Commit**

```bash
git add 空间质检/deploy/scripts/22_install_ace.ps1 空间质检/deploy/scripts/25_install_vcredist.ps1
git commit -m "feat(deploy): add ACE and VC++ runtime stages (non-critical)"
```

---

## Task 8: 30_setup_redis.ps1

**Files:**
- Create: `空间质检/deploy/scripts/30_setup_redis.ps1`

**Interfaces:**
- Consumes: `Test-RedisRunning`、`Test-PortListening`、`Get-OfflineDir`、`Get-ProjectRoot`、`Write-StageLog`（Task 1）。
- Produces: Redis 运行于 6379；注册开机自启计划任务 `VCS-Redis`（因 Redis 8.10 msys 构建不支持 `--service-install`）。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/30_setup_redis.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[30_redis] begin"
if (Test-RedisRunning) {
    if (-not (Get-ScheduledTask -TaskName 'VCS-Redis' -ErrorAction SilentlyContinue)) {
        Write-StageLog "[30_redis] running but no autostart task" 'WARN'
    }
    Write-StageLog "[30_redis] Redis already listening on 6379 -> SKIP" 'SKIP'; exit 0
}
if ($DryRun) { Write-StageLog "[30_redis] DRY-RUN would start Redis + register task VCS-Redis" 'INFO'; exit 0 }

Assert-Admin
$redisDir = Join-Path (Get-OfflineDir) 'redis'
$exe = Join-Path $redisDir 'redis-server.exe'
if (-not (Test-Path -LiteralPath $exe)) { Write-StageLog "[30_redis] redis-server.exe missing" 'ERROR'; exit 1 }

# start now (hidden), from the redis dir so redis.conf + dump.rdb resolve
Start-Process -FilePath $exe -ArgumentList 'redis.conf' -WorkingDirectory $redisDir -WindowStyle Hidden

# wait up to 10s for the port
$ok = $false
for ($i=0; $i -lt 20; $i++) { if (Test-PortListening -Port 6379) { $ok = $true; break }; Start-Sleep -Milliseconds 500 }
if (-not $ok) { Write-StageLog "[30_redis] port 6379 not listening after start" 'ERROR'; exit 1 }

# register autostart scheduled task (SYSTEM, at startup)
$taskCmd = "`"$exe`" redis.conf"
$action  = New-ScheduledTaskAction -Execute $exe -Argument 'redis.conf' -WorkingDirectory $redisDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'VCS-Redis' -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null

Write-StageLog "[30_redis] OK (started + task VCS-Redis registered)" 'OK'
exit 0
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\30_setup_redis.ps1" -DryRun`
Expected（本机 Redis 若已在跑）：`Redis already listening on 6379 -> SKIP`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/30_setup_redis.ps1
git commit -m "feat(deploy): add 30_setup_redis stage with autostart task"
```

---

## Task 9: 40_setup_venv.ps1

**Files:**
- Create: `空间质检/deploy/scripts/40_setup_venv.ps1`

**Interfaces:**
- Consumes: `Test-VenvReady`、`Get-OfflineDir`、`Get-ProjectRoot`、`Write-StageLog`（Task 1）。
- Produces: `空间质检/.venv`（含 uvicorn/celery/flower 可执行，pywin32 已 postinstall）。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/40_setup_venv.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[40_venv] begin"
$pr = Get-ProjectRoot
$venv = Join-Path $pr '.venv'
$py   = Join-Path $venv 'Scripts\python.exe'

if (Test-VenvReady -ProjectRoot $pr) { Write-StageLog "[40_venv] venv ready -> SKIP" 'SKIP'; exit 0 }
if ($DryRun) { Write-StageLog "[40_venv] DRY-RUN would create .venv and install wheels offline" 'INFO'; exit 0 }

if (-not (Test-Python313Installed)) { Write-StageLog "[40_venv] py -3.13 not available" 'ERROR'; exit 1 }

# create venv only if python.exe absent (idempotent)
if (-not (Test-Path -LiteralPath $py)) {
    Write-StageLog "[40_venv] creating venv at $venv" 'INFO'
    & py -3.13 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-StageLog "[40_venv] venv creation failed" 'ERROR'; exit 1 }
}

$wheels = Join-Path (Get-OfflineDir) 'wheels'
Write-StageLog "[40_venv] installing wheels offline from $wheels" 'INFO'
& $py -m pip install --no-index --find-links $wheels -r (Join-Path $pr 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Write-StageLog "[40_venv] pip install failed" 'ERROR'; exit 1 }

# pywin32 postinstall (registers DLLs; safe to re-run)
$pwi = Join-Path $venv 'Scripts\pywin32_postinstall.py'
if (Test-Path -LiteralPath $pwi) {
    Write-StageLog "[40_venv] running pywin32 postinstall" 'INFO'
    & $py $pwi -install
    if ($LASTEXITCODE -ne 0) { Write-StageLog "[40_venv] pywin32 postinstall warning (non-fatal)" 'WARN' }
}

if (Test-VenvReady -ProjectRoot $pr) { Write-StageLog "[40_venv] OK" 'OK'; exit 0 }
Write-StageLog "[40_venv] FAILED - uvicorn.exe not found after install" 'ERROR'
exit 1
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\40_setup_venv.ps1" -DryRun`
Expected（本机已有 .venv）：`venv ready -> SKIP`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/40_setup_venv.ps1
git commit -m "feat(deploy): add 40_setup_venv stage with offline wheel install"
```

---

## Task 10: 50_init_database.ps1

**Files:**
- Create: `空间质检/deploy/scripts/50_init_database.ps1`

**Interfaces:**
- Consumes: `Get-ProjectRoot`、`Write-StageLog`（Task 1）。
- Produces: 数据库 `vcs_db`（含 `postgis` 扩展，迁移至 alembic head）。
- 约定：PG 口令来自 `$env:VCS_PG_SUPERPASSWORD`（缺省 `postgres`）。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/50_init_database.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[50_db] begin"
$pr = Get-ProjectRoot
$pwd = if ($env:VCS_PG_SUPERPASSWORD) { $env:VCS_PG_SUPERPASSWORD } else { 'postgres' }
$psql = 'C:\Program Files\PostgreSQL\15\bin\psql.exe'
$alembic = Join-Path $pr '.venv\Scripts\alembic.exe'

if (-not (Test-Path -LiteralPath $psql)) { Write-StageLog "[50_db] psql not found: $psql" 'ERROR'; exit 1 }
if (-not (Test-Path -LiteralPath $alembic)) { Write-StageLog "[50_db] alembic not found (run stage 40 first)" 'ERROR'; exit 1 }

$env:PGPASSWORD = $pwd
$env:PGCLIENTENCODING = 'UTF8'

# create db if absent
$exists = & $psql -U postgres -h localhost -p 5432 -tAc "SELECT 1 FROM pg_database WHERE datname='vcs_db'"
if ($exists -ne '1') {
    if ($DryRun) { Write-StageLog "[50_db] DRY-RUN would create database vcs_db" 'INFO'; exit 0 }
    Write-StageLog "[50_db] creating database vcs_db" 'INFO'
    & $psql -U postgres -h localhost -p 5432 -c 'CREATE DATABASE vcs_db'
    if ($LASTEXITCODE -ne 0) { Write-StageLog "[50_db] createdb failed" 'ERROR'; exit 1 }
} elseif ($DryRun) {
    Write-StageLog "[50_db] vcs_db exists" 'INFO'
}

if ($DryRun) { Write-StageLog "[50_db] DRY-RUN done" 'INFO'; exit 0 }

# enable postgis
& $psql -U postgres -h localhost -p 5432 -d vcs_db -c 'CREATE EXTENSION IF NOT EXISTS postgis'
if ($LASTEXITCODE -ne 0) { Write-StageLog "[50_db] CREATE EXTENSION postgis failed" 'ERROR'; exit 1 }

# migrate to head
Push-Location $pr
& $alembic upgrade head
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { Write-StageLog "[50_db] alembic upgrade failed" 'ERROR'; exit 1 }

Write-StageLog "[50_db] OK" 'OK'
exit 0
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\50_init_database.ps1" -DryRun`
Expected: 打印 `vcs_db exists` 或 `DRY-RUN would create database`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/50_init_database.ps1
git commit -m "feat(deploy): add 50_init_database stage"
```

---

## Task 11: 60_seed_and_key.ps1

**Files:**
- Create: `空间质检/deploy/scripts/60_seed_and_key.ps1`

**Interfaces:**
- Consumes: `Get-ProjectRoot`、`Get-OfflineDir`、`Get-DeployRoot`、`New-RandomKey`、`Test-PortListening`、`Write-StageLog`（Task 1）。
- Produces: `空间质检/.env`（含随机 `ADMIN_INIT_KEY`）；`deploy/.install_state.json`（含首个 API Key）；种子标准导入 vcs_db。
- 契约：`POST http://127.0.0.1:45679/api/v1/admin/apikeys`，头 `X-Admin-Init-Key: <ADMIN_INIT_KEY>`，body `{"name":"bootstrap"}`，响应字段 `api_key`。

- [ ] **Step 1: Write the script**

Create `空间质检/deploy/scripts/60_seed_and_key.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[60_seed] begin"
$pr = Get-ProjectRoot
$deployRoot = Get-DeployRoot
$stateFile = Join-Path $deployRoot '.install_state.json'
$envFile = Join-Path $pr '.env'
$py = Join-Path $pr '.venv\Scripts\python.exe'

# already done?
if ((Test-Path -LiteralPath $stateFile) -and (Test-Path -LiteralPath $envFile)) {
    Write-StageLog "[60_seed] .env + install state present -> SKIP" 'SKIP'; exit 0
}
if ($DryRun) { Write-StageLog "[60_seed] DRY-RUN would write .env, create API key, import seed" 'INFO'; exit 0 }

if (-not (Test-Path -LiteralPath $py)) { Write-StageLog "[60_seed] venv python missing (run stage 40)" 'ERROR'; exit 1 }

# 1) write .env (from .env.example, override secrets/URLs)
$adminKey = New-RandomKey -Bytes 32
$tmpl = Get-Content -LiteralPath (Join-Path $pr '.env.example') -Raw -Encoding UTF8
$tmpl = $tmpl -replace '(?m)^ADMIN_INIT_KEY=.*$', "ADMIN_INIT_KEY=$adminKey"
$tmpl | Set-Content -LiteralPath $envFile -Encoding UTF8
Write-StageLog "[60_seed] wrote .env (ADMIN_INIT_KEY generated)" 'OK'

# 2) start a temporary API to create the first key + import seed
$proc = Start-Process -FilePath $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','45679' `
    -WorkingDirectory $pr -WindowStyle Hidden -PassThru
$ready = $false
for ($i=0; $i -lt 60; $i++) {
    if (Test-PortListening -Port 45679) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:45679/health' -TimeoutSec 3 | Out-Null; $ready=$true; break } catch {}
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; Write-StageLog "[60_seed] temp API did not become healthy" 'ERROR'; exit 1 }

try {
    # create API key
    $body = '{"name":"bootstrap"}'
    $resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:45679/api/v1/admin/apikeys' `
        -Headers @{ 'X-Admin-Init-Key' = $adminKey } -ContentType 'application/json' -Body $body
    $apiKey = $resp.api_key
    if (-not $apiKey) { throw "no api_key in response" }
    Write-StageLog "[60_seed] bootstrap API key created" 'OK'

    # import seed (reference standards) if any
    $seed = Join-Path (Get-OfflineDir) 'seed'
    $jsons = @(Get-ChildItem -LiteralPath $seed -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($jsons.Count -gt 0) {
        Write-StageLog "[60_seed] importing $($jsons.Count) seed standards..." 'INFO'
        & $py (Join-Path $pr 'scripts\import_reference_standards.py') --base 'http://127.0.0.1:45679' --key $apiKey --dir $seed
        if ($LASTEXITCODE -ne 0) { Write-StageLog "[60_seed] seed import reported failures (non-fatal)" 'WARN' }
    } else { Write-StageLog "[60_seed] no seed files -> skip import" 'WARN' }

    # persist state
    [ordered]@{ admin_init_key=$adminKey; api_key=$apiKey; created=(Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-StageLog "" 'INFO'
    Write-StageLog "================ BOOTSTRAP CREDENTIALS ================" 'OK'
    Write-StageLog "ADMIN_INIT_KEY : $adminKey" 'OK'
    Write-StageLog "API_KEY        : $apiKey" 'OK'
    Write-StageLog "=======================================================" 'OK'
}
finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

Write-StageLog "[60_seed] OK" 'OK'
exit 0
```

- [ ] **Step 2: Dry-run**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\60_seed_and_key.ps1" -DryRun`
Expected: `DRY-RUN would write .env, create API key, import seed`，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add 空间质检/deploy/scripts/60_seed_and_key.ps1
git commit -m "feat(deploy): add 60_seed_and_key stage (env + bootstrap key + seed import)"
```

---

## Task 12: 90_start_services.ps1 + 99_verify.ps1

**Files:**
- Create: `空间质检/deploy/scripts/90_start_services.ps1`
- Create: `空间质检/deploy/scripts/99_verify.ps1`

**Interfaces:**
- Consumes: `Get-ProjectRoot`、`Test-PortListening`、`Write-StageLog`（Task 1）。
- Produces: uvicorn(45679) + celery worker + flower(5555) 运行；健康自检通过；打开浏览器。

- [ ] **Step 1: Write 90_start_services.ps1**

Create `空间质检/deploy/scripts/90_start_services.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[90_start] begin"
$pr = Get-ProjectRoot
$py = Join-Path $pr '.venv\Scripts\python.exe'
$uvicorn = Join-Path $pr '.venv\Scripts\uvicorn.exe'
$celery  = Join-Path $pr '.venv\Scripts\celery.exe'
$port = 45679

if ($DryRun) { Write-StageLog "[90_start] DRY-RUN would start API+celery+flower on $port" 'INFO'; exit 0 }

# stop old
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*app.main:app*' -or $_.CommandLine -like '*celery*worker*' -or $_.CommandLine -like '*flower*' } |
    ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }
Start-Sleep -Seconds 3

# API
Start-Process -FilePath $uvicorn -ArgumentList 'app.main:app','--host','127.0.0.1','--port',"$port" `
    -WorkingDirectory $pr -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pr 'server_out.log') -RedirectStandardError (Join-Path $pr 'server_err.log')
# Celery worker (Windows -> solo pool)
Start-Process -FilePath $celery -ArgumentList '-A','app.workers.celery_app','worker','--loglevel=info','--pool=solo' `
    -WorkingDirectory $pr -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pr 'worker_out.log') -RedirectStandardError (Join-Path $pr 'worker_err.log')
# Flower
Start-Process -FilePath $py -ArgumentList '-m','celery','-A','app.workers.celery_app','flower','--port=5555' `
    -WorkingDirectory $pr -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pr 'flower_out.log') -RedirectStandardError (Join-Path $pr 'flower_err.log')

# wait for health
$ok = $false
for ($i=0; $i -lt 40; $i++) {
    if (Test-PortListening -Port $port) {
        try { Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3 | Out-Null; $ok=$true; break } catch {}
    }
    Start-Sleep -Seconds 1
}
if (-not $ok) { Write-StageLog "[90_start] health check failed; see server_err.log" 'ERROR'; exit 1 }

Start-Process "http://127.0.0.1:$port/app/"
Write-StageLog "[90_start] OK (frontend http://127.0.0.1:$port/app/)" 'OK'
exit 0
```

- [ ] **Step 2: Write 99_verify.ps1**

Create `空间质检/deploy/scripts/99_verify.ps1`:

```powershell
param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[99_verify] begin"
$pr = Get-ProjectRoot
$stateFile = Join-Path (Get-DeployRoot) '.install_state.json'
$port = 45679

$rows = @()
$rows += [pscustomobject]@{ Item='Python'; Detail=(& py -3.13 --version 2>&1) }
$rows += [pscustomobject]@{ Item='PostgreSQL'; Detail=(& 'C:\Program Files\PostgreSQL\15\bin\psql.exe' --version 2>&1) }
$rows += [pscustomobject]@{ Item='PostGIS'; Detail=("installed=" + (Test-PostgisInstalled)) }
$rows += [pscustomobject]@{ Item='Redis:6379'; Detail=("listening=" + (Test-PortListening -Port 6379)) }
$rows += [pscustomobject]@{ Item='API:45679'; Detail=("listening=" + (Test-PortListening -Port $port)) }
$rows += [pscustomobject]@{ Item='Flower:5555'; Detail=("listening=" + (Test-PortListening -Port 5555)) }
$rows += [pscustomobject]@{ Item='ACE'; Detail=("installed=" + (Test-AceInstalled)) }

Write-StageLog "---------------- VERIFY REPORT ----------------" 'INFO'
foreach ($r in $rows) { Write-StageLog ("{0,-14} {1}" -f $r.Item, $r.Detail) 'INFO' }

if (Test-Path -LiteralPath $stateFile) {
    $s = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-StageLog "ADMIN_INIT_KEY : $($s.admin_init_key)" 'OK'
    Write-StageLog "API_KEY        : $($s.api_key)" 'OK'
}
Write-StageLog "Frontend : http://127.0.0.1:$port/app/" 'OK'
Write-StageLog "API Docs : http://127.0.0.1:$port/docs" 'OK'
Write-StageLog "Flower   : http://127.0.0.1:5555" 'OK'
Write-StageLog "-----------------------------------------------" 'INFO'
Write-StageLog "[99_verify] OK" 'OK'
exit 0
```

- [ ] **Step 3: Dry-run + real start verification**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\90_start_services.ps1" -DryRun`
Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\99_verify.ps1"`
Expected: 90 打印 DRY-RUN 提示；99 打印 VERIFY REPORT（API:45679 listening=True），退出码 0。

- [ ] **Step 4: Commit**

```bash
git add 空间质检/deploy/scripts/90_start_services.ps1 空间质检/deploy/scripts/99_verify.ps1
git commit -m "feat(deploy): add service start and verification stages"
```

---

## Task 13: README、旧脚本退役、文档同步

**Files:**
- Create: `空间质检/deploy/README.md`（中文，UTF-8）
- Modify: `空间质检/deploy_windows.bat`
- Modify: `空间质检/BASELINE.md`
- Modify: `空间质检/CONTEXT.md`
- Create: `空间质检/docs/revision_v2_23_0.md`

**Interfaces:**
- Consumes: 全部前面任务。
- Produces: 面向实施人员的说明与项目文档同步。

- [ ] **Step 1: Write deploy/README.md**

Create `空间质检/deploy/README.md`（内容要点：交付清单、构建步骤、安装步骤、凭证、排障、卸载/重装）：包含：
- 「本目录用途」；「离线资源 `offline/` 由 `build_bundle.ps1` 在联网机生成」。
- 「交付」：整目录拷贝 `空间质检\`，确认 `deploy\offline\manifest.json` 存在。
- 「安装」：右键 `deploy\deploy_offline.bat` → 以管理员运行；或双击（会 UAC 自提升）。
- 「凭证」：安装末尾打印的 `ADMIN_INIT_KEY` 与 `API_KEY`，同时保存在 `deploy\.install_state.json`。
- 「续跑/干跑」：`deploy_offline.bat --from 40`、`--dry-run`。
- 「排障」：日志 `deploy\logs\`；PG 口令经 `VCS_PG_SUPERPASSWORD` 传入；非关键阶段（ACE/VC++）失败不阻塞。
- 「重装」：删 `.venv` 重跑 `--from 40`；删库重跑 `--from 50`。

- [ ] **Step 2: Retire legacy deploy_windows.bat**

将 `空间质检/deploy_windows.bat` 全文替换为薄封装（ASCII）：

```bat
@echo off
rem Legacy entry kept for compatibility. Delegates to the offline deploy subsystem.
echo This script now delegates to deploy\deploy_offline.bat
call "%~dp0deploy\deploy_offline.bat" %*
exit /b %errorlevel%
```

- [ ] **Step 3: Update BASELINE.md / CONTEXT.md / revision**

- `BASELINE.md`：第 2 节目录结构加 `deploy/`；第 6 节脚本清单加 `deploy/deploy_offline.bat`、`deploy/build_bundle.ps1`、`deploy/scripts/*`；第 15 节版本号提升至 v2.23.0；顶部「最后更新日期」= 2026-10-09。
- `CONTEXT.md`：版本号 → v2.23.0。
- 新建 `docs/revision_v2_23_0.md`：记录本版新增「Windows 离线一键部署子系统（deploy/）」、版本矩阵、验收=丙、已知偏差（开发 PG16 vs 交付 PG15）。

- [ ] **Step 4: Final full dry-run (integration check)**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File "空间质检\deploy\scripts\deploy.ps1" -DryRun`
Expected: 全部阶段打印 `>>> / <<< ... OK`（ACE/VC++ 可能 SKIP），末行 `RESULT: SUCCESS`，退出码 0。

- [ ] **Step 5: Commit**

```bash
git add 空间质检/deploy/README.md 空间质检/deploy_windows.bat 空间质检/BASELINE.md 空间质检/CONTEXT.md 空间质检/docs/revision_v2_23_0.md
git commit -m "docs(deploy): add offline deploy README and sync project docs"
```

---

## Self-Review

**1. Spec coverage:**
- §3 版本矩阵 → Task 3（构建）+ Task 5/6/7/8（安装）。✅
- §4 目录布局 → File Structure + Task 3（offline 生成）+ Task 1/4（scripts）。✅
- §5 部署流程 12 阶段 → Task 2(00)、5(10)、6(20/21)、7(22/25)、8(30)、9(40)、10(50)、11(60)、12(90/99)。✅
- §6 健壮性（幂等/校验/日志/重跑/自提升/失败分级）→ Task 1（校验/日志）、Task 2（manifest）、Task 4（自提升/重跑/分级）。✅
- §7 数据库/密钥/种子（丙）→ Task 10（库+postgis+alembic）、Task 11（.env+key+seed）。✅
- §8 交付与体积 → Task 3（manifest 记录体量）+ Task 13 README。✅
- §9 验证策略 → 各 Task 的 Dry-run + Task 12/13 集成 dry-run。PG16/15 兼容性核查：Task 10 使用 alembic head，实际迁移在目标 PG15 上执行即验证。✅
- §10 默认决策 → 端口 45679/Flower 包含（Task 12）、口令 fallback（Task 6/10）、ACE 降级（Task 7）。✅
- §11 风险 → PostGIS 参数（Task 6）、sdist 问题（`--only-binary=:all:`，Task 3）、PG 版本差（Task 10/13）。✅

**2. Placeholder scan:** 无 TBD/TODO；每个脚本步骤含完整代码。build_bundle 的 ACE URL 给了具体链接并附「失效则改用官方页」的可执行回退。

**3. Type consistency:** 阶段脚本统一 `param([switch]$DryRun)`；`Write-StageLog` 的 `-Level` 集合 `INFO/OK/WARN/ERROR/SKIP` 全一致；`Assert-Manifest` 返回清单对象、消费方用 `.files.Count`；`New-RandomKey`/`Get-FileSha256` 命名在 Task 1 定义、Task 3/11 复用一致。

**备注（执行顺序依赖）：** Task 3 的 `build_bundle.ps1` 依赖 Task 1 的 `common.ps1`（`Get-OfflineDir` 等），故须在 Task 1 之后执行；Task 2 的 preflight 校验需在 Task 3 构建后才有 manifest，Step 2 的首跑失败属预期。
