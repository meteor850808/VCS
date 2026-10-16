param([switch]$DryRun, [switch]$Unattended)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[90_start] begin"
$pr = Get-ProjectRoot
$py = Join-Path $pr '.venv\Scripts\python.exe'
$uvicorn = Join-Path $pr '.venv\Scripts\uvicorn.exe'
$celery  = Join-Path $pr '.venv\Scripts\celery.exe'
$port = 45679
$unattended = $Unattended -or ($env:VCS_DEPLOY_UNATTENDED -eq '1')

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
if (-not $ok) {
    Write-StageLog "[90_start] health check failed; see server_err.log" 'ERROR'
    if (Test-Path -LiteralPath (Join-Path $pr 'server_err.log')) {
        Get-Content -LiteralPath (Join-Path $pr 'server_err.log') -Tail 15 | ForEach-Object { Write-StageLog "  server_err: $_" 'ERROR' }
    }
    exit 1
}

# Opening a browser is a convenience for a human at the console; it is noise (and
# can fail) when the deploy runs from a scheduler. -Unattended / --unattended
# (or VCS_DEPLOY_UNATTENDED=1) suppresses it.
if ($unattended) {
    Write-StageLog "[90_start] unattended mode - not opening a browser" 'INFO'
} else {
    try { Start-Process "http://127.0.0.1:$port/app/" } catch { Write-StageLog "[90_start] could not open browser: $_" 'WARN' }
}
Write-StageLog "[90_start] OK (frontend http://127.0.0.1:$port/app/)" 'OK'
exit 0
