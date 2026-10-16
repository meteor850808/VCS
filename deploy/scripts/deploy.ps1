[CmdletBinding()]
param(
    [int]$From = 0,
    [int]$Only = -1,
    [switch]$DryRun,
    [switch]$Silent,
    [switch]$Unattended,
    [int]$MaxRetries = 0
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

# Accept both "--from 40 --dry-run" (documented, operator friendly) and the
# PowerShell-native "-From 40 -DryRun". See Resolve-DeployArgs for why this is
# required: in -File mode a double-dash argument is not bound to param() and would
# otherwise be dropped, turning an advertised "--dry-run" into a real install.
$__args = Resolve-DeployArgs -ArgumentList $args -BaseFrom $From -BaseOnly $Only `
    -BaseDryRun ([bool]$DryRun) -BaseSilent ([bool]$Silent) `
    -BaseUnattended ([bool]$Unattended) -BaseMaxRetries $MaxRetries
$From = [int]$__args.From
$Only = [int]$__args.Only
$DryRun = [bool]$__args.DryRun
$Silent = [bool]$__args.Silent
$Unattended = [bool]$__args.Unattended
$MaxRetries = [int]$__args.MaxRetries

$deployRoot = Get-DeployRoot
$logDir = Join-Path $deployRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$env:VCS_DEPLOY_LOG = Join-Path $logDir "deploy_$stamp.log"

# An unrecognised token is a typo. Refuse to run rather than silently ignore it:
# guessing here means picking the wrong install mode on a production machine.
if (@($__args.Unknown).Count -gt 0) {
    Write-StageLog "unrecognised argument(s): $(@($__args.Unknown) -join ', ')" 'ERROR'
    Write-StageLog "supported: --from N  --only N  --dry-run  --silent  --unattended  --max-retries N" 'ERROR'
    Write-StageLog "  (PowerShell-native spellings also accepted: -From -Only -DryRun -Silent -Unattended -MaxRetries)" 'ERROR'
    Write-DeployResult -Result 'FAILED' -Stage '' -Reason "unrecognised argument(s): $(@($__args.Unknown) -join ', ')" -ExitCode 2
    Write-StageLog "RESULT: FAILED" 'ERROR'
    exit 2
}
# Child stage scripts inherit this through the environment, so a silent run stays
# silent end to end. WARN/ERROR still reach the console; everything reaches the log.
if ($Silent) { $env:VCS_DEPLOY_QUIET = '1' } else { Remove-Item Env:\VCS_DEPLOY_QUIET -ErrorAction SilentlyContinue }
if ($Unattended) { $env:VCS_DEPLOY_UNATTENDED = '1' } else { Remove-Item Env:\VCS_DEPLOY_UNATTENDED -ErrorAction SilentlyContinue }

Write-StageLog "=== VCS offline deploy v$(Get-ProjectVersion) (DryRun=$DryRun From=$From Only=$Only Silent=$Silent Unattended=$Unattended) ==="
if (-not $Silent) { Write-StageLog "log file: $($env:VCS_DEPLOY_LOG)" }

$nonCritical = @(22, 25)   # ACE, VC++ -> warn but continue

# Stages that are pointless (and misleading) to retry: nothing about them is
# transient, and re-running them only delays the operator's diagnosis.
$noRetry = @(0, 10, 21, 99)
# Per-stage automatic repair. Must be idempotent and must only touch what this
# deploy owns: the pinned PostgreSQL 15 service, the offline Redis, our venv, our
# API/Celery processes. Never removes or reconfigures anything else.
function Invoke-StageRepair {
    param([Parameter(Mandatory)][string]$StageFile, [int]$Attempt)
    switch -Regex ($StageFile) {
        '^20_' {
            # A half-registered service or a stale postmaster.pid blocks a retry.
            $svc = Get-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -ne 'Stopped') {
                Stop-Service -Name 'postgresql-x64-15' -Force -ErrorAction SilentlyContinue
                Write-StageLog "[repair] stopped the half-registered postgresql-x64-15 service" 'WARN'
            }
            $lock = 'C:\Program Files\PostgreSQL\15\data\postmaster.pid'
            if (Test-Path -LiteralPath $lock) {
                Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
                Write-StageLog "[repair] removed a stale postmaster.pid" 'WARN'
            }
            Start-Sleep -Seconds 10
        }
        '^21_' {
            Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        }
        '^30_' {
            $redisDir = Join-Path (Get-OfflineDir) 'redis'
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -eq 'redis-server.exe' -and $_.CommandLine -like '*redis.conf*' } |
                ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }
            foreach ($stale in 'redis_6379.pid', 'dump.rdb.tmp') {
                $sp = Join-Path $redisDir $stale
                if (Test-Path -LiteralPath $sp) { Remove-Item -LiteralPath $sp -Force -ErrorAction SilentlyContinue; Write-StageLog "[repair] removed $stale" 'WARN' }
            }
            Start-Sleep -Seconds 2
        }
        '^40_' {
            # The venv is reproducible from the bundled wheels, so rebuilding it is
            # the safe repair for a half-created environment.
            $venv = Join-Path (Get-ProjectRoot) '.venv'
            if ((Test-Path -LiteralPath $venv) -and -not (Test-VenvReady -ProjectRoot (Get-ProjectRoot))) {
                Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue
                Write-StageLog "[repair] removed an incomplete .venv (will be rebuilt from bundled wheels)" 'WARN'
            }
            & py -3.13 -m ensurepip --upgrade 2>&1 | Out-Null
        }
        '^50_' {
            Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        }
        '^60_' {
            Clear-OwnApiListener -Port 45679
            Start-Sleep -Seconds 2
        }
        '^90_' {
            Clear-OwnApiListener -Port 45679
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*celery*worker*' -or $_.CommandLine -like '*flower*' } |
                ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }
            Start-Sleep -Seconds 3
        }
        default { Start-Sleep -Seconds 2 }
    }
    Write-StageLog "[repair] stage=$StageFile attempt=$Attempt done" 'WARN'
}

