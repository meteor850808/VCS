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
function Read-JsonFile {
    # Never throws. A missing or unreadable artifact must show up as a failed
    # assertion, not as a terminating error that aborts the whole suite (the
    # runner sets $ErrorActionPreference = 'Stop').
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { return $null }
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

# Get-PostgresInstallerArg: unattended + explicit no-UI + installer-accepted locale
$pa = Get-PostgresInstallerArg -SuperPassword 's3cret'
Assert-True ($pa -contains '--mode') "PG args include --mode"
Assert-Equal $pa[$pa.IndexOf('--mode')+1] 'unattended' "PG args mode=unattended"
Assert-Equal $pa[$pa.IndexOf('--unattendedmodeui')+1] 'none' "PG args unattendedmodeui=none"
Assert-Equal $pa[$pa.IndexOf('--locale')+1] 'zh-CN' "PG args locale=zh-CN (installer-accepted form)"
Assert-True ($pa -contains 'C:\Program Files\PostgreSQL\15') "PG args keep space-containing prefix"
Assert-True ($pa -contains 's3cret') "PG args carry superpassword"
Assert-True (-not ($pa -contains 'Chinese_China.936')) "PG args drop installer-rejected locale literal"

# Get-PostgresInstallDecision: pure policy.
# v2.23.2 policy B: never uninstall automatically; only a *healthy* 15 is reused.
Assert-Equal (Get-PostgresInstallDecision -Install $null) 'install' "decision: none -> install"
Assert-Equal (Get-PostgresInstallDecision -Install ([pscustomobject]@{ Major=16 })) 'conflict' "decision: major 16 -> conflict"
Assert-Equal (Get-PostgresInstallDecision -Install ([pscustomobject]@{ Major=0 })) 'unknown' "decision: major 0 -> unknown (never guess)"
$svc16 = Get-Service -Name 'postgresql-x64-16' -ErrorAction SilentlyContinue
$hp15 = [pscustomobject]@{ Major=15; Prefix='C:\Program Files\PostgreSQL\15'; ServiceName='postgresql-x64-15'; Version='15.15' }
$pg15psql = Test-Path -LiteralPath 'C:\Program Files\PostgreSQL\15\bin\psql.exe'
if ($pg15psql) {
    Assert-Equal (Get-PostgresInstallDecision -Install $hp15) 'skip' "decision: healthy PG15 (psql on disk) -> skip"
} else {
    Assert-Equal (Get-PostgresInstallDecision -Install $hp15) 'install' "decision: PG15 without psql.exe -> install (broken remnant)"
}
Assert-Equal (Get-PostgresInstallDecision -Install ([pscustomobject]@{ Major=15 })) 'skip' "decision: bare PG15 (no probe data) -> skip"
if ($svc16) {
    $aliased = [pscustomobject]@{ Major=15; Prefix='C:\Program Files\PostgreSQL\16'; ServiceName=$svc16.Name; Version='16.4' }
    Assert-True ((Get-PostgresInstallDecision -Install $aliased) -in @('install','skip')) "decision: PG15 major with a live service never aborts"
}

# Get-InstalledPostgres: never throws; $null or an object exposing Major
$ip = Get-InstalledPostgres
Assert-True (($null -eq $ip) -or ($ip.PSObject.Properties.Name -contains 'Major')) "Get-InstalledPostgres: null or object with Major"

# (Resolve-PostgresUninstaller was removed in v2.23.2 - the deploy never removes
#  an existing PostgreSQL automatically, so there is no uninstaller to resolve.)

# Format-InstallerArg: quotes whitespace-containing elements (Start-Process does not)
$qa = Format-InstallerArg -ArgumentList @('--prefix','C:\Program Files\PostgreSQL\15','/S')
Assert-Equal $qa[0] '--prefix' "Format-InstallerArg leaves flag unchanged"
Assert-Equal $qa[1] '"C:\Program Files\PostgreSQL\15"' "Format-InstallerArg quotes path with space"
Assert-Equal $qa[2] '/S' "Format-InstallerArg leaves slash-flag unchanged"
Assert-Equal (Format-InstallerArg -ArgumentList @()).Count 0 "Format-InstallerArg empty -> empty"

# Invoke-SilentInstaller must deliver a path-with-space as ONE argv token (regression:
# previously it was split into [C:\Program] [Files\...] -> installer exit 1).
$ad  = Join-Path $env:TEMP ("vcs_argdump_" + [guid]::NewGuid().ToString('N') + ".bat")
$ado = Join-Path $env:TEMP ("vcs_argdump_" + [guid]::NewGuid().ToString('N') + ".out")
$batLines = @(
    '@echo off',
    ':loop',
    'if "%~1"=="" goto :eof',
    ('echo [%~1]>>"{0}"' -f $ado),
    'shift',
    'goto loop'
)
Set-Content -LiteralPath $ad -Value $batLines -Encoding ASCII
Invoke-SilentInstaller -FilePath $ad -ArgumentList @('--prefix','C:\Program Files\PostgreSQL\15') | Out-Null
$got = @(Get-Content -LiteralPath $ado)
Assert-True ($got -contains '[C:\Program Files\PostgreSQL\15]') "Invoke-SilentInstaller keeps path-with-space as one token"
Assert-True ($got -contains '[--prefix]') "Invoke-SilentInstaller delivers flag unchanged"
Remove-Item -LiteralPath $ad,$ado -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# v2.23.2 regressions
# ---------------------------------------------------------------------------

function Invoke-Stage {
    # Runs one real stage script in a child powershell, returns its exit code,
    # its console output and (if given) the contents of its log file.
    # $Script is a bare stage file name under deploy\scripts, or an absolute path.
    #
    # -ProjectRoot isolates the run: the whole deploy tree is mirrored into that
    # throwaway root and the stage is executed from the COPY. This matters because
    # a stage resolves its project root from its own $PSScriptRoot two levels up,
    # and several stages mutate real state (40_setup_venv rebuilds .venv,
    # 50/60 touch the database and .env). Running them against the live repository
    # once deleted the developer's virtualenv. Never do that again: anything that
    # runs a stage must pass an isolated -ProjectRoot.
    param(
        [Parameter(Mandatory)][string]$Script,
        [string[]]$ExtraArgs = @(),
        [string]$StageLog,
        [string]$ProjectRoot
    )
    $realDeploy = Get-DeployRoot
    if ($ProjectRoot) {
        $root = $ProjectRoot
        if (-not (Test-Path -LiteralPath (Join-Path $root 'deploy\scripts\common.ps1'))) {
            $mirror = Join-Path $root 'deploy'
            New-Item -ItemType Directory -Path $mirror -Force | Out-Null
            # Copy the source's CHILDREN, not the directory itself: Copy-Item
            # -LiteralPath <dir> -Destination <dir> nests it (deploy\deploy), and
            # -Exclude is ignored for -LiteralPath. Both mistakes silently produce a
            # tree with no stage scripts, which powershell.exe reports as the opaque
            # exit code -196608 (0xFFFF0000) instead of a useful error.
            Get-ChildItem -LiteralPath $realDeploy -Force |
                Where-Object { $_.Name -notin @('offline', 'logs', 'diagnostics') } |
                ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $mirror -Recurse -Force }
            if (-not (Test-Path -LiteralPath (Join-Path $mirror 'scripts\common.ps1'))) {
                throw "stage mirror is incomplete: $mirror\scripts\common.ps1 missing"
            }
        }
        if ([System.IO.Path]::IsPathRooted($Script)) {
            $target = Join-Path (Join-Path $root 'deploy\scripts') (Split-Path $Script -Leaf)
        } else {
            $target = Join-Path (Join-Path $root 'deploy\scripts') $Script
        }
    } else {
        $target = if ([System.IO.Path]::IsPathRooted($Script)) { $Script }
                  else { Join-Path (Join-Path $realDeploy 'scripts') $Script }
    }
    $out = Join-Path $env:TEMP ("vcs_stage_" + [guid]::NewGuid().ToString('N') + ".out")
    $err = Join-Path $env:TEMP ("vcs_stage_" + [guid]::NewGuid().ToString('N') + ".err")
    # Never let a test write into the live deploy\logs\: when no explicit stage log
    # was requested, use a throwaway file. The suite must not leave anything behind
    # in the delivery directory it is validating.
    $ownsLog = $false
    if ($StageLog) {
        $env:VCS_DEPLOY_LOG = $StageLog
    } else {
        $env:VCS_DEPLOY_LOG = Join-Path $env:TEMP ("vcs_stagelog_" + [guid]::NewGuid().ToString('N') + ".log")
        $ownsLog = $true
    }
    try {
        $stageLogPath = $env:VCS_DEPLOY_LOG
        $a = @('-NoProfile','-ExecutionPolicy','Bypass','-File', ('"{0}"' -f $target)) + $ExtraArgs
        # Capture stderr too: a stage that dies on an unhandled exception explains
        # itself there, and a bare exit code is not enough to debug with.
        $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $a -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err
        $text = ''
        if (Test-Path -LiteralPath $out) { $text = (Get-Content -LiteralPath $out -Raw -ErrorAction SilentlyContinue) }
        if (Test-Path -LiteralPath $err) {
            $et = Get-Content -LiteralPath $err -Raw -ErrorAction SilentlyContinue
            if ($et) { $text = "$text`n[stderr]`n$et" }
        }
        # The child writes its log through the environment; fold it into the text so
        # assertions can inspect it without a caller-supplied path.
        if ($ownsLog -and (Test-Path -LiteralPath $stageLogPath)) {
            $lt = Get-Content -LiteralPath $stageLogPath -Raw -ErrorAction SilentlyContinue
            if ($lt) { $text = "$text`n[log]`n$lt" }
        }
        return [pscustomobject]@{ Exit = [int]$p.ExitCode; Text = [string]$text }
    } finally {
        Remove-Item -LiteralPath $out,$err -Force -ErrorAction SilentlyContinue
        if ($ownsLog) { Remove-Item -LiteralPath $stageLogPath -Force -ErrorAction SilentlyContinue }
        Remove-Item Env:\VCS_DEPLOY_LOG -ErrorAction SilentlyContinue
    }
}

