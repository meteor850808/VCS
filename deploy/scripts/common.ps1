# Shared helpers for VCS offline deployment. ASCII-only (PowerShell 5.1 safe).

function Get-ScriptRoot {
    # The directory holding these scripts. $PSScriptRoot is the normal answer;
    # $global:VCS_SCRIPT_ROOT is an explicit override used by the test runner (and
    # by anything that loads this file without dot-sourcing it). ASCII-only.
    if ($PSScriptRoot) { return $PSScriptRoot }
    if ($global:VCS_SCRIPT_ROOT) { return $global:VCS_SCRIPT_ROOT }
    return (Get-Location).Path
}

function Resolve-DeployArgs {
    # Normalise "deploy_offline.bat --from 40 --dry-run" onto the real parameters.
    #
    # Why this exists: in PowerShell's -File mode a double-dash argument is NOT
    # bound to param() - it lands in $args and is silently ignored. The documented
    # command line is double-dash style, so without this a user typing
    # `deploy_offline.bat --dry-run` would get a REAL install instead of the
    # advertised health check. Silent wrong behaviour is worse than an error, so
    # the parser accepts both styles, and rejects a typo instead of ignoring it.
    #
    # Returns a hashtable with the effective values plus the unrecognised tokens:
    #   From int, Only int, DryRun bool, Silent bool, Unattended bool,
    #   MaxRetries int, Unknown string[]
    param(
        [string[]]$ArgumentList = @(),
        [int]$BaseFrom = 0,
        [int]$BaseOnly = -1,
        [bool]$BaseDryRun = $false,
        [bool]$BaseSilent = $false,
        [bool]$BaseUnattended = $false,
        [int]$BaseMaxRetries = 0
    )
    $r = @{
        From       = $BaseFrom
        Only       = $BaseOnly
        DryRun     = $BaseDryRun
        Silent     = $BaseSilent
        Unattended = $BaseUnattended
        MaxRetries = $BaseMaxRetries
        Unknown    = @()
    }
    $valueKeys = @('from', 'only', 'maxretry', 'maxretries', 'max-retry', 'max-retries')
    $i = 0
    while ($i -lt $ArgumentList.Count) {
        $tok = [string]$ArgumentList[$i]

        # Only option-shaped tokens are considered; anything else is a typo.
        if ($tok -notmatch '^--?[A-Za-z]') { $r.Unknown += $tok; $i++; continue }

        # Accept both "--from 40" and "--from=40".
        $body = $tok -replace '^--?', ''
        $inlineValue = $null
        if ($body -match '^([^=]+)=(.*)$') { $body = $Matches[1]; $inlineValue = $Matches[2] }
        $key = $body.ToLowerInvariant()

        if ($valueKeys -contains $key) {
            $raw = $inlineValue
            $consumed = 1
            if ($null -eq $raw) {
                if ($i + 1 -lt $ArgumentList.Count) { $raw = [string]$ArgumentList[$i + 1]; $consumed = 2 }
            }
            $num = 0
            if (($null -ne $raw) -and [int]::TryParse($raw, [ref]$num)) {
                switch ($key) {
                    'from' { $r.From = $num }
                    'only' { $r.Only = $num }
                    default { $r.MaxRetries = $num }
                }
            } else {
                $r.Unknown += $tok
            }
            $i += $consumed
            continue
        }

        # Switch style options must not carry a value.
        if ($null -ne $inlineValue) { $r.Unknown += $tok; $i++; continue }
        switch ($key) {
            { $_ -in @('dry-run', 'dryrun') } { $r.DryRun = $true }
            'silent'                          { $r.Silent = $true }
            'unattended'                      { $r.Unattended = $true }
            default                           { $r.Unknown += $tok }
        }
        $i++
    }
    return $r
}
function Get-DeployRoot {
    (Resolve-Path -LiteralPath (Join-Path (Get-ScriptRoot) '..')).Path
}
function Get-ProjectRoot {
    (Resolve-Path -LiteralPath (Join-Path (Get-ScriptRoot) '..\..')).Path
}function Get-OfflineDir {
    Join-Path (Get-DeployRoot) 'offline'
}

