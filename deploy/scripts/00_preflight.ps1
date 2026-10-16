param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[00_preflight] begin"
$fail = @()

# 1) admin
Assert-Admin
Write-StageLog "admin: OK" 'OK'

# 2) x64
if ([Environment]::Is64BitOperatingSystem) { Write-StageLog "os: 64-bit OK" 'OK' }
else { $fail += "target must be 64-bit Windows" }

# 3) disk space (require >= 3 GB free on project drive)
$pr = Get-ProjectRoot
$drive = (Split-Path -Qualifier $pr)
$freeGB = [math]::Round((Get-PSDrive -Name $drive.TrimEnd(':')).Free / 1GB, 1)
if ($freeGB -ge 3) { Write-StageLog "free space on $drive = ${freeGB} GB OK" 'OK' }
else { $fail += "insufficient disk on $drive ($freeGB GB, need >=3 GB)" }

# 4) manifest integrity
$off = Get-OfflineDir
try { $m = Assert-Manifest -OfflineDir $off; Write-StageLog "manifest: $($m.files.Count) files verified OK" 'OK' }
catch { $fail += "manifest: $($_.Exception.Message)" }

if ($fail.Count -gt 0) {
    foreach ($f in $fail) { Write-StageLog $f 'ERROR' }
    Write-StageLog "[00_preflight] FAILED" 'ERROR'
    exit 1
}
Write-StageLog "[00_preflight] OK" 'OK'
exit 0
