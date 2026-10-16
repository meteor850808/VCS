# VCS offline bundle builder.
#
# SELF-SUFFICIENT BY DEFAULT: every source below defaults to a path *inside*
# deploy/offline/. Running this script with no arguments on a machine that already
# has a populated offline/ therefore just re-verifies and re-emits manifest.json
# (nothing is copied, because source and destination are the same file). The
# deploy/ directory is the single source of truth - no external archive folder, no
# machine-specific path, and no network access is required.
#
# Pass an explicit path to refresh one component from elsewhere, e.g.
#   .\build_bundle.ps1 -PgInstaller D:\downloads\postgresql-15.15-2-windows-x64.exe
# Only non-ASCII values supplied that way can introduce CJK literals; this file is
# ASCII-only on purpose so Windows PowerShell 5.1 never has to guess its encoding
# (a .ps1 with no BOM is decoded with the system ANSI codepage, 936/GBK on zh-CN).
# deploy/tests/run_tests.ps1 guards both invariants: ASCII-only, and no source
# default may point outside deploy/offline/.
param(
    [string]$PythonInstaller  = '',
    [string]$PgInstaller      = '',
    [string]$PostgisInstaller = '',
    [string]$AceInstaller     = '',
    [string]$VcInstaller      = '',
    [string]$SeedDir          = '',
    [string]$RedisSrc         = '',
    [string]$LibreDwgSrc      = '',
    [string]$WheelsDir        = '',
    [switch]$SkipWheels
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\scripts\common.ps1"

$off = Get-OfflineDir
$pr  = Get-ProjectRoot
foreach ($d in 'installers','redis','libredwg','wheels','seed') {
    New-Item -ItemType Directory -Path (Join-Path $off $d) -Force | Out-Null
}

# Resolve a source parameter: use the caller's path when given, otherwise fall
# back to the already-present copy inside offline/. Returning $null means "no
# source at all" and the caller decides whether that is fatal.
function Resolve-SourcePath {
    param([string]$Given, [string]$Fallback)
    if ($Given) { return $Given }
    if ($Fallback -and (Test-Path -LiteralPath $Fallback)) { return $Fallback }
    return $null
}

function Get-NormalizedPath {
    param([string]$Path)
    try { return (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path.ToLowerInvariant() }
    catch { return ([System.IO.Path]::GetFullPath($Path)).ToLowerInvariant() }
}

# Copy one file onto a canonical name inside offline/. Idempotent: when source and
# destination are the same file (the default case) it does nothing instead of
# throwing "source and destination path must be different".
function Copy-To {
    param([string]$Source, [string]$DestDir, [string]$DestName)
    if (-not (Test-Path -LiteralPath $Source)) { return $false }
    $dst = Join-Path $DestDir $DestName
    if ((Get-NormalizedPath -Path $Source) -eq (Get-NormalizedPath -Path $dst)) {
        Write-StageLog "in place -> offline/$DestName (already the source)" 'SKIP'
        return $true
    }
    Copy-Item -LiteralPath $Source -Destination $dst -Force
    Write-StageLog "copied -> offline/$DestName" 'OK'
    return $true
}

# Same idea for a directory: only copy entries that are not already the target.
function Copy-TreeContents {
    param([string]$Source, [string]$DestDir)
    if ((Get-NormalizedPath -Path $Source) -eq (Get-NormalizedPath -Path $DestDir)) { return }
    Get-ChildItem -LiteralPath $Source -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            $dst = Join-Path $DestDir $_.Name
            if ((Get-NormalizedPath -Path $_.FullName) -eq (Get-NormalizedPath -Path $dst)) { return }
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
        }
}

# ---------------------------------------------------------------------------
# 1) installers (canonical names)
# ---------------------------------------------------------------------------
$instDir = Join-Path $off 'installers'
$missing = @()
$specs = @(
    @{ Given=$PythonInstaller;  Name='python-3.13.3-amd64.exe';                Label='Python 3.13.3' },
    @{ Given=$PgInstaller;      Name='postgresql-15.15-2-windows-x64.exe';     Label='PostgreSQL 15.15' },
    @{ Given=$PostgisInstaller; Name='postgis-bundle-pg15x64-setup-3.6.1-1.exe'; Label='PostGIS 3.6.1' },
    @{ Given=$VcInstaller;      Name='MSVBCRT_AIO_x64.exe';                    Label='VC++ 2019 runtime' }
)
foreach ($s in $specs) {
    $src = Resolve-SourcePath -Given $s.Given -Fallback (Join-Path $instDir $s.Name)
    if (-not $src) { $missing += $s.Label; continue }
    Copy-To -Source $src -DestDir $instDir -DestName $s.Name | Out-Null
}

# ACE (Access Database Engine) is optional: the 22_install_ace stage degrades to a
# WARN when it is absent, so a bundle without it still deploys (MDB export off).
$aceName = 'accessdatabaseengine_X64.exe'
$aceSrc = Resolve-SourcePath -Given $AceInstaller -Fallback (Join-Path $instDir $aceName)
if ($aceSrc) { Copy-To -Source $aceSrc -DestDir $instDir -DestName $aceName | Out-Null }
else { Write-StageLog "ACE installer not bundled (-AceInstaller). The ACE stage degrades to a WARN at install time." 'WARN' }

if ($missing.Count -gt 0) {
    throw ("offline/installers is incomplete and no external source was given.`n" +
           "Missing: " + ($missing -join ', ') + "`n" +
           "Either restore them under deploy/offline/installers/, or pass explicit paths, e.g.`n" +
           "  .\build_bundle.ps1 -PythonInstaller <path> -PgInstaller <path> -PostgisInstaller <path> -VcInstaller <path>")
}
Write-StageLog "installers: 4 canonical present (+ ACE if bundled)" 'OK'

# ---------------------------------------------------------------------------
# 2) redis (binary payload only - runtime artifacts must never ship)
# ---------------------------------------------------------------------------
$redisDst = Join-Path $off 'redis'
$redisSrcResolved = Resolve-SourcePath -Given $RedisSrc -Fallback (Join-Path $pr 'Redis8')
if ($redisSrcResolved) {
    $redisFiles = @(Get-ChildItem -LiteralPath $redisSrcResolved -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.exe', '.dll' -or $_.Name -eq 'redis.conf' })
    foreach ($f in $redisFiles) {
        $dst = Join-Path $redisDst $f.Name
        if ((Get-NormalizedPath -Path $f.FullName) -eq (Get-NormalizedPath -Path $dst)) { continue }
        Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
    }
}

# A stale dump.rdb makes the target machine's Redis start with foreign data, and a
# stale .pid file can stop it from starting at all. They are also NOT in the
# manifest, so 00_preflight cannot catch them - the operator would get a silently
# dirty bundle. Drop them on every build.
foreach ($stale in 'dump.rdb', 'redis_6379.pid') {
    $sp = Join-Path $redisDst $stale
    if (Test-Path -LiteralPath $sp) { Remove-Item -LiteralPath $sp -Force; Write-StageLog "removed stale redis artifact: $stale" 'WARN' }
}
Get-ChildItem -LiteralPath $redisDst -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in '.aof', '.pid' } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force; Write-StageLog "removed stale redis artifact: $($_.Name)" 'WARN' }
if (Test-Path -LiteralPath (Join-Path $redisDst 'appendonlydir')) {
    Remove-Item -LiteralPath (Join-Path $redisDst 'appendonlydir') -Recurse -Force
    Write-StageLog "removed stale redis artifact: appendonlydir/" 'WARN'
}