function Write-StageLog {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','OK','WARN','ERROR','SKIP')][string]$Level = 'INFO'
    )
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts][$Level] $Message"
    # Quiet mode (-Silent / VCS_DEPLOY_QUIET=1) drops the routine chatter from the
    # console but keeps WARN/ERROR audible, and always keeps the full log on disk.
    # Child stage scripts inherit the variable through the environment.
    # The decision is deliberately a named variable so the rule can be read and
    # asserted without having to intercept Write-Host output.
    $quiet = ($env:VCS_DEPLOY_QUIET -eq '1')
    $toConsole = -not ($quiet -and ($Level -in @('INFO', 'OK', 'SKIP')))
    if ($toConsole) {
        switch ($Level) {
            'ERROR' { Write-Host $line -ForegroundColor Red }
            'WARN'  { Write-Host $line -ForegroundColor Yellow }
            'OK'    { Write-Host $line -ForegroundColor Green }
            'SKIP'  { Write-Host $line -ForegroundColor DarkGray }
            default { Write-Host $line }
        }
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

function Format-InstallerArg {
    # Start-Process -ArgumentList does NOT quote array elements. An element such
    # as "C:\Program Files\PostgreSQL\15" would be split into several argv tokens
    # by the child process, breaking the installer's argument parser. Wrap any
    # element that contains whitespace in double quotes. ASCII-only.
    param([string[]]$ArgumentList = @())
    @($ArgumentList | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } })
}

function Invoke-SilentInstaller {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSec = 1800
    )
    if (-not (Test-Path -LiteralPath $FilePath)) { throw "installer not found: $FilePath" }
    $argv = Format-InstallerArg -ArgumentList $ArgumentList
    $p = Start-Process -FilePath $FilePath -ArgumentList $argv -NoNewWindow -Wait -PassThru
    return [int]$p.ExitCode
}

function Get-PostgresInstallerArg {
    # Argument list for the EDB PostgreSQL 15 Windows unattended installer.
    #
    # --unattendedmodeui none : the installer default is "minimal", which still
    #   draws a UI (progress + post-install Stack Builder prompt). On a console
    #   run that prompt can block or confuse the deploy. "none" = fully silent.
    # --locale must be a form the installer parser accepts (long name or BCP47,
    #   e.g. "en-US"). "Chinese_China.936" is an initdb-style literal that the
    #   installer rejects -> early exit 1 (~15s). Use BCP47 "zh-CN".
    # ASCII-only (PowerShell 5.1 safe).
    param([Parameter(Mandatory)][string]$SuperPassword)
    @(
        '--mode','unattended',
        '--unattendedmodeui','none',
        '--prefix','C:\Program Files\PostgreSQL\15',
        '--datadir','C:\Program Files\PostgreSQL\15\data',
        '--superpassword',$SuperPassword,
        '--serverport','5432',
        '--locale','zh-CN',
        '--enable-components','server,pgAdmin,commandlinetools'
    )
}

function Get-InstalledPostgres {
    # Detect any PostgreSQL *server* install on this machine. Never throws.
    # Returns $null, or a PSCustomObject:
    #   Version     string  ("" when unknown, e.g. "16.4")
    #   Major       int     (0 when unknown)  <-- used for policy decisions
    #   ServiceName string  (may be $null)
    #   Prefix      string  (install dir, may be $null)
    #   Source      string  ('registry' | 'service' | 'path')
    # ASCII-only (PowerShell 5.1 safe).

    # 1) EDB registry: HKLM\SOFTWARE\PostgreSQL\Installations\<key>
    foreach ($hive in @('HKLM:\SOFTWARE\PostgreSQL\Installations',
                        'HKLM:\SOFTWARE\WOW6432Node\PostgreSQL\Installations')) {
        if (-not (Test-Path -LiteralPath $hive)) { continue }
        foreach ($s in @(Get-ChildItem -LiteralPath $hive -ErrorAction SilentlyContinue)) {
            $p = Get-ItemProperty -LiteralPath $s.PSPath -ErrorAction SilentlyContinue
            if (-not $p) { continue }
            $base = [string]$p.'Base Directory'
            if (-not $base) { continue }
            $ver = [string]$p.Version
            $svc = [string]$p.'Service ID'
            $major = 0
            if ($ver -match '^(\d+)') { $major = [int]$Matches[1] }
            elseif ($svc -match '(\d+)') { $major = [int]$Matches[1] }
            return [pscustomobject]@{ Version=$ver; Major=$major; ServiceName=$svc; Prefix=$base; Source='registry' }
        }
    }

    # 2) Windows services named postgresql*
    $svc = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($svc) {
        $major = 0
        if ($svc.Name -match '(\d+)') { $major = [int]$Matches[1] }
        $prefix = if ($major -gt 0) { 'C:\Program Files\PostgreSQL\{0}' -f $major } else { $null }
        return [pscustomobject]@{ Version=''; Major=$major; ServiceName=$svc.Name; Prefix=$prefix; Source='service' }
    }

    # 3) psql on PATH
    if (Test-CommandExists -Name 'psql') {
        $out = (& psql --version 2>$null | Out-String)
        if ($out -match '(\d+)\.') {
            return [pscustomobject]@{ Version=''; Major=[int]$Matches[1]; ServiceName=$null; Prefix=$null; Source='path' }
        }
    }

    return $null
}

