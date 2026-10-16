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