function Clear-OwnApiListener {
    # Free the API port, but only from a process that is recognisably ours. Anything
    # else is left alone - we cannot know what it is, and killing a stranger's
    # process would be worse than failing.
    param([Parameter(Mandatory)][int]$Port)
    $held = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    foreach ($c in @($held)) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        if ($p.ProcessName -in @('python', 'python3', 'uvicorn')) {
            & taskkill.exe /f /t /pid $p.Id 2>&1 | Out-Null
            Write-StageLog "[repair] freed port $Port from a previous $($p.ProcessName) (pid $($p.Id))" 'WARN'
        } else {
            Write-StageLog "[repair] port $Port is held by '$($p.ProcessName)' (pid $($p.Id)) - not ours, leaving it alone" 'WARN'
        }
    }
}

$stages = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' |
    Where-Object { $_.Name -match '^(\d{2})_.+\.ps1$' } |
    Sort-Object Name

$results = @()
$failedStage = ''
$failedReason = ''
foreach ($f in $stages) {
    if ($f.Name -notmatch '^(\d{2})_') { continue }
    $num = [int]$Matches[1]
    if ($num -lt $From) { Write-StageLog "skip $($f.Name) (stage < From=$From)" 'SKIP'; continue }
    if ($Only -ge 0 -and $num -ne $Only) { continue }

    $maxAttempts = 1
    if (-not $DryRun -and ($noRetry -notcontains $num)) {
        $maxAttempts = if ($MaxRetries -gt 0) { $MaxRetries + 1 } else { 3 }
    }

    $attempt = 0
    $code = 0
    $repairs = 0
    while ($attempt -lt $maxAttempts) {
        $attempt++
        if ($attempt -eq 1) { Write-StageLog ">>> $($f.Name)" 'INFO' }
        else { Write-StageLog ">>> $($f.Name) (retry $attempt/$maxAttempts)" 'INFO' }

        $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File', ('"{0}"' -f $f.FullName))
        if ($DryRun) { $childArgs += '-DryRun' }
        $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $childArgs -NoNewWindow -Wait -PassThru
        $code = [int]$p.ExitCode
        if ($code -eq 0) { break }
        if ($attempt -ge $maxAttempts) { break }
        Write-StageLog "$($f.Name) failed (exit $code) - attempting automatic repair" 'WARN'
        Invoke-StageRepair -StageFile $f.Name -Attempt $attempt
        $repairs++
    }

    $ok = ($code -eq 0)
    $results += [pscustomobject]@{ Stage=$f.Name; Exit=$code; Ok=$ok; Attempts=$attempt; Repairs=$repairs }
    if ($ok) {
        if ($attempt -gt 1) { Write-StageLog "<<< $($f.Name) OK (recovered after $repairs repair(s))" 'OK' }
        else { Write-StageLog "<<< $($f.Name) OK" 'OK' }
    } else {
        $level = if ($nonCritical -contains $num) { 'WARN' } else { 'ERROR' }
        Write-StageLog "<<< $($f.Name) FAILED (exit $code after $attempt attempt(s))" $level
        if ($nonCritical -notcontains $num) {
            $failedStage = $f.Name
            $failedReason = "stage $($f.Name) exited $code after $attempt attempt(s)"
            break
        }
    }
}