function Get-PostgresInstallDecision {
    # Policy B (changed in v2.23.2) - never uninstall anything automatically.
    #
    # History: policy A used to return 'conflict' for any non-15 major, and
    # 20_install_postgres offered to silently uninstall it after a [y/N] prompt.
    # That made an unattended deploy a data-destroying operation on a machine
    # that already ran PostgreSQL 16, and the EDB unattended uninstaller
    # normally leaves the Installation registry key behind, so the post-check
    # ("still present after uninstall") aborted the deploy anyway -> the
    # operator lost their database AND still had no VCS.
    #
    # Policy B:
    #   install     - nothing found, or the 15.x install is unusable
    #                 (partial/broken) -> safe to install
    #   skip        - a healthy 15.x server is present -> reuse it
    #   conflict-15 - PostgreSQL 15 is present but unusable and it is NOT safe
    #                 to just run the installer over it -> manual remediation
    #   conflict    - a non-15 major is present -> manual remediation, never
    #                 removed by this script
    #   unknown     - something is listening/registered but the major could not
    #                 be determined -> manual remediation (do not guess)
    # Pure function (unit-testable). $Install is $null or an object exposing:
    #   Major int, Prefix string, ServiceName string, Version string
    param($Install)

    if ($null -eq $Install) { return 'install' }

    $major = 0
    if ($Install.PSObject.Properties.Name -contains 'Major') { $major = [int]$Install.Major }

    if ($major -eq 15) {
        # A PG15 registration may still be a broken remnant (uninstalled service,
        # missing client binaries, interrupted install). Reuse only when the
        # server is actually usable, otherwise a plain (re)install is the fix.
        $prefix = $null
        if ($Install.PSObject.Properties.Name -contains 'Prefix') { $prefix = $Install.Prefix }
        if ($prefix) {
            $psql = Join-Path ([string]$prefix) 'bin\psql.exe'
            if (-not (Test-Path -LiteralPath $psql)) { return 'install' }
            $svc = $null
            if ($Install.PSObject.Properties.Name -contains 'ServiceName') { $svc = $Install.ServiceName }
            if ($svc) {
                $s = Get-Service -Name ([string]$svc) -ErrorAction SilentlyContinue
                if (-not $s) { return 'install' }
            }
        }
        return 'skip'
    }

    if ($major -gt 0) { return 'conflict' }
    return 'unknown'
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

# ---------------------------------------------------------------------------
# Secret hygiene
#
# The bootstrap credentials are deliberately stored in ONE place
# (deploy\.install_state.json) so that a shareable artifact never carries them.
# deploy\logs\ is the first thing an operator pastes into a ticket, and
# deploy\diagnostics\ is documented as "send this to the troubleshooter" - neither
# may contain a usable key. Everything else refers to a key by its fingerprint.
# ---------------------------------------------------------------------------

function Get-SecretFingerprint {
    # Short, stable, non-reversible identifier for a secret, safe to log/print.
    # SHA-256 over the UTF-8 bytes, first 12 hex chars. Pure function.
    param([string]$Secret)
    if ([string]::IsNullOrEmpty($Secret)) { return '(none)' }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Secret))
        return -join ($hash[0..5] | ForEach-Object { $_.ToString('x2') })
    } finally { $sha.Dispose() }
}

function Format-SecretLine {
    # A log line that identifies a secret without disclosing it.
    # Pure function.
    param(
        [Parameter(Mandatory)][string]$Label,
        [string]$Secret,
        [string]$Source = ''
    )
    if ([string]::IsNullOrEmpty($Secret)) { return "$Label : (absent)" }
    $fp = Get-SecretFingerprint -Secret $Secret
    if ($Source) { return "$Label : fp=$fp  (full value: $Source)" }
    return "$Label : fp=$fp"
}

function Protect-SecretFile {
    # Restrict a file holding credentials to Administrators + SYSTEM + the current
    # user, and drop inherited ACEs. Best-effort and non-fatal: an ACL failure must
    # not abort a deployment. ASCII-only.
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)   # disable inheritance, drop inherited
        foreach ($id in @('BUILTIN\Administrators', 'NT AUTHORITY\SYSTEM', $me)) {
            try {
                $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                    $id, 'FullControl', 'Allow')))
            } catch { }
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
        return $true
    } catch {
        Write-StageLog "[secrets] could not restrict ACL on $Path : $($_.Exception.Message)" 'WARN'
        return $false
    }
}

