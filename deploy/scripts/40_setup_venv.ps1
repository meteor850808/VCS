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