$redisExe = Join-Path $redisDst 'redis-server.exe'
$redisConf = Join-Path $redisDst 'redis.conf'
if (-not (Test-Path -LiteralPath $redisExe) -or -not (Test-Path -LiteralPath $redisConf)) {
    throw "offline/redis is incomplete: need redis-server.exe and redis.conf (source: $redisSrcResolved)"
}
Write-StageLog "redis: $(@(Get-ChildItem -LiteralPath $redisDst -File).Count) files ready" 'OK'

# ---------------------------------------------------------------------------
# 3) libredwg
# ---------------------------------------------------------------------------
$dwgDst = Join-Path $off 'libredwg'
$dwgSrc = Resolve-SourcePath -Given $LibreDwgSrc -Fallback (Join-Path $pr 'tools\libredwg')
if ($dwgSrc) { Copy-TreeContents -Source $dwgSrc -DestDir $dwgDst }
if (-not (Test-Path -LiteralPath (Join-Path $dwgDst 'dwg2dxf.exe'))) {
    throw "offline/libredwg is incomplete: dwg2dxf.exe not found (source: $dwgSrc)"
}
Write-StageLog "libredwg: $(@(Get-ChildItem -LiteralPath $dwgDst -File).Count) files ready" 'OK'

# ---------------------------------------------------------------------------
# 4) seed (reference standard JSON)
# ---------------------------------------------------------------------------
$seedDst = Join-Path $off 'seed'
$seedSrc = Resolve-SourcePath -Given $SeedDir -Fallback $seedDst
if ($seedSrc) { Copy-TreeContents -Source $seedSrc -DestDir $seedDst }
$seedCount = @(Get-ChildItem -LiteralPath $seedDst -Filter '*.json' -File -ErrorAction SilentlyContinue).Count
if ($seedCount -eq 0) {
    throw "offline/seed is empty: no reference standard JSON (source: $seedSrc). Pass -SeedDir <dir>."
}
Write-StageLog "seed: $seedCount json" 'OK'