# ---------------------------------------------------------------------------
# Verdict: diagnostics on failure, machine-readable result either way
# ---------------------------------------------------------------------------
$okStages = @($results | Where-Object { $_.Ok })
$failed = @($results | Where-Object { -not $_.Ok })

if ($results.Count -eq 0) {
    Write-StageLog "=== summary: 0 stages executed (check -From/-Only) ===" 'ERROR'
    Write-DeployResult -Result 'FAILED' -Stage '' -Reason 'no stages matched -From/-Only' -ExitCode 1 -Stages @($results)
    Write-StageLog "RESULT: FAILED" 'ERROR'
    exit 1
}

$diagDir = ''
if ($failed.Count -gt 0) {
    $diagDir = Invoke-Diagnostics -Reason $failedReason -Stage $failedStage
}

Write-StageLog "=== summary: $($results.Count) stages, $($okStages.Count) ok, $($failed.Count) failed ==="
foreach ($r in $results) {
    $tag = if ($r.Ok) { 'OK  ' } else { 'FAIL' }
    $extra = if ($r.Attempts -gt 1) { " attempts=$($r.Attempts) repairs=$($r.Repairs)" } else { '' }
    Write-StageLog ("  [{0}] {1,-26} exit={2}{3}" -f $tag, $r.Stage, $r.Exit, $extra) 'INFO'
}

if ($failed.Count -gt 0) {
    Write-StageLog "--- what to do about it ---" 'ERROR'
    foreach ($h in (Get-FailureHints -Stage $failedStage)) { Write-StageLog "  - $h" 'ERROR' }
    if ($diagDir) { Write-StageLog "  diagnostics bundle: $diagDir" 'ERROR' }
    Write-DeployResult -Result 'FAILED' -Stage $failedStage -Reason $failedReason -ExitCode 1 -Stages @($results) -DiagnosticsDir $diagDir -Extra @{ dry_run = [bool]$DryRun }
    Write-StageLog "RESULT: FAILED" 'ERROR'
    exit 1
}

Write-DeployResult -Result 'SUCCESS' -Stage '' -Reason '' -ExitCode 0 -Stages @($results) -Extra @{ dry_run = [bool]$DryRun }
if (-not $Silent) {
    $stateFile = Join-Path $deployRoot '.install_state.json'
    if (Test-Path -LiteralPath $stateFile) {
        try {
            $s = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            # Fingerprints only - this console output is captured into deploy\logs\.
            Write-StageLog (Format-SecretLine -Label 'ADMIN_INIT_KEY' -Secret $s.admin_init_key -Source '.install_state.json') 'OK'
            Write-StageLog (Format-SecretLine -Label 'API_KEY' -Secret $s.api_key -Source '.install_state.json') 'OK'
        } catch { }
    }
}
Write-StageLog "RESULT: SUCCESS" 'OK'
exit 0