# Every stage invocation below runs against this throwaway root, so the live
# repository (its .venv, .env and database) is never touched. Leftovers from a
# previous crashed run are cleared first so a failed run cannot accumulate them.
$stageRoot = Join-Path $env:TEMP ("vcs_stageroot_" + [guid]::NewGuid().ToString('N'))
Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'vcs_stageroot_*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

$pg15psql = Test-Path -LiteralPath 'C:\Program Files\PostgreSQL\15\bin\psql.exe'

# 50_init_database under -DryRun must NEVER fail. Regression: the psql existence
# check used to run before the DryRun branch, so '--dry-run' went red on every
# machine without PG15 and the operator concluded the package was broken.
$log50d = Join-Path $env:TEMP ("vcs_log50d_" + [guid]::NewGuid().ToString('N') + ".log")
$r50d = Invoke-Stage -Script '50_init_database.ps1' -ExtraArgs @('-DryRun') -StageLog $log50d -ProjectRoot $stageRoot
Assert-Equal $r50d.Exit 0 "50_init_database -DryRun exits 0 even without PG15"
$t50d = if (Test-Path -LiteralPath $log50d) { Get-Content -LiteralPath $log50d -Raw } else { '' }
if (-not $pg15psql) {
    Assert-True ($t50d -match 'DRY-RUN: psql not found') "50 -DryRun reports the missing psql as WARN"
    Assert-True ($t50d -notmatch '\[ERROR\]') "50 -DryRun emits no ERROR line"
}
Remove-Item -LiteralPath $log50d -Force -ErrorAction SilentlyContinue

# A real (non-dry) 50 run must still fail hard and say what to do next.
if (-not $pg15psql) {
    $r50 = Invoke-Stage -Script '50_init_database.ps1' -ProjectRoot $stageRoot
    Assert-Equal $r50.Exit 1 "50_init_database real run exits 1 when PG15 is absent"
    Assert-True ($r50.Text -match 'psql not found') "50 real failure names the missing psql"
    Assert-True ($r50.Text -match '--from 20') "50 real failure tells the operator to re-run from stage 20"
}

# 20_install_postgres on a machine that already runs another major (PG16 here):
# - must abort with an actionable message instead of installing over it
# - must NEVER run an uninstaller (regression: policy A silently removed the
#   operator's existing PostgreSQL after a [y/N] prompt with a default of N,
#   and then aborted anyway because the EDB uninstaller left its registry key).
$existingPg = Get-InstalledPostgres
$log20 = Join-Path $env:TEMP ("vcs_log20_" + [guid]::NewGuid().ToString('N') + ".log")
$r20 = Invoke-Stage -Script '20_install_postgres.ps1' -ExtraArgs @('-DryRun') -StageLog $log20 -ProjectRoot $stageRoot
$t20 = if (Test-Path -LiteralPath $log20) { Get-Content -LiteralPath $log20 -Raw } else { '' }
if ($existingPg -and [int]$existingPg.Major -ne 15) {
    Assert-Equal $r20.Exit 1 "20 aborts on a non-15 PostgreSQL"
    Assert-True ($t20 -match 'never removes an existing PostgreSQL automatically') "20 states it never auto-removes PG"
    Assert-True ($t20 -match 'deploy_offline\.bat --from 20') "20 gives the exact re-run command"
    Assert-True ($t20 -match 'netstat') "20 tells the operator how to check the port"
    Assert-True ($t20 -notmatch 'uninstall-postgresql') "20 never invokes an uninstaller"
    Assert-True ($t20 -notmatch 'Uninstall PostgreSQL') "20 never offers to uninstall"
} else {
    Assert-True ($r20.Exit -in @(0,1)) "20 dry-run on a 15/absent machine does not crash"
}

