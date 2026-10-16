param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[20_postgres] begin"
$pgRoot = 'C:\Program Files\PostgreSQL\15'
$psql = Join-Path $pgRoot 'bin\psql.exe'
$port = 5432

# Detect first, decide second - even under -DryRun the operator must be able to
# see "this machine already has PostgreSQL 16" before anything is touched.
$existing = Get-InstalledPostgres
$decision = Get-PostgresInstallDecision -Install $existing

# Log-only remediation hint. Deliberately does NOT run any uninstaller.
function Write-PgManualRemediation {
    param([string]$Reason)
    Write-StageLog "[20_postgres] $Reason" 'ERROR'
    Write-StageLog "[20_postgres] this script never removes an existing PostgreSQL automatically." 'INFO'
    Write-StageLog "[20_postgres] do this on the target machine, then re-run: deploy_offline.bat --from 20" 'INFO'
    Write-StageLog "[20_postgres]   1) Control Panel > Programs and Features > uninstall the PostgreSQL entry above" 'INFO'
    Write-StageLog "[20_postgres]   2) delete the leftover directory if any: 'C:\Program Files\PostgreSQL\<major>'" 'INFO'
    Write-StageLog "[20_postgres]   3) remove the leftover registry key: HKLM\SOFTWARE\PostgreSQL\Installations\<key>" 'INFO'
    Write-StageLog "[20_postgres]   4) also clear: sc delete postgresql-x64-<major>" 'INFO'
    Write-StageLog "[20_postgres]   5) confirm port $port is free: netstat -ano | findstr :$port" 'INFO'
}

if ($decision -eq 'skip') {
    $src = if ($existing.Source) { $existing.Source } else { 'detected' }
    Write-StageLog "[20_postgres] usable PostgreSQL 15 detected ($src) -> reuse, SKIP" 'SKIP'
    exit 0
}

if ($decision -eq 'conflict') {
    $ver = if ($existing.Version) { $existing.Version } else { "major $($existing.Major)" }
    Write-PgManualRemediation -Reason "incompatible PostgreSQL $ver found (service '$($existing.ServiceName)'); this deploy requires 15.x on port $port"
    exit 1
}

if ($decision -eq 'unknown') {
    Write-StageLog "[20_postgres] a PostgreSQL install or listener was detected but its major version is unknown; refusing to guess" 'ERROR'
    Write-PgManualRemediation -Reason "cannot determine the installed PostgreSQL version"
    exit 1
}

# decision = install. Everything below is destructive-ish (writes Program Files,
# registers a service, creates a cluster), so dry-run stops right here.
if ($DryRun) { Write-StageLog "[20_postgres] DRY-RUN would install PostgreSQL 15.15" 'INFO'; exit 0 }

Assert-Admin

# If something is already serving the port, the EDB installer cannot create the
# cluster and fails with an opaque exit code (~15s to 3min depending on where it
# dies). Catch it here so the operator gets a one-line, actionable reason.
if (Test-PortListening -Port $port) {
    $owner = ''
    try {
        $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop | Select-Object -First 1
        $op = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($op) { $owner = " (pid $($op.Id), $($op.ProcessName))" }
    } catch { }
    Write-StageLog "[20_postgres] port $port is already in use$owner - PostgreSQL 15 cannot create its cluster" 'ERROR'
    Write-PgManualRemediation -Reason "port $port is occupied before the PostgreSQL 15 install"
    exit 1
}

$saPwd = if ($env:VCS_PG_SUPERPASSWORD) { $env:VCS_PG_SUPERPASSWORD } else { 'postgres' }
$exe = Join-Path (Get-OfflineDir) 'installers\postgresql-15.15-2-windows-x64.exe'
$args = Get-PostgresInstallerArg -SuperPassword $saPwd
$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList $args
# 0 = success, 3010 = success but reboot pending
if ($code -notin @(0,3010)) {
    Write-StageLog "[20_postgres] installer exit $code - inspect %TEMP%\install-postgresql.log and %TEMP%\bitrock_installer*.log" 'ERROR'
    exit 1
}

# Post-install verification: service registered AND client binary laid down
# AND the server actually answers on the port.
if (-not (Test-Pg15Installed)) {
    Write-StageLog "[20_postgres] FAILED - service postgresql-x64-15 not found after install" 'ERROR'
    exit 1
}
Start-Service -Name 'postgresql-x64-15' -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $psql)) {
    Write-StageLog "[20_postgres] FAILED - service present but bin\psql.exe missing (partial install)" 'ERROR'
    Write-StageLog "[20_postgres] uninstall the PostgreSQL 15 entry, delete '$pgRoot', then re-run --from 20" 'INFO'
    exit 1
}
$listening = $false
for ($i = 0; $i -lt 30; $i++) { if (Test-PortListening -Port $port) { $listening = $true; break }; Start-Sleep -Seconds 1 }
if (-not $listening) {
    Write-StageLog "[20_postgres] FAILED - service registered but $port is not listening (cluster init failed?)" 'ERROR'
    Write-StageLog "[20_postgres] inspect '$pgRoot\data\log' and %TEMP%\install-postgresql.log" 'INFO'
    exit 1
}
Write-StageLog "[20_postgres] OK (service up, port $port listening)" 'OK'
exit 0