# ---------------------------------------------------------------------------
# Automatic remediation framework (v2.23.3)
#
# Deploying on an unknown machine fails in a small number of predictable ways.
# This framework retries a recoverable unit of work, and between attempts runs a
# *whitelisted* repair action that removes the known cause of the failure. A
# repair action must be idempotent and must never destroy unrelated machine
# state: the deploy is allowed to clean up after itself, never to reconfigure or
# remove software it did not install.
# ---------------------------------------------------------------------------

function Get-RetryPolicy {
    # action -> { Max int, WaitSec int, Label string, Reason string }
    # Sourced from real failures observed in deploy/logs/. Pure lookup.
    param([Parameter(Mandatory)][string]$Action)
    $table = @{
        'python-install'  = @{ Max = 2; WaitSec = 5;  Reason = 'installer can fail while a reboot-pending MSI lock is held' }
        'postgres-install'= @{ Max = 2; WaitSec = 15; Reason = 'installer can fail on a leftover dir/reboot-pending state' }
        'postgis-install' = @{ Max = 2; WaitSec = 5;  Reason = 'bundle installer needs a settled PostgreSQL service' }
        'redis-start'     = @{ Max = 3; WaitSec = 3;  Reason = 'a stale .pid / half-dead server blocks the port' }
        'venv-build'      = @{ Max = 2; WaitSec = 3;  Reason = 'pip can fail on a half-created venv or a locked wheel' }
        'db-migrate'      = @{ Max = 2; WaitSec = 3;  Reason = 'alembic can fail on a partially applied revision' }
        'api-boot'        = @{ Max = 3; WaitSec = 5;  Reason = '45679 may still be held by a previous attempt' }
        'celery-boot'     = @{ Max = 2; WaitSec = 3;  Reason = 'worker can fail if a redis connection is not ready yet' }
        # Reserved for the regression suite: a retry policy with a real budget but
        # NO registered repair, so retry mechanics can be exercised without any
        # chance of mutating a real machine.
        'unit-test-action' = @{ Max = 2; WaitSec = 1; Reason = 'unit test: retry without side effects' }
    }
    if ($table.ContainsKey($Action)) { return $table[$Action] }
    return @{ Max = 1; WaitSec = 3; Reason = 'generic retry' }
}

