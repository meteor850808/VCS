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
$action  = New-ScheduledTaskAction -Execute $exe -Argument 'redis.conf' -WorkingDirectory $redisDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'VCS-Redis' -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null

Write-StageLog "[30_redis] OK (started + task VCS-Redis registered)" 'OK'
exit 0