# 20 must refuse early (not after a 3-minute installer run) when the port is
# taken. Simulated by asserting the guard exists and is evaluated before the
# installer call in the script source.
$src20 = Get-Content -LiteralPath (Join-Path (Get-DeployRoot) 'scripts\20_install_postgres.ps1') -Raw
$iPort = $src20.IndexOf('Test-PortListening -Port $port')
$iInst = $src20.IndexOf('Invoke-SilentInstaller')
Assert-True ($iPort -ge 0) "20 has a port-occupancy guard"
Assert-True ($iInst -ge 0 -and $iPort -lt $iInst) "20 checks the port BEFORE calling the installer"
$iDry = $src20.IndexOf('if ($DryRun) { Write-StageLog "[20_postgres] DRY-RUN')
Assert-True ($iDry -ge 0 -and $iDry -lt $iInst) "20 dry-run exits before the installer call"
Assert-True ($src20 -notmatch 'Resolve-PostgresUninstaller') "20 no longer references the removed uninstaller helper"
$srcCommon = Get-Content -LiteralPath (Join-Path (Get-DeployRoot) 'scripts\common.ps1') -Raw
Assert-True ($srcCommon -notmatch 'function Resolve-PostgresUninstaller') "common.ps1 dropped Resolve-PostgresUninstaller"

# build_bundle.ps1 must strip Redis runtime artifacts. Regression: offline\redis
# shipped dump.rdb + redis_6379.pid. They are not in the manifest, so
# 00_preflight cannot catch them -> the target machine starts Redis with foreign
# data. Reproduced here on a throwaway project tree with stub inputs, so the test
# stays fast and never depends on the real installers being present.
$fakePrj = Join-Path $env:TEMP ("vcs_bundle_" + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path (Join-Path $fakePrj 'deploy\scripts') -Force | Out-Null
    foreach ($d in 'installers','redis','libredwg','wheels','seed') { New-Item -ItemType Directory -Path (Join-Path $fakePrj "deploy\offline\$d") -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $fakePrj 'tools\libredwg') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path (Get-DeployRoot) 'scripts\common.ps1') -Destination (Join-Path $fakePrj 'deploy\scripts\common.ps1')
    Copy-Item -LiteralPath (Join-Path (Get-DeployRoot) 'build_bundle.ps1') -Destination (Join-Path $fakePrj 'deploy\build_bundle.ps1')
    Set-Content -LiteralPath (Join-Path $fakePrj 'requirements.txt') -Value '# empty' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePrj 'tools\libredwg\dwg2dxf.exe') -Value 'stub' -Encoding ASCII

    # Stub installers. The seed is NOT stubbed: leaving -SeedDir unset makes
    # build_bundle.ps1 fall back to deploy/offline/seed, which is exactly the
    # self-sufficiency contract under test. An empty seed directory is a hard
    # failure by design (it would ship a bundle with no reference standards).
    $stub = Join-Path $fakePrj 'stub'
    New-Item -ItemType Directory -Path $stub -Force | Out-Null
    $stubPy = Join-Path $stub 'py.exe'; Set-Content -LiteralPath $stubPy -Value 'stub' -Encoding ASCII
    $stubVc = Join-Path $stub 'vc.exe'; Set-Content -LiteralPath $stubVc -Value 'stub' -Encoding ASCII

    foreach ($f in 'redis-server.exe','redis-cli.exe','redis.conf') {
        Copy-Item -LiteralPath (Join-Path (Get-ProjectRoot) "Redis8\$f") -Destination (Join-Path $fakePrj "deploy\offline\redis\$f")
    }
    Set-Content -LiteralPath (Join-Path $fakePrj 'deploy\offline\redis\dump.rdb') -Value 'stale' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePrj 'deploy\offline\redis\redis_6379.pid') -Value '99999' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePrj 'deploy\offline\seed\SHGT_STUB.json') -Value '{}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $fakePrj 'deploy\offline\wheels\stub.whl') -Value 'stub' -Encoding ASCII

    $bbArgs = @('-SkipWheels', '-PythonInstaller', ('"{0}"' -f $stubPy), '-PgInstaller', ('"{0}"' -f $stubPy),
                '-PostgisInstaller', ('"{0}"' -f $stubPy), '-VcInstaller', ('"{0}"' -f $stubVc))
    $bb = Invoke-Stage -Script (Join-Path $fakePrj 'deploy\build_bundle.ps1') -ExtraArgs $bbArgs
    Assert-Equal $bb.Exit 0 "build_bundle.ps1 completes on a minimal stub tree"
    if ($bb.Exit -ne 0) { Write-Host "  (bundle build output: $($bb.Text))" -ForegroundColor DarkGray }
    $rdir = Join-Path $fakePrj 'deploy\offline\redis'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $rdir 'dump.rdb'))) "build_bundle strips stale dump.rdb"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $rdir 'redis_6379.pid'))) "build_bundle strips stale redis_6379.pid"
    Assert-True (Test-Path -LiteralPath (Join-Path $rdir 'redis-server.exe')) "build_bundle keeps redis-server.exe"
    Assert-True (Test-Path -LiteralPath (Join-Path $rdir 'redis.conf')) "build_bundle keeps redis.conf"
    $leftovers = @(Get-ChildItem -LiteralPath $rdir -File | Where-Object { $_.Name -notin @('redis-server.exe','redis-cli.exe','redis.conf') })
    Assert-Equal $leftovers.Count 0 "offline\redis contains no leftover runtime artifacts"
    $mf = Read-JsonFile -Path (Join-Path $fakePrj 'deploy\offline\manifest.json')
    Assert-True ($null -ne $mf) "stub bundle published manifest.json"
    if ($mf) {
        $strayArtifacts = @($mf.files | Where-Object { $_.path -like '*dump.rdb' -or $_.path -like '*.pid' })
        Assert-Equal $strayArtifacts.Count 0 "manifest never lists redis runtime artifacts"
        Assert-True ($mf.files.Count -ge 4) "manifest lists the remaining redis payload"
        Assert-True (@($mf.files | Where-Object { $_.path -like 'seed/*' }).Count -ge 1) "manifest includes the seed fallback"
    }
} finally {
    Remove-Item -LiteralPath $fakePrj -Recurse -Force -ErrorAction SilentlyContinue
}

