param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[50_db] begin"
$pr = Get-ProjectRoot
$pwd = if ($env:VCS_PG_SUPERPASSWORD) { $env:VCS_PG_SUPERPASSWORD } else { 'postgres' }
$psql = 'C:\Program Files\PostgreSQL\15\bin\psql.exe'
$alembic = Join-Path $pr '.venv\Scripts\alembic.exe'
$port = 5432

# Dry-run is a read-only health check: it must never fail because a component is
# absent, otherwise 'deploy_offline.bat --dry-run' turns red on any machine
# without PG15 and the operator concludes the package is broken. Missing pieces
# are reported and the stage still exits 0. A real run keeps failing hard.
if ($DryRun) {
    if (-not (Test-Path -LiteralPath $psql)) {
        Write-StageLog "[50_db] DRY-RUN: psql not found at $psql (stage 20 would install it)" 'WARN'
    }
    if (-not (Test-Path -LiteralPath $alembic)) {
        Write-StageLog "[50_db] DRY-RUN: alembic not found at $alembic (stage 40 would create it)" 'WARN'
    }
    Write-StageLog "[50_db] DRY-RUN would create database vcs_db, enable PostGIS and run alembic upgrade head" 'INFO'
    exit 0
}

if (-not (Test-Path -LiteralPath $psql)) {
    Write-StageLog "[50_db] psql not found: $psql" 'ERROR'
    Write-StageLog "[50_db] stage 20 must have installed PostgreSQL 15 first; re-run: deploy_offline.bat --from 20" 'INFO'
    exit 1
}
if (-not (Test-Path -LiteralPath $alembic)) {
    Write-StageLog "[50_db] alembic not found (run stage 40 first)" 'ERROR'
    exit 1
}
if (-not (Test-PortListening -Port $port)) {
    Write-StageLog "[50_db] nothing is listening on port $port - PostgreSQL service is not running" 'ERROR'
    Write-StageLog "[50_db] start it with: Start-Service postgresql-x64-15" 'INFO'
    exit 1
}

$env:PGPASSWORD = $pwd
$env:PGCLIENTENCODING = 'UTF8'

# Fail fast when the server rejects our credentials, instead of letting alembic
# report it later in a much less readable way.
& $psql -U postgres -h localhost -p $port -tAc "SELECT 1" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-StageLog "[50_db] cannot authenticate as 'postgres' on localhost:$port (password mismatch?)" 'ERROR'
    Write-StageLog "[50_db] this deploy assumes the default password 'postgres'; VCS_PG_SUPERPASSWORD is only supported if the .env connection string matches it" 'INFO'
    exit 1
}

# create db if absent
$exists = (& $psql -U postgres -h localhost -p $port -tAc "SELECT 1 FROM pg_database WHERE datname='vcs_db'") | Out-String
if ($exists.Trim() -ne '1') {
    Write-StageLog "[50_db] creating database vcs_db" 'INFO'
    & $psql -U postgres -h localhost -p $port -c 'CREATE DATABASE vcs_db'
    if ($LASTEXITCODE -ne 0) { Write-StageLog "[50_db] createdb failed" 'ERROR'; exit 1 }
} else {
    Write-StageLog "[50_db] database vcs_db already exists" 'INFO'
}

# enable postgis
& $psql -U postgres -h localhost -p $port -d vcs_db -c 'CREATE EXTENSION IF NOT EXISTS postgis'
if ($LASTEXITCODE -ne 0) {
    Write-StageLog "[50_db] CREATE EXTENSION postgis failed - is PostGIS installed (stage 21)?" 'ERROR'
    exit 1
}

# migrate to head. alembic resolves its URL from app.config settings, i.e. from
# .env when present and otherwise from the postgres:postgres@localhost:5432/vcs_db
# default - which is exactly the database created above.
Push-Location $pr
& $alembic upgrade head
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { Write-StageLog "[50_db] alembic upgrade failed (see output above)" 'ERROR'; exit 1 }

Write-StageLog "[50_db] OK" 'OK'
exit 0
