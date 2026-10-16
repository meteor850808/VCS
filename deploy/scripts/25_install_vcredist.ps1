param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[25_vcredist] begin"
if (Test-VcRuntimeInstalled) { Write-StageLog "[25_vcredist] VC++ runtime already present -> SKIP" 'SKIP'; exit 0 }
$exe = Join-Path (Get-OfflineDir) 'installers\MSVBCRT_AIO_x64.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    Write-StageLog "[25_vcredist] installer not bundled -> SKIP" 'WARN'; exit 0
}
if ($DryRun) { Write-StageLog "[25_vcredist] DRY-RUN would install VC++ runtime AIO x64" 'INFO'; exit 0 }

$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList @('/quiet','/norestart')
if ($code -notin @(0,3010)) { Write-StageLog "[25_vcredist] installer exit $code (non-fatal)" 'WARN'; exit 0 }
Write-StageLog "[25_vcredist] OK" 'OK'
exit 0