# Encoding + self-sufficiency guard.
#
# 1) Encoding: Windows PowerShell 5.1 picks a .ps1's encoding from its BOM and
#    falls back to the system ANSI codepage (936/GBK on zh-CN hosts) when there is
#    none, so a script with non-ASCII content and no BOM decodes to mojibake. Every
#    deploy script - build_bundle.ps1 included - must simply stay pure ASCII.
# 2) Self-sufficiency: build_bundle.ps1 used to default to machine-specific
#    sources outside the repo (a personal archive folder). All sources must now
#    default to deploy/offline/ itself, so the bundle can be rebuilt from the
#    deploy folder alone - no external directory, no network, no arguments.
$deployScripts = @(Get-ChildItem -LiteralPath (Join-Path (Get-DeployRoot) 'scripts') -Filter '*.ps1' -File) +
                  @(Get-ChildItem -LiteralPath (Get-DeployRoot) -Filter '*.ps1' -File) +
                  @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File)
Assert-True ($deployScripts.Count -ge 15) "encoding guard sees every deploy script"
$nonAsciiFiles = @()
$bomFiles = @()
foreach ($f in $deployScripts) {
    $b = [System.IO.File]::ReadAllBytes($f.FullName)
    if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { $bomFiles += $f.Name }
    $nonAscii = @($b | Where-Object { $_ -gt 127 }).Count
    if ($nonAscii -gt 0) { $nonAsciiFiles += "$($f.Name) ($nonAscii bytes)" }
}
Assert-Equal $nonAsciiFiles.Count 0 ("every deploy script is pure ASCII" + $(if ($nonAsciiFiles) { " -> " + ($nonAsciiFiles -join '; ') } else { '' }))
Assert-Equal $bomFiles.Count 0 "no deploy script needs a UTF-8 BOM"

$bbPath = Join-Path (Get-DeployRoot) 'build_bundle.ps1'
$bbText = [System.IO.File]::ReadAllText($bbPath, [System.Text.UTF8Encoding]::new($false))
# Only the param() block matters: that is where source defaults used to be pinned to
# the author's machine. (The header comment may still show a D:\ path as an example
# of passing an explicit override.)
$bbParamBlock = [regex]::Match($bbText, '(?s)param\s*\(.*?\n\)').Value
Assert-True ($bbParamBlock.Length -gt 0) "build_bundle.ps1 param block located"
Assert-True (-not ($bbParamBlock -match '[A-Za-z]:\\')) "build_bundle.ps1 param block declares no absolute source path"
Assert-True (-not ($bbText.Contains([char]0x8F6F))) "build_bundle.ps1 has no CJK literals"
foreach ($name in 'python-3.13.3-amd64.exe','postgresql-15.15-2-windows-x64.exe','postgis-bundle-pg15x64-setup-3.6.1-1.exe','MSVBCRT_AIO_x64.exe','accessdatabaseengine_X64.exe') {
    Assert-True ($bbText -match [regex]::Escape($name)) "build_bundle.ps1 knows the canonical name $name"
}
$bbErrs = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($bbPath, [ref]$null, [ref]$bbErrs)
Assert-Equal $bbErrs.Count 0 "build_bundle.ps1 parses without errors"

# The real offline/ tree must already satisfy the new defaults: every canonical
# installer present, redis/libredwg/seed populated. This is what makes a
# no-argument build a no-op re-verification instead of a failure.
$realOff = Get-OfflineDir
foreach ($n in 'python-3.13.3-amd64.exe','postgresql-15.15-2-windows-x64.exe','postgis-bundle-pg15x64-setup-3.6.1-1.exe','MSVBCRT_AIO_x64.exe') {
    Assert-True (Test-Path -LiteralPath (Join-Path $realOff "installers\$n")) "offline/installers already holds $n"
}
Assert-True (Test-Path -LiteralPath (Join-Path $realOff 'redis\redis-server.exe')) "offline/redis already holds redis-server.exe"
Assert-True (Test-Path -LiteralPath (Join-Path $realOff 'libredwg\dwg2dxf.exe')) "offline/libredwg already holds dwg2dxf.exe"
Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $realOff 'seed') -Filter '*.json' -ErrorAction SilentlyContinue).Count -gt 0) "offline/seed already holds seed json"

# build_bundle.ps1 with NO paths at all must publish a complete manifest from an
# already-populated offline/ tree, and must strip redis runtime artifacts.
$selfPrj = Join-Path $env:TEMP ("vcs_selfsuff_" + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path (Join-Path $selfPrj 'deploy\scripts') -Force | Out-Null
    foreach ($d in 'installers','redis','libredwg','wheels','seed') { New-Item -ItemType Directory -Path (Join-Path $selfPrj "deploy\offline\$d") -Force | Out-Null }
    Copy-Item -LiteralPath (Join-Path (Get-DeployRoot) 'scripts\common.ps1') -Destination (Join-Path $selfPrj 'deploy\scripts\common.ps1')
    Copy-Item -LiteralPath (Join-Path (Get-DeployRoot) 'build_bundle.ps1') -Destination (Join-Path $selfPrj 'deploy\build_bundle.ps1')

    $sInst = Join-Path $selfPrj 'deploy\offline\installers'
    foreach ($n in 'python-3.13.3-amd64.exe','postgresql-15.15-2-windows-x64.exe','postgis-bundle-pg15x64-setup-3.6.1-1.exe','MSVBCRT_AIO_x64.exe','accessdatabaseengine_X64.exe') {
        Set-Content -LiteralPath (Join-Path $sInst $n) -Value 'stub' -Encoding ASCII
    }
    $sRedis = Join-Path $selfPrj 'deploy\offline\redis'
    foreach ($f in 'redis-server.exe','redis-cli.exe','redis.conf') {
        Copy-Item -LiteralPath (Join-Path (Get-ProjectRoot) "Redis8\$f") -Destination (Join-Path $sRedis $f)
    }
    Set-Content -LiteralPath (Join-Path $sRedis 'dump.rdb') -Value 'stale' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $sRedis 'redis_6379.pid') -Value '99999' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $selfPrj 'deploy\offline\libredwg\dwg2dxf.exe') -Value 'stub' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $selfPrj 'deploy\offline\seed\S.json') -Value '{}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $selfPrj 'deploy\offline\wheels\w.whl') -Value 'stub' -Encoding ASCII

    # NO arguments and NO -SkipWheels: the wheels already bundled must be reused.
    $ss = Invoke-Stage -Script (Join-Path $selfPrj 'deploy\build_bundle.ps1')
    Assert-Equal $ss.Exit 0 "build_bundle.ps1 with no arguments succeeds on a self-contained tree"
    $ssMan = Join-Path $selfPrj 'deploy\offline\manifest.json'
    Assert-True (Test-Path -LiteralPath $ssMan) "self-contained build writes manifest.json"
    $mfSelf = Read-JsonFile -Path $ssMan
    $ssFiles = if ($mfSelf) { @($mfSelf.files) } else { @() }
    Assert-Equal $ssFiles.Count 11 "self-contained manifest lists all 11 payload files"
    Assert-Equal @($ssFiles | Where-Object { $_.path -like '*dump.rdb' -or $_.path -like '*.pid' }).Count 0 "self-contained manifest excludes redis artifacts"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $sRedis 'dump.rdb'))) "self-contained build still strips stale dump.rdb"
    Assert-True ($ss.Text -match 'in place') "self-contained build recognised the in-place sources"

    # An incomplete tree must fail loudly with a fixable message, never publish a
    # partial bundle.
    Remove-Item -LiteralPath (Join-Path $sInst 'python-3.13.3-amd64.exe') -Force
    $bad = Invoke-Stage -Script (Join-Path $selfPrj 'deploy\build_bundle.ps1')
    # Check the message before the status so an unexpected failure still prints why.
    Assert-True ($bad.Text -match 'Python 3.13.3') "failure names the missing component (exit=$($bad.Exit))"
    Assert-True ($bad.Text -match 'offline') "failure points at deploy/offline/installers"
    Assert-Equal $bad.Exit 1 "build_bundle.ps1 fails when an installer is missing and no source was given"
} finally {
    Remove-Item -LiteralPath $selfPrj -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# v2.23.3: silent install, automatic remediation, diagnostics, result file
# ---------------------------------------------------------------------------

# The deploy subsystem must be able to run untouched by a scheduler: no prompt, no
# final pause, and a machine-readable verdict. Static guards first - they fail fast
# and need no child process.
$silentBat = Join-Path (Get-DeployRoot) 'deploy_silent.bat'
Assert-True (Test-Path -LiteralPath $silentBat) "deploy_silent.bat exists"
$silentText = [System.IO.File]::ReadAllText($silentBat, [System.Text.UTF8Encoding]::new($false))
Assert-True (-not ($silentText -match '(?im)^\s*pause\b')) "deploy_silent.bat never pauses"
Assert-True (-not ($silentText -match 'RunAs')) "deploy_silent.bat never raises a UAC prompt (assumes an elevated session)"
Assert-True ($silentText -match 'fltmc') "deploy_silent.bat proves elevation without depending on the Server service"
Assert-True ($silentText -match '--silent') "deploy_silent.bat passes --silent"
Assert-True ($silentText -match '--unattended') "deploy_silent.bat passes --unattended"
Assert-True ($silentText -match 'exit /b %RC%') "deploy_silent.bat propagates the real exit code"

$interactiveBat = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'deploy_offline.bat'), [System.Text.UTF8Encoding]::new($false))
Assert-True ($interactiveBat -match '(?m)^\s*pause\s*$') "deploy_offline.bat still pauses for a human"
Assert-True ($interactiveBat -match 'SILENT_MODE') "deploy_offline.bat can be told not to pause"
Assert-True ($interactiveBat -match 'RunAs -Wait') "deploy_offline.bat waits for the elevated run"