function Invoke-Repair {
    # Runs one named, idempotent repair. Returns $true when the repair was applied
    # (or was a no-op that still counts as handled). Never throws: a failing repair
    # must not mask the original failure.
    param(
        [Parameter(Mandatory)][string]$Action,
        [int]$Attempt = 1
    )
    Write-StageLog "[repair] action=$Action attempt=$Attempt" 'WARN'
    try {
        switch ($Action) {
            'python-install' {
                # A pending reboot leaves the MSI mutex held. Nothing to delete in
                # Program Files (the installer owns it); just settle and retry.
                Start-Sleep -Seconds 10
            }
            'postgres-install' {
                # A previous failed attempt can leave the service half-registered.
                # Only stop a service that carries our exact pinned name, and never
                # delete the data directory (it may hold the operator's cluster).
                $svc = Get-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
                if ($svc -and $svc.Status -ne 'Stopped') {
                    Stop-Service -Name 'postgresql-x64-15' -Force -ErrorAction SilentlyContinue
                    Write-StageLog "[repair] stopped half-registered postgresql-x64-15" 'WARN'
                }
                foreach ($stale in 'C:\Program Files\PostgreSQL\15\data\postmaster.pid') {
                    if (Test-Path -LiteralPath $stale) {
                        Remove-Item -LiteralPath $stale -Force -ErrorAction SilentlyContinue
                        Write-StageLog "[repair] removed stale postmaster.pid" 'WARN'
                    }
                }
            }
            'postgis-install' {
                Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
            }
            'redis-start' {
                $redisDir = Join-Path (Get-OfflineDir) 'redis'
                # A stale pid file makes redis refuse to start; a half-dead server
                # keeps the port bound. Both are ours to clean.
                Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -eq 'redis-server.exe' -and $_.CommandLine -like '*redis.conf*' } |
                    ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }
                foreach ($stale in 'redis_6379.pid', 'dump.rdb.tmp') {
                    $p = Join-Path $redisDir $stale
                    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue; Write-StageLog "[repair] removed $stale" 'WARN' }
                }
                Start-Sleep -Seconds 2
            }
            'venv-build' {
                $pr = Get-ProjectRoot
                $venv = Join-Path $pr '.venv'
                # Safety guard: only a real deployment target has the offline bundle
                # next to the scripts. A development checkout (and, emphatically, the
                # test suite) must never have its virtualenv deleted by a repair.
                $isTarget = Test-Path -LiteralPath (Join-Path $pr 'deploy\offline')
                if (-not $isTarget) {
                    Write-StageLog "[repair] refusing to touch '$venv': no deploy\offline bundle here, so this is not a deployment target" 'WARN'
                } elseif (Test-Path -LiteralPath $venv) {
                    # Only ever remove the venv this script creates. It is reproducible
                    # from the bundled wheels, so deleting it is the safe repair.
                    Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue
                    Write-StageLog "[repair] removed a half-created .venv (rebuilt from bundled wheels)" 'WARN'
                }
                & py -3.13 -m ensurepip --upgrade 2>&1 | Out-Null
            }
            'db-migrate' {
                Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
            }
            'api-boot' {
                # 45679 held by our own previous uvicorn = kill it. Anything else is
                # left alone and reported, because we cannot know what it is.
                $held = Get-NetTCPConnection -State Listen -LocalPort 45679 -ErrorAction SilentlyContinue
                foreach ($c in @($held)) {
                    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
                    if ($p -and $p.ProcessName -in @('python', 'python3', 'uvicorn')) {
                        & taskkill.exe /f /t /pid $p.Id 2>&1 | Out-Null
                        Write-StageLog "[repair] freed port 45679 from a previous $($p.ProcessName) (pid $($p.Id))" 'WARN'
                    } else {
                        Write-StageLog "[repair] port 45679 is held by '$($p.ProcessName)' (pid $($p.Id)) - not ours, leaving it alone" 'WARN'
                    }
                }
                Start-Sleep -Seconds 2
            }
            'celery-boot' {
                Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                    Where-Object { $_.CommandLine -like '*celery*worker*' } |
                    ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }
                Start-Sleep -Seconds 2
            }
            default { Write-StageLog "[repair] no repair action registered for '$Action'" 'WARN' }
        }
        return $true
    } catch {
        Write-StageLog "[repair] action=$Action failed: $($_.Exception.Message)" 'WARN'
        return $false
    }
}

function Invoke-WithRetry {
    # Run $Action; on a non-zero exit code run the matching repair and try again,
    # up to the policy's Max attempts. Returns a hashtable:
    #   Ok bool, Attempts int, ExitCode int, Repairs string[]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][scriptblock]$Body,
        [int]$MaxAttempts = 0,
        [int]$WaitSec = 0
    )
    $policy = Get-RetryPolicy -Action $Action
    $max = if ($MaxAttempts -gt 0) { $MaxAttempts } else { [int]$policy.Max }
    if ($max -lt 1) { $max = 1 }
    $wait = if ($WaitSec -gt 0) { $WaitSec } else { [int]$policy.WaitSec }
    $repairs = @()
    $attempt = 0
    while ($attempt -lt $max) {
        $attempt++
        $code = 0
        try { $code = [int](& $Body) } catch { Write-StageLog "[$Name] attempt $attempt threw: $($_.Exception.Message)" 'WARN'; $code = 1 }
        if ($code -eq 0) {
            return @{ Ok = $true; Attempts = $attempt; ExitCode = 0; Repairs = $repairs }
        }
        Write-StageLog "[$Name] attempt $attempt/$max failed (exit $code)" 'WARN'
        if ($attempt -ge $max) { break }
        Write-StageLog "[$Name] auto-repair: $($policy.Reason)" 'WARN'
        Invoke-Repair -Action $Action -Attempt $attempt | Out-Null
        $repairs += $Action
        if ($wait -gt 0) { Start-Sleep -Seconds $wait }
    }
    return @{ Ok = $false; Attempts = $attempt; ExitCode = $code; Repairs = $repairs }
}

