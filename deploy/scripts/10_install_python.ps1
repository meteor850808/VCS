param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[10_python] begin"
if (Test-Python313Installed) { Write-StageLog "[10_python] Python 3.13 already present -> SKIP" 'SKIP'; exit 0 }
if ($DryRun) { Write-StageLog "[10_python] DRY-RUN would install Python 3.13.3" 'INFO'; exit 0 }

Assert-Admin
$exe = Join-Path (Get-OfflineDir) 'installers\python-3.13.3-amd64.exe'
$args = @('/quiet','InstallAllUsers=1','PrependPath=1','Include_pip=1','Include_test=0')
$code = Invoke-SilentInstaller -FilePath $exe -ArgumentList $args
Write-StageLog "[10_python] installer exit code = $code"

# refresh PATH for current process
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
if (Test-Python313Installed) { Write-StageLog "[10_python] OK" 'OK'; exit 0 }
Write-StageLog "[10_python] FAILED - py -3.13 not available after install" 'ERROR'
exit 1