# No stage script may block on input. Read-Host in a stage is the classic way a
# silent deploy hangs forever in a scheduler.
foreach ($f in @(Get-ChildItem -LiteralPath (Join-Path (Get-DeployRoot) 'scripts') -Filter '*.ps1' -File)) {
    $body = [System.IO.File]::ReadAllText($f.FullName, [System.Text.UTF8Encoding]::new($false))
    Assert-True (-not ($body -match 'Read-Host')) "stage never prompts for input: $($f.Name)"
    Assert-True (-not ($body -match 'Get-Credential')) "stage never asks for credentials: $($f.Name)"
    Assert-True (-not ($body -match '(?im)^\s*pause\s*$')) "stage never pauses: $($f.Name)"
    Assert-True (-not ($body -match 'ConfirmPreference\s*=\s*.High.')) "stage never demands confirmation: $($f.Name)"
}

# The orchestrator must retry recoverably, and must not burn time retrying stages
# where failure is deterministic (that only delays the operator's diagnosis).
$orch = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\deploy.ps1'), [System.Text.UTF8Encoding]::new($false))
Assert-True ($orch -match '\$noRetry\s*=\s*@\(([^)]*)\)') "deploy.ps1 declares a no-retry stage list"
Assert-True ($orch -match 'while \(\$attempt -lt \$maxAttempts\)') "deploy.ps1 has a bounded retry loop"
Assert-True ($orch -match 'Invoke-StageRepair') "deploy.ps1 attempts an automatic repair between attempts"
Assert-True ($orch -match 'maxAttempts = if \(\$MaxRetries -gt 0\) \{ \$MaxRetries \+ 1 \} else \{ 3 \}') "retry budget is bounded and overridable"
Assert-True ($orch -match 'Invoke-Diagnostics') "deploy.ps1 collects diagnostics on failure"
Assert-True ($orch -match 'Write-DeployResult') "deploy.ps1 writes a machine-readable result"
Assert-True ($orch -match 'Get-FailureHints') "deploy.ps1 prints actionable hints on failure"
Assert-True ($orch -match "VCS_DEPLOY_QUIET") "deploy.ps1 propagates quiet mode to child stages"
Assert-True ($orch -match "VCS_DEPLOY_UNATTENDED") "deploy.ps1 propagates unattended mode to child stages"
# A repair must never touch anything this deploy did not create.
Assert-True ($orch -notmatch 'Resolve-PostgresUninstaller') "repairs never uninstall software"
Assert-True ($orch -notmatch 'Remove-Item[^\r\n]*PostgreSQL\\15\\data[^\r\n]*Recurse') "repairs never delete a PostgreSQL data directory"

# Retry policy: known recoverable actions have a real budget, unknown ones do not
# get an invented one.
Assert-True ((Get-RetryPolicy -Action 'redis-start').Max -ge 2) "redis-start has a retry budget"
Assert-True ((Get-RetryPolicy -Action 'venv-build').Max -ge 2) "venv-build has a retry budget"
Assert-Equal (Get-RetryPolicy -Action 'unit-test-action').Max 2 "unit-test-action has a small fixed budget"
Assert-Equal (Get-RetryPolicy -Action 'definitely-not-registered').Max 1 "unknown action -> single attempt"

# Every stage that can plausibly fail for a fixable reason must offer hints.
foreach ($s in '00_preflight.ps1','20_install_postgres.ps1','21_install_postgis.ps1','22_install_ace.ps1',
               '30_setup_redis.ps1','40_setup_venv.ps1','50_init_database.ps1','60_seed_and_key.ps1','90_start_services.ps1') {
    Assert-True (@(Get-FailureHints -Stage $s).Count -ge 1) "failure hints exist for $s"
}
$pgHints = (Get-FailureHints -Stage '20_install_postgres.ps1') -join "`n"
Assert-True ($pgHints.Length -gt 0) "20 hints are non-empty on this machine"

# The reported version must come from the app, not be hardcoded in the deploy layer.
Assert-True ((Get-ProjectVersion) -match '^\d+\.\d+\.\d+$') "Get-ProjectVersion reads a semver from app/main.py"