function Get-FailureHints {
    # Known, non-automatable conditions -> the exact thing the operator must do.
    # Returns a string[] (may be empty). Never throws. Pure w.r.t. machine state
    # except for read-only probes.
    param([Parameter(Mandatory)][string]$Stage)
    $hints = @()

    if ($Stage -eq '20_install_postgres.ps1') {
        $pg = Get-InstalledPostgres
        if ($pg -and [int]$pg.Major -ne 15) {
            $hints += "Machine already runs PostgreSQL $($pg.Version) (service '$($pg.ServiceName)'). This deploy needs 15.x on port 5432 and will not remove another PostgreSQL for you."
            $hints += "Either uninstall it (Control Panel > Programs and Features), or point this deploy at a dedicated host/port and adjust .env accordingly."
        }
        if (Test-PortListening -Port 5432) {
            try {
                $c = Get-NetTCPConnection -State Listen -LocalPort 5432 -ErrorAction Stop | Select-Object -First 1
                $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
                if ($p) { $hints += "Port 5432 is held by '$($p.ProcessName)' (pid $($p.Id)). Free it, then re-run --from 20." }
            } catch { }
        }
        $hints += "Installer's own log is more precise: %TEMP%\install-postgresql.log and %TEMP%\bitrock_installer*.log"
        $hints += "Leftover state from an earlier attempt: delete 'C:\Program Files\PostgreSQL\15' and 'sc delete postgresql-x64-15', then re-run --from 20."
    }

    if ($Stage -eq '21_install_postgis.ps1') {
        $hints += "PostGIS 3.6.1 bundle requires a *running* PostgreSQL 15: confirm service 'postgresql-x64-15' is Running, then re-run --from 21."
        $hints += "Verify afterwards: Test-Path 'C:\Program Files\PostgreSQL\15\share\extension\postgis.control'"
    }

    if ($Stage -eq '22_install_ace.ps1') {
        $hints += "ACE 2016 x64 cannot install next to a 32-bit Office/Access (error 'bitness'). This stage is non-critical: MDB export degrades, HTTP main path is unaffected."
    }

    if ($Stage -eq '30_setup_redis.ps1') {
        $hints += "Redis is configured to listen on 6379 only (bind 127.0.0.1). If 6379 is already in use, stop that process or change the port in offline\redis\redis.conf AND in the API's REDIS_URL/CELERY_* .env keys."
    }

    if ($Stage -eq '40_setup_venv.ps1') {
        $hints += "Offline pip needs a complete wheel set for cp313/win_amd64 in deploy\offline\wheels. Re-run build_bundle.ps1 to refresh it."
        $hints += "If 'py -3.13' is unavailable, install Python 3.13.3 with 'Include launcher' (setup: offline\installers\python-3.13.3-amd64.exe)."
    }

    if ($Stage -eq '50_init_database.ps1') {
        $hints += "This deploy assumes the PostgreSQL superuser password is the default 'postgres'. A custom password requires editing DATABASE_URL/DATABASE_URL_SYNC in .env to match."
        $hints += "If PostGIS is missing: re-run --from 21, then --from 50."
    }

    if ($Stage -eq '60_seed_and_key.ps1') {
        $hints += "The bootstrap API needs a reachable database (stage 50) and a strong ADMIN_INIT_KEY (written automatically into .env)."
        $hints += "If 45679 was held by a stale process, the repair removes only our own python/uvicorn listener; check deploy\logs\ and server_err.log."
    }

    if ($Stage -eq '90_start_services.ps1') {
        $hints += "Read server_err.log / worker_err.log / flower_err.log in the project root for the real traceback."
        $hints += "If the API starts but /health never answers, verify PostgreSQL 15 is running and .env DATABASE_URL points at it."
    }

    if ($Stage -eq '00_preflight.ps1') {
        $hints += "manifest.json mismatch means deploy\offline was modified after the bundle was built. Re-run build_bundle.ps1 to regenerate manifest.json."
    }

    return $hints
}

