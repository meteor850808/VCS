param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[99_verify] begin"
$pr = Get-ProjectRoot
$stateFile = Join-Path (Get-DeployRoot) '.install_state.json'
$port = 45679

$rows = @()
$rows += [pscustomobject]@{ Item='Python'; Detail=$(try { (& py -3.13 --version 2>&1) -join ' ' } catch { 'not found' }) }
$rows += [pscustomobject]@{ Item='PostgreSQL'; Detail=$(try { (& 'C:\Program Files\PostgreSQL\15\bin\psql.exe' --version 2>&1) -join ' ' } catch { 'not found' }) }
$rows += [pscustomobject]@{ Item='PostGIS'; Detail=("installed=" + (Test-PostgisInstalled)) }
$rows += [pscustomobject]@{ Item='Redis:6379'; Detail=("listening=" + (Test-PortListening -Port 6379)) }
$rows += [pscustomobject]@{ Item='API:45679'; Detail=("listening=" + (Test-PortListening -Port $port)) }
$rows += [pscustomobject]@{ Item='Flower:5555'; Detail=("listening=" + (Test-PortListening -Port 5555)) }
$rows += [pscustomobject]@{ Item='ACE'; Detail=("installed=" + (Test-AceInstalled)) }

Write-StageLog "---------------- VERIFY REPORT ----------------" 'INFO'
foreach ($r in $rows) { Write-StageLog ("{0,-14} {1}" -f $r.Item, $r.Detail) 'INFO' }

if (Test-Path -LiteralPath $stateFile) {
    $s = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    # Fingerprints, not values: this output also lands in deploy\logs\.
    Write-StageLog (Format-SecretLine -Label 'ADMIN_INIT_KEY' -Secret $s.admin_init_key -Source '.install_state.json') 'OK'
    Write-StageLog (Format-SecretLine -Label 'API_KEY' -Secret $s.api_key -Source '.install_state.json') 'OK'
}
Write-StageLog "Frontend : http://127.0.0.1:$port/app/" 'OK'
Write-StageLog "API Docs : http://127.0.0.1:$port/docs" 'OK'
Write-StageLog "Flower   : http://127.0.0.1:5555" 'OK'
Write-StageLog "-----------------------------------------------" 'INFO'
Write-StageLog "[99_verify] OK" 'OK'
exit 0