# -Silent must quiet the routine chatter while still recording everything and
# still shouting about problems.
#
# Why this is asserted as a rule mirror instead of by capturing console output:
# Write-Host writes to the host, not to [Console]::Out, so it cannot be redirected
# in-process. Adding a capture hook inside Write-StageLog would be test-only
# coupling in production code. Instead the rule is proven twice:
#   1) the exact console/log decision is re-evaluated here for every (quiet, level)
#      combination, mirroring `$toConsole = -not ($quiet -and ($Level -in @('INFO','OK','SKIP')))`
#   2) the real logger is called and its LOG output is read back, which also proves
#      the decision never suppresses what lands on disk
$commonText = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\common.ps1'), [System.Text.UTF8Encoding]::new($false))
Assert-True ($commonText -match '\$toConsole = -not \(\$quiet -and \(\$Level -in @\(''INFO'', ''OK'', ''SKIP''\)\)\)') "quiet rule is stated as a single readable expression"
Assert-True ($commonText -match 'VCS_DEPLOY_QUIET') "quiet mode is driven by VCS_DEPLOY_QUIET"
Assert-True ($commonText -match 'Add-Content -LiteralPath \$env:VCS_DEPLOY_LOG') "the log write is unconditional (before the console gate)"

function Test-QuietDecision {
    param([bool]$Quiet, [string]$Level)
    return (-not ($Quiet -and ($Level -in @('INFO', 'OK', 'SKIP'))))
}
Assert-True (-not (Test-QuietDecision -Quiet $true  -Level 'INFO'))  "silent: INFO stays off the console"
Assert-True (-not (Test-QuietDecision -Quiet $true  -Level 'OK'))    "silent: OK stays off the console"
Assert-True (-not (Test-QuietDecision -Quiet $true  -Level 'SKIP'))  "silent: SKIP stays off the console"
Assert-True (Test-QuietDecision -Quiet $true  -Level 'WARN')         "silent: WARN reaches the console"
Assert-True (Test-QuietDecision -Quiet $true  -Level 'ERROR')        "silent: ERROR reaches the console"
Assert-True (Test-QuietDecision -Quiet $false -Level 'INFO')         "normal: INFO reaches the console"
Assert-True (Test-QuietDecision -Quiet $false -Level 'WARN')         "normal: WARN reaches the console"

$logQuiet = Join-Path $env:TEMP ("vcs_quiet_" + [guid]::NewGuid().ToString('N') + ".log")
$env:VCS_DEPLOY_LOG = $logQuiet
$env:VCS_DEPLOY_QUIET = '1'
foreach ($lvl in 'INFO','OK','WARN','ERROR','SKIP') {
    Write-StageLog "quiet-$lvl-marker" $lvl
}
Remove-Item Env:\VCS_DEPLOY_QUIET -ErrorAction SilentlyContinue
Remove-Item Env:\VCS_DEPLOY_LOG -ErrorAction SilentlyContinue
$quietLog = if (Test-Path -LiteralPath $logQuiet) { Get-Content -LiteralPath $logQuiet -Raw } else { '' }
foreach ($lvl in 'INFO','OK','WARN','ERROR','SKIP') {
    Assert-True ($quietLog -match "quiet-$lvl-marker") "silent mode still records $lvl in the log"
}
Assert-Equal @(Get-Content -LiteralPath $logQuiet).Count 5 "silent mode records every level exactly once"
Remove-Item -LiteralPath $logQuiet -Force -ErrorAction SilentlyContinue

# Invoke-WithRetry is the shared retry primitive: it must retry, report how many
# attempts it took, cap out, and never let a throwing body escape.
$script:retryBody = 0
$rr = Invoke-WithRetry -Name 'unit-ok' -Action 'redis-start' -Body { $script:retryBody++; if ($script:retryBody -lt 3) { return 7 } else { return 0 } }
Assert-True $rr.Ok "Invoke-WithRetry recovers when a later attempt succeeds"
Assert-Equal $rr.Attempts 3 "Invoke-WithRetry reports the attempt count"
Assert-Equal $rr.Repairs.Count 2 "Invoke-WithRetry records one repair per retry"
$script:retryBody2 = 0
# 'unit-test-action' has a budget of 2 and NO registered repair, so this exercises
# the cap without any chance of mutating the machine.
$rf = Invoke-WithRetry -Name 'unit-fail' -Action 'unit-test-action' -Body { $script:retryBody2++; return 4 }
Assert-True (-not $rf.Ok) "Invoke-WithRetry gives up at the policy limit"
Assert-Equal $rf.Attempts 2 "unit-test-action policy caps at 2 attempts"
Assert-Equal $rf.ExitCode 4 "Invoke-WithRetry preserves the last exit code"
$rt = Invoke-WithRetry -Name 'unit-throw' -Action 'unknown-unit' -Body { throw 'boom' }
Assert-True (-not $rt.Ok) "Invoke-WithRetry contains a throwing body"
Assert-Equal $rt.Attempts 1 "unknown action is not retried"

# Diagnostics must be collected without disturbing the machine - and without
# leaving a folder behind in the real deploy/ tree.
#
# Invoke-Diagnostics has no root parameter and resolves its output directory the
# same way every stage does (script root -> deploy root), so the probe necessarily
# writes into the real deploy\diagnostics. The suite therefore preserves whatever
# was there and removes exactly what the probe created.
$realDiagRoot = Join-Path (Get-DeployRoot) 'diagnostics'
$stashedDiag = $null
if (Test-Path -LiteralPath $realDiagRoot) {
    $stashedDiag = Join-Path $env:TEMP ("vcs_diagstash_" + [guid]::NewGuid().ToString('N'))
    Move-Item -LiteralPath $realDiagRoot -Destination $stashedDiag -Force
}
$diagProbe = $null
try {
    $diagProbe = Invoke-Diagnostics -Reason 'unit test' -Stage '20_install_postgres.ps1'
} finally {
    # Read the bundle BEFORE removing it, and remove only what the probe created
    # (whatever was there beforehand was stashed above and is restored).
    if ($diagProbe) {
        if (Test-Path -LiteralPath (Join-Path $diagProbe 'summary.txt')) {
            $diagText = Get-Content -LiteralPath (Join-Path $diagProbe 'summary.txt') -Raw
            Assert-True ($diagText -match 'reason    : unit test') "diagnostics summary records the reason"
            Assert-True ($diagText -match '== components ==') "diagnostics summary records component probe results"
            Assert-True ($diagText -match '== ports ==') "diagnostics summary records port ownership"
            Assert-True ($diagText -match '5432') "diagnostics summary covers the PostgreSQL port"
        }
        Assert-True (Test-Path -LiteralPath (Join-Path $diagProbe 'summary.txt')) "diagnostics bundle contains summary.txt"
    }
    Remove-Item -LiteralPath $realDiagRoot -Recurse -Force -ErrorAction SilentlyContinue
    if ($stashedDiag) { Move-Item -LiteralPath $stashedDiag -Destination $realDiagRoot -Force -ErrorAction SilentlyContinue }
}
Assert-True ([bool]$diagProbe) "Invoke-Diagnostics returns a bundle path"
Assert-True (-not (Test-Path -LiteralPath $realDiagRoot)) "the probe left no diagnostics folder in the real deploy tree"