function Invoke-Diagnostics {
    # Collect the facts an operator (or the next agent) needs to debug a failure,
    # into deploy/diagnostics/<stamp>/. Read-only except for creating that folder.
    # Never throws.
    param(
        [Parameter(Mandatory)][string]$Reason,
        [string]$Stage = '(none)'
    )
    $outDir = $null
    try {
        $diagRoot = Join-Path (Get-DeployRoot) 'diagnostics'
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $outDir = Join-Path $diagRoot $stamp
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null

        $report = Join-Path $outDir 'summary.txt'
        $lines = @()
        $lines += "VCS deploy diagnostics"
        $lines += "generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        $lines += "stage     : $Stage"
        $lines += "reason    : $Reason"
        $lines += "log       : $($env:VCS_DEPLOY_LOG)"
        $lines += ""
        $lines += "== hints =="
        foreach ($h in (Get-FailureHints -Stage $Stage)) { $lines += "  - $h" }
        $lines += ""
        $lines += "== host =="
        $lines += "  os        : $([Environment]::OSVersion.VersionString)"
        $lines += "  ps        : $($PSVersionTable.PSVersion)"
        $lines += "  admin     : $(Test-Admin)"
        $lines += "  culture   : $((Get-Culture).Name) / codepage $((Get-Culture).TextInfo.ANSICodePage)"
        $pr = Get-ProjectRoot
        try {
            $drive = Split-Path -Qualifier $pr
            $freeGB = [math]::Round((Get-PSDrive -Name $drive.TrimEnd(':')).Free / 1GB, 1)
            $lines += "  disk $drive  : ${freeGB} GB free"
        } catch { }
        $lines += ""
        $lines += "== components =="
        $lines += "  python      : $(try { (& py -3.13 --version 2>&1) -join ' ' } catch { 'not found' })"
        $lines += "  postgres    : $(try { (& 'C:\Program Files\PostgreSQL\15\bin\psql.exe' --version 2>&1) -join ' ' } catch { 'not found' })"
        $pg = Get-InstalledPostgres
        $lines += "  pg probe    : $(if ($pg) { "$($pg.Version) major=$($pg.Major) svc=$($pg.ServiceName) src=$($pg.Source)" } else { 'none detected' })"
        $lines += "  postgis     : $(Test-PostgisInstalled)"
        $lines += "  ace         : $(Test-AceInstalled)"
        $lines += "  vcredist    : $(Test-VcRuntimeInstalled)"
        $lines += "  venv        : $(Test-VenvReady -ProjectRoot $pr)"
        $lines += ""
        $lines += "== ports =="
        foreach ($p in 5432, 6379, 45679, 5555) {
            $owner = ''
            if (Test-PortListening -Port $p) {
                try {
                    $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction Stop | Select-Object -First 1
                    $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
                    if ($proc) { $owner = " <- $($proc.ProcessName) (pid $($proc.Id))" }
                } catch { }
            }
            $lines += "  $p : listening=$(Test-PortListening -Port $p)$owner"
        }
        $lines += ""
        $lines += "== services =="
        foreach ($s in @(Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue)) { $lines += "  $($s.Name) : $($s.Status)" }
        $task = Get-ScheduledTask -TaskName 'VCS-Redis' -ErrorAction SilentlyContinue
        $lines += "  VCS-Redis task : $(if ($task) { $task.State } else { 'absent' })"
        $lines += ""
        $lines += "== portable copies =="
        foreach ($f in 'manifest.json') {
            $src = Join-Path (Get-DeployRoot) $f
            if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $outDir $f) -Force; $lines += "  copied $f" }
        }
        # SECRETS: .install_state.json is NOT copied into the bundle. This bundle is
        # documented as "send it to the troubleshooter", so it must not carry usable
        # credentials. Record only where the keys live and their fingerprints.
        $stateFile = Join-Path (Get-DeployRoot) '.install_state.json'
        $lines += "  NOT copied .install_state.json (contains credentials; kept on the host)"
        if (Test-Path -LiteralPath $stateFile) {
            try {
                $st = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
                $lines += "  credential fingerprints (full values stay in deploy\.install_state.json):"
                $lines += "    ADMIN_INIT_KEY fp=$(Get-SecretFingerprint -Secret $st.admin_init_key)"
                $lines += "    API_KEY        fp=$(Get-SecretFingerprint -Secret $st.api_key)"
            } catch { $lines += "    (install state unreadable: $($_.Exception.Message))" }
        } else {
            $lines += "  .install_state.json absent - no credentials were generated on this host"
        }
        if ($env:VCS_DEPLOY_LOG -and (Test-Path -LiteralPath $env:VCS_DEPLOY_LOG)) {
            Copy-Item -LiteralPath $env:VCS_DEPLOY_LOG -Destination (Join-Path $outDir 'deploy.log') -Force
            $lines += "  copied deploy.log"
        }
        foreach ($f in 'server_err.log', 'server_out.log', 'worker_err.log', 'flower_err.log') {
            $src = Join-Path $pr $f
            if (Test-Path -LiteralPath $src) {
                $dst = Join-Path $outDir $f
                Get-Content -LiteralPath $src -Tail 200 | Set-Content -LiteralPath $dst -Encoding UTF8
                $lines += "  copied $f (last 200 lines)"
            }
        }
        foreach ($f in 'install-postgresql.log', 'bitrock_installer.log') {
            $src = Join-Path $env:TEMP $f
            if (Test-Path -LiteralPath $src) {
                Get-Content -LiteralPath $src -Tail 200 | Set-Content -LiteralPath (Join-Path $outDir $f) -Encoding UTF8
                $lines += "  copied %TEMP%\$f (last 200 lines)"
            }
        }
        Get-ChildItem -LiteralPath $env:TEMP -Filter 'bitrock_installer*.log' -ErrorAction SilentlyContinue |
            ForEach-Object {
                Get-Content -LiteralPath $_.FullName -Tail 200 | Set-Content -LiteralPath (Join-Path $outDir $_.Name) -Encoding UTF8
                $lines += "  copied %TEMP%\$($_.Name) (last 200 lines)"
            }
        $lines += ""
        $lines += "== windows application log (postgresql source, last 20) =="
        try {
            Get-WinEvent -FilterHashtable @{ LogName='Application'; StartTime=(Get-Date).AddDays(-1) } -MaxEvents 200 -ErrorAction Stop |
                Where-Object { $_.Message -match 'postgres' } | Select-Object -First 20 |
                ForEach-Object { $lines += "  [$($_.TimeCreated)] $($_.LevelDisplayName): $(($_.Message -split "`n")[0])" }
        } catch { $lines += "  (unavailable: $($_.Exception.Message))" }

        $lines | Set-Content -LiteralPath $report -Encoding UTF8

        # Keep the bundle directory bounded: a repeatedly-retried deploy would
        # otherwise pile up a copy of the logs per failure.
        $all = @(Get-ChildItem -LiteralPath $diagRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
        if ($all.Count -gt 5) {
            $all | Select-Object -Skip 5 | ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        Write-StageLog "[diagnostics] written to $outDir" 'WARN'
    } catch {
        Write-StageLog "[diagnostics] collection failed: $($_.Exception.Message)" 'WARN'
    }
    return $outDir
}

function Get-ProjectVersion {
    # Single source of truth for the reported version: the FastAPI app version.
    # Falls back to a marked unknown so a reader can tell "not read" from "2.x.y".
    param([string]$ProjectRoot = '')
    try {
        $roots = @()
        if ($ProjectRoot) { $roots += $ProjectRoot }
        try { $roots += (Get-ProjectRoot) } catch { }
        if ($PSScriptRoot) { $roots += (Join-Path $PSScriptRoot '..\..') }
        foreach ($r in $roots) {
            if (-not $r) { continue }
            $main = Join-Path $r 'app\main.py'
            if (-not (Test-Path -LiteralPath $main)) { continue }
            $m = [regex]::Match((Get-Content -LiteralPath $main -Raw -Encoding UTF8), 'version\s*=\s*"([^"]+)"')
            if ($m.Success) { return $m.Groups[1].Value }
        }
    } catch { }
    return '0.0.0-unknown'
}

function Write-DeployResult {
    # Machine-readable deploy outcome, so a scheduler/technician can read the verdict
    # without parsing the log. Written atomically. Never throws.
    param(
        [Parameter(Mandatory)][string]$Result,
        [string]$Stage = '',
        [string]$Reason = '',
        [int]$ExitCode = 0,
        [array]$Stages = @(),
        [string]$DiagnosticsDir = '',
        [hashtable]$Extra = @{}
    )
    try {
        $stateFile = Join-Path (Get-DeployRoot) '.install_state.json'
        $state = $null
        if (Test-Path -LiteralPath $stateFile) {
            try { $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        }
        $obj = [ordered]@{
            version        = (Get-ProjectVersion)
            result         = $Result
            finished_at    = (Get-Date).ToString('s')
            failed_stage   = $Stage
            reason         = $Reason
            exit_code      = $ExitCode
            diagnostics    = $DiagnosticsDir
            stages         = @($Stages)
            # SECRETS: never inline the keys here. last_result.json is a status file
            # that gets copied around; it carries only a fingerprint plus the path of
            # the single file that does hold them.
            credentials    = [ordered]@{
                source             = 'deploy\.install_state.json'
                admin_init_key_fp  = if ($state) { Get-SecretFingerprint -Secret $state.admin_init_key } else { '(none)' }
                api_key_fp         = if ($state) { Get-SecretFingerprint -Secret $state.api_key } else { '(none)' }
            }
            deploy_log     = $env:VCS_DEPLOY_LOG
        }
        foreach ($k in $Extra.Keys) { $obj[$k] = $Extra[$k] }
        $out = Join-Path (Get-DeployRoot) 'last_result.json'
        $tmp = "$out.tmp"
        $obj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmp -Encoding UTF8
        Move-Item -LiteralPath $tmp -Destination $out -Force
    } catch {
        Write-StageLog "[result] could not write last_result.json: $($_.Exception.Message)" 'WARN'
    }
}
