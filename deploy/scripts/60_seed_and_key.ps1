param([switch]$DryRun)
. "$PSScriptRoot\common.ps1"

Write-StageLog "[60_seed] begin"
$pr = Get-ProjectRoot
$deployRoot = Get-DeployRoot
$stateFile = Join-Path $deployRoot '.install_state.json'
$envFile = Join-Path $pr '.env'
$py = Join-Path $pr '.venv\Scripts\python.exe'

# already done?
if ((Test-Path -LiteralPath $stateFile) -and (Test-Path -LiteralPath $envFile)) {
    Write-StageLog "[60_seed] .env + install state present -> SKIP" 'SKIP'; exit 0
}
if ($DryRun) { Write-StageLog "[60_seed] DRY-RUN would write .env, create API key, import seed" 'INFO'; exit 0 }

if (-not (Test-Path -LiteralPath $py)) { Write-StageLog "[60_seed] venv python missing (run stage 40)" 'ERROR'; exit 1 }

# 1) write .env (from .env.example, override secrets/URLs)
$adminKey = New-RandomKey -Bytes 32
$tmpl = Get-Content -LiteralPath (Join-Path $pr '.env.example') -Raw -Encoding UTF8
$tmpl = $tmpl -replace '(?m)^ADMIN_INIT_KEY=.*$', "ADMIN_INIT_KEY=$adminKey"
$tmpl | Set-Content -LiteralPath $envFile -Encoding UTF8
Write-StageLog "[60_seed] wrote .env (ADMIN_INIT_KEY generated)" 'OK'

# 2) start a temporary API to create the first key + import seed
$proc = Start-Process -FilePath $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','45679' `
    -WorkingDirectory $pr -WindowStyle Hidden -PassThru
$ready = $false
for ($i=0; $i -lt 60; $i++) {
    if (Test-PortListening -Port 45679) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:45679/health' -TimeoutSec 3 | Out-Null; $ready=$true; break } catch {}
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; Write-StageLog "[60_seed] temp API did not become healthy" 'ERROR'; exit 1 }

try {
    # create API key
    $body = '{"name":"bootstrap"}'
    $resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:45679/api/v1/admin/apikeys' `
        -Headers @{ 'X-Admin-Init-Key' = $adminKey } -ContentType 'application/json' -Body $body
    $apiKey = $resp.api_key
    if (-not $apiKey) { throw "no api_key in response" }
    Write-StageLog "[60_seed] bootstrap API key created" 'OK'

    # import seed (reference standards) if any
    $seed = Join-Path (Get-OfflineDir) 'seed'
    $jsons = @(Get-ChildItem -LiteralPath $seed -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($jsons.Count -gt 0) {
        Write-StageLog "[60_seed] importing $($jsons.Count) seed standards..." 'INFO'
        & $py (Join-Path $pr 'scripts\import_reference_standards.py') --base 'http://127.0.0.1:45679' --key $apiKey --dir $seed
        if ($LASTEXITCODE -ne 0) { Write-StageLog "[60_seed] seed import reported failures (non-fatal)" 'WARN' }
    } else { Write-StageLog "[60_seed] no seed files -> skip import" 'WARN' }

    # persist state (this file is the ONE place the credentials live)
    [ordered]@{ admin_init_key=$adminKey; api_key=$apiKey; created=(Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    # Lock it down to Administrators/SYSTEM/current user (best-effort).
    if (Protect-SecretFile -Path $stateFile) {
        Write-StageLog "[60_seed] restricted ACL on .install_state.json" 'OK'
    }
    Write-StageLog "" 'INFO'
    Write-StageLog "================ BOOTSTRAP CREDENTIALS ================" 'OK'
    # Fingerprints only: the deploy log is a shareable artifact.
    Write-StageLog (Format-SecretLine -Label 'ADMIN_INIT_KEY' -Secret $adminKey -Source '.install_state.json') 'OK'
    Write-StageLog (Format-SecretLine -Label 'API_KEY' -Secret $apiKey -Source '.install_state.json') 'OK'
    Write-StageLog "The full values are ONLY in: deploy\.install_state.json" 'OK'
    Write-StageLog "=======================================================" 'OK'
}
finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

Write-StageLog "[60_seed] OK" 'OK'
exit 0