# The result file is the machine-readable contract a scheduler reads. It must be
# usable as an artifact WITHOUT carrying credentials: only a fingerprint plus the
# path of the single file that does hold them.
$resultPath = Join-Path (Get-DeployRoot) 'last_result.json'
$statePath = Join-Path (Get-DeployRoot) '.install_state.json'
# Plant a known credential pair so the leak check has something specific to hunt.
$prevState = $null
if (Test-Path -LiteralPath $statePath) { $prevState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 }
$secretAdmin = 'SECRET-ADMIN-MUST-NOT-APPEAR-0123456789'
$secretApi = 'SECRET-API-MUST-NOT-APPEAR-9876543210'
[ordered]@{ admin_init_key = $secretAdmin; api_key = $secretApi; created = 'unit-test' } |
    ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
try {
    Write-DeployResult -Result 'FAILED' -Stage '50_init_database.ps1' -Reason 'unit test failure' -ExitCode 1 `
        -Stages @([pscustomobject]@{ Stage='50_init_database.ps1'; Exit=1; Ok=$false; Attempts=3; Repairs=2 }) `
        -DiagnosticsDir 'X:\diag' -Extra @{ dry_run = $true }
    Assert-True (Test-Path -LiteralPath $resultPath) "last_result.json is written"
    $rj = Read-JsonFile -Path $resultPath
    Assert-True ($null -ne $rj) "last_result.json is valid JSON"
    $rjText = if (Test-Path -LiteralPath $resultPath) { Get-Content -LiteralPath $resultPath -Raw } else { '' }
    # The regression guard: a shareable status file must never carry a usable key.
    Assert-True (-not $rjText.Contains($secretAdmin)) "result file does NOT contain the admin key"
    Assert-True (-not $rjText.Contains($secretApi)) "result file does NOT contain the api key"
    if ($rj) {
        Assert-Equal $rj.result 'FAILED' "result file records the verdict"
        Assert-Equal $rj.failed_stage '50_init_database.ps1' "result file records the failing stage"
        Assert-Equal $rj.reason 'unit test failure' "result file records the reason"
        Assert-Equal $rj.exit_code 1 "result file records the exit code"
        Assert-Equal $rj.stages.Count 1 "result file records per-stage detail"
        Assert-Equal $rj.stages[0].Attempts 3 "result file records how many attempts were made"
        Assert-Equal $rj.stages[0].Repairs 2 "result file records how many repairs were made"
        Assert-Equal $rj.dry_run $true "result file carries extra fields"
        Assert-Equal $rj.version (Get-ProjectVersion) "result file version matches the app version"
        Assert-True ($rj.PSObject.Properties.Name -contains 'credentials') "result file has a credentials section"
        Assert-True (-not ($rj.PSObject.Properties.Name -contains 'admin_init_key')) "result file has no top-level admin_init_key field"
        Assert-True (-not ($rj.PSObject.Properties.Name -contains 'api_key')) "result file has no top-level api_key field"
        Assert-Equal $rj.credentials.admin_init_key_fp (Get-SecretFingerprint -Secret $secretAdmin) "result file records the admin key fingerprint"
        Assert-Equal $rj.credentials.api_key_fp (Get-SecretFingerprint -Secret $secretApi) "result file records the api key fingerprint"
        Assert-Equal $rj.credentials.source 'deploy\.install_state.json' "result file points at the single credential file"
    }
} finally {
    Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $prevState) { Set-Content -LiteralPath $statePath -Value $prevState -Encoding UTF8 }
    else { Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue }
}
Assert-True (-not (Test-Path -LiteralPath $resultPath)) "result file is cleaned up by the test"

# ---------------------------------------------------------------------------
# Secret hygiene
#
# deploy\logs\ is what an operator pastes into a ticket and deploy\diagnostics\ is
# documented as "send this to the troubleshooter". Neither may contain a usable key.
# ---------------------------------------------------------------------------
$fingerprint = Get-SecretFingerprint -Secret 'a'
Assert-Equal $fingerprint.Length 12 "fingerprint is 12 hex chars"
Assert-Equal $fingerprint 'ca978112ca1b' "fingerprint is the SHA-256 prefix of the secret (known vector)"
Assert-Equal (Get-SecretFingerprint -Secret 'a') (Get-SecretFingerprint -Secret 'a') "fingerprint is stable"
Assert-True ((Get-SecretFingerprint -Secret 'a') -ne (Get-SecretFingerprint -Secret 'b')) "distinct secrets get distinct fingerprints"
Assert-Equal (Get-SecretFingerprint -Secret '') '(none)' "empty secret has no fingerprint"
Assert-True ((Get-SecretFingerprint -Secret $secretAdmin) -match '^[0-9a-f]{12}$') "fingerprint is lowercase hex"

$line = Format-SecretLine -Label 'API_KEY' -Secret $secretAdmin -Source '.install_state.json'
Assert-True (-not $line.Contains($secretAdmin)) "a log line never contains the secret"
Assert-True ($line -match 'fp=') "a log line carries the fingerprint"
Assert-True ($line -match '\.install_state\.json') "a log line points at the credential file"
Assert-True ((Format-SecretLine -Label 'API_KEY' -Secret '') -match '\(absent\)') "an absent secret is reported as absent"

# Static guards: the stage scripts must not print raw keys, and the diagnostics
# bundle must not copy the credential file.
$cmSource = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\common.ps1'), [System.Text.UTF8Encoding]::new($false))
$seedSource = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\60_seed_and_key.ps1'), [System.Text.UTF8Encoding]::new($false))
$verifySource = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\99_verify.ps1'), [System.Text.UTF8Encoding]::new($false))
$orchSource = [System.IO.File]::ReadAllText((Join-Path (Get-DeployRoot) 'scripts\deploy.ps1'), [System.Text.UTF8Encoding]::new($false))
foreach ($pair in @(@('60_seed_and_key.ps1', $seedSource), @('99_verify.ps1', $verifySource), @('deploy.ps1', $orchSource))) {
    Assert-True (-not ($pair[1] -match 'ADMIN_INIT_KEY : \$')) "$($pair[0]) does not print the raw admin key"
    Assert-True (-not ($pair[1] -match 'API_KEY\s+: \$')) "$($pair[0]) does not print the raw api key"
    Assert-True ($pair[1] -match 'Format-SecretLine') "$($pair[0]) reports credentials via fingerprint"
}
Assert-True ($cmSource -match 'NOT copied \.install_state\.json') "diagnostics bundle refuses to copy the credential file"
Assert-True ($cmSource -match 'function Protect-SecretFile') "a helper exists to restrict the credential file ACL"
Assert-True ($seedSource -match 'Protect-SecretFile -Path \$stateFile') "stage 60 locks down the credential file"
Assert-True ($cmSource -match "source             = 'deploy\\\.install_state\.json'") "the result file names the credential source"
$gitignore = [System.IO.File]::ReadAllText((Join-Path (Get-ProjectRoot) '.gitignore'), [System.Text.UTF8Encoding]::new($false))
Assert-True ($gitignore -match 'deploy/last_result\.json') "last_result.json is git-ignored"
Assert-True ($gitignore -match 'deploy/\.install_state\.json') ".install_state.json is git-ignored"

