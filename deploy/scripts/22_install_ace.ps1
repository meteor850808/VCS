param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[22_ace] begin"
if (Test-AceInstalled) { Write-StageLog "[22_ace] ACE already present -> SKIP" 'SKIP'; exit 0 }
$exe = Join-Path (Get-OfflineDir) 'installers\accessdatabaseengine_X64.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-StageLog "[22_ace] installer not bundled -> SKIP (MDB feature degraded)" 'WARN'; exit 0
}
if ($DryRun) { Write-StageLog "[22_ace] DRY-RUN would install ACE 2016 x64" 'INFO'; exit 0 }

$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList @('/quiet','/norestart')
if ($code -notin @(0,3010)) { Write-StageLog "[22_ace] installer exit $code (non-fatal)" 'WARN'; exit 0 }
if (Test-AceInstalled) { Write-StageLog "[22_ace] OK" 'OK' } else { Write-StageLog "[22_ace] post-check failed (non-fatal)" 'WARN' }
exit 0