# ---------------------------------------------------------------------------
# 5) wheels (offline pip download for cp313 / win_amd64)
# ---------------------------------------------------------------------------
$wheelDst = Join-Path $off 'wheels'
$wheelSrc = Resolve-SourcePath -Given $WheelsDir -Fallback $wheelDst
$sameWheelDir = $wheelSrc -and ((Get-NormalizedPath -Path $wheelSrc) -eq (Get-NormalizedPath -Path $wheelDst))
$wheelCount = @(Get-ChildItem -LiteralPath $wheelDst -File -ErrorAction SilentlyContinue).Count
if ($SkipWheels -or $sameWheelDir) {
    if ($wheelCount -eq 0) { throw "offline/wheels is empty and wheel download was skipped; pass -SkipWheels only when the wheels are already bundled" }
    if ($SkipWheels) { Write-StageLog "wheels: $wheelCount files reused (-SkipWheels)" 'SKIP' }
    else { Write-StageLog "wheels: $wheelCount files already bundled (source is offline/wheels)" 'SKIP' }
} else {
    if (-not (Test-CommandExists -Name 'py')) { throw "py launcher required to download wheels" }
    Write-StageLog "downloading wheels (py -3.13 -m pip download) from $wheelSrc ..." 'INFO'
    & py -3.13 -m pip download -r (Join-Path $pr 'requirements.txt') `
        -d $wheelDst --only-binary=:all:
    if ($LASTEXITCODE -ne 0) { throw "pip download failed" }
    $wheelCount = @(Get-ChildItem -LiteralPath $wheelDst -File).Count
    Write-StageLog "wheels: $wheelCount files" 'OK'
}

# ---------------------------------------------------------------------------
# 6) manifest (all files under offline/, relative path + sha256 + size)
# ---------------------------------------------------------------------------
$files = @(Get-ChildItem -LiteralPath $off -Recurse -File |
    Where-Object { $_.Name -ne 'manifest.json' } |
    ForEach-Object {
        [ordered]@{
            path   = $_.FullName.Substring($off.Length + 1).Replace('\','/')
            sha256 = Get-FileSha256 -Path $_.FullName
            size   = $_.Length
        }
    })
$manifest = [ordered]@{
    version  = '1'
    created  = (Get-Date).ToString('s')
    python   = '3.13.3'
    postgres = '15.15'
    postgis  = '3.6.1'
    files    = $files
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $off 'manifest.json') -Encoding UTF8
$totalMB = [math]::Round((($files | ForEach-Object { [long]$_['size'] } | Measure-Object -Sum).Sum) / 1MB, 1)
Write-StageLog "manifest.json written: $($files.Count) files, $totalMB MB" 'OK'
exit 0