# ---------------------------------------------------------------------------
# v2.23.3: command-line contract
#
# Regression: the documented command line is double-dash style
# ("deploy_offline.bat --from 40 --dry-run"), but in PowerShell's -File mode a
# double-dash argument is NOT bound to param() - it lands in $args and is dropped.
# deploy.ps1 used to ignore $args entirely, so `--dry-run` performed a REAL
# install while the operator believed they were running a health check. Silent
# wrong behaviour, so it gets its own contract test.
function Assert-Args {
    param([string[]]$Argv, [string]$Want, [string]$Label)
    $r = Resolve-DeployArgs -ArgumentList $Argv
    $got = "From=$($r.From) Only=$($r.Only) DryRun=$($r.DryRun) Silent=$($r.Silent) Unattended=$($r.Unattended) MaxRetries=$($r.MaxRetries) Unknown=[$(@($r.Unknown) -join ',')]"
    Assert-Equal $got $Want $Label
}
Assert-Args @() 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[]' "no arguments -> defaults"
Assert-Args @('--from','40','--dry-run') 'From=40 Only=-1 DryRun=True Silent=False Unattended=False MaxRetries=0 Unknown=[]' "documented double-dash style is honoured"
Assert-Args @('-From','20','-Only','50','-DryRun') 'From=20 Only=50 DryRun=True Silent=False Unattended=False MaxRetries=0 Unknown=[]' "PowerShell-native single-dash style is honoured"
Assert-Args @('--silent','--unattended') 'From=0 Only=-1 DryRun=False Silent=True Unattended=True MaxRetries=0 Unknown=[]' "deploy_silent.bat's own flags parse"
Assert-Args @('--max-retries','5') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=5 Unknown=[]' "--max-retries is accepted"
Assert-Args @('--from=20') 'From=20 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[]' "--from=20 equals form is accepted"
Assert-Args @('--only=50','--max-retries=4') 'From=0 Only=50 DryRun=False Silent=False Unattended=False MaxRetries=4 Unknown=[]' "equals form works for several options"
Assert-Args @('--DRY-RUN','--From','30') 'From=30 Only=-1 DryRun=True Silent=False Unattended=False MaxRetries=0 Unknown=[]' "option names are case-insensitive"
Assert-Args @('--from','10','--from','20') 'From=20 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[]' "a repeated option takes the last value"
Assert-Args @('--dryrun-typo') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[--dryrun-typo]' "a typo is reported, never silently ignored"
Assert-Args @('verbose') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[verbose]' "a bare word is reported"
Assert-Args @('--from') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[--from]' "a value-option without a value is reported"
Assert-Args @('--from','abc') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[--from]' "a non-numeric value is reported"
Assert-Args @('--dry-run=yes') 'From=0 Only=-1 DryRun=False Silent=False Unattended=False MaxRetries=0 Unknown=[--dry-run=yes]' "a switch given a value is reported"
$baseMixed = Resolve-DeployArgs -ArgumentList @('--dry-run') -BaseSilent $true -BaseMaxRetries 9
Assert-True ($baseMixed.DryRun -and $baseMixed.Silent) "already-bound switches are merged with parsed ones"
Assert-Equal $baseMixed.MaxRetries 9 "an option absent from argv keeps its bound value"

# deploy.ps1 must actually consume $args (that is the whole point) and must refuse
# an unrecognised argument rather than guessing an install mode.
Assert-True ($orch -match 'Resolve-DeployArgs') "deploy.ps1 normalises its command line"
Assert-True ($orch -match '\$args') "deploy.ps1 reads \$args"
Assert-True ($orch -match 'unrecognised argument\(s\)') "deploy.ps1 reports unrecognised arguments"
Assert-True ($orch -match '__args\.Unknown') "deploy.ps1 inspects the unrecognised list"
Assert-True ($orch -match 'exit 2') "deploy.ps1 fails with a distinct exit code on a bad command line"
Assert-True ($orch -match 'supported: --from N') "deploy.ps1 prints the supported options"

# The silent entry point must advertise the same style it passes.
$silentText2 = [System.IO.File]::ReadAllText($silentBat, [System.Text.UTF8Encoding]::new($false))
Assert-True ($silentText2 -match '--silent --unattended') "deploy_silent.bat passes the documented style"
Assert-True ($silentText2 -match '--from 20') "deploy_silent.bat documents --from usage"

# ---------------------------------------------------------------------------
# Test-isolation guard
#
# Regression: the suite used to run real stage scripts with no -ProjectRoot, so a
# stage resolved the LIVE repository as its project root and 40_setup_venv
# rebuilt (destroyed) the developer's .venv. A test must never be able to mutate
# the repository it lives in, so this is asserted rather than trusted.
$suiteText = [System.IO.File]::ReadAllText($PSCommandPath, [System.Text.UTF8Encoding]::new($false))
Assert-True ($suiteText -match '\$stageRoot = Join-Path \$env:TEMP') "the suite creates a throwaway stage root"
Assert-True ($suiteText -match 'function Invoke-Stage' -and $suiteText -match '\[string\]\$ProjectRoot') "Invoke-Stage accepts an isolating -ProjectRoot"
$liveStageCalls = @()
foreach ($m in [regex]::Matches($suiteText, "(?m)^\s*(\$\w+ = )?Invoke-Stage -Script '(\d\d_[^']+)'([^\r\n]*)$")) {
    if ($m.Groups[3].Value -notmatch '-ProjectRoot') { $liveStageCalls += $m.Groups[2].Value }
}
Assert-Equal $liveStageCalls.Count 0 ("no stage runs against the live repo" + $(if ($liveStageCalls) { " -> " + ($liveStageCalls -join ', ') } else { '' }))
Assert-True ($suiteText -match 'Remove-Item -LiteralPath \$stageRoot -Recurse -Force') "the suite cleans up its stage root"

Write-Host ""
Write-Host "Passed: $script:Passed  Failed: $script:Failed"
Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($script:Failed -gt 0) { exit 1 }
exit 0
