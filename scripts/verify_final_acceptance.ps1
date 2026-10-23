$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ComposeFile = Join-Path $ProjectRoot 'docker-compose.ip4.yml'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$TempTag = [guid]::NewGuid().ToString('N')
$TempDiagnostic = Join-Path $env:TEMP "insightpilot-ip4-diagnostic-$TempTag.json"
$TempDomain = Join-Path $env:TEMP "insightpilot-domain-validation-$TempTag.json"
$EnvNames = @(
    'INSIGHTPILOT_IP4_ADMIN_PASSWORD',
    'INSIGHTPILOT_IP4_ADMIN_DSN',
    'INSIGHTPILOT_IP4_APP_ROLE',
    'INSIGHTPILOT_IP4_APP_PASSWORD',
    'INSIGHTPILOT_IP4_APP_DSN'
)
$OldEnv = @{}
foreach ($Name in $EnvNames) { $OldEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process') }
$ComposeAttempted = $false
$PushedLocation = $false

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program $($Arguments -join ' ')" }
}

try {
    Push-Location $ProjectRoot
    $PushedLocation = $true
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Project virtual environment is missing. Create it and install requirements first.' }
    $Existing = & docker ps -a --filter 'name=^insightpilot-ip4-postgres$' --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker containers.' }
    if ($Existing) { throw 'Container insightpilot-ip4-postgres already exists; stop it before acceptance run.' }

    $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = 'local-acceptance-admin-only'
    $env:INSIGHTPILOT_IP4_ADMIN_DSN = 'postgresql://postgres:local-acceptance-admin-only@127.0.0.1:55432/insightpilot_ip4'
    $env:INSIGHTPILOT_IP4_APP_ROLE = 'insightpilot_acceptance_app'
    $env:INSIGHTPILOT_IP4_APP_PASSWORD = 'local-acceptance-app-only'
    $env:INSIGHTPILOT_IP4_APP_DSN = 'postgresql://insightpilot_acceptance_app:local-acceptance-app-only@127.0.0.1:55432/insightpilot_ip4'

    $ComposeAttempted = $true
    Invoke-Checked 'docker' @('compose', '-f', $ComposeFile, 'up', '-d', '--wait')
    Invoke-Checked $Python @('scripts/prepare_ip4_postgres.py')
    Invoke-Checked $Python @('scripts/diagnose_ip4_r01_r02.py', '--output', $TempDiagnostic, '--require-passed')
    Invoke-Checked $Python @('scripts/verify_domain_validation.py', '--output', $TempDomain)
    Invoke-Checked $Python @('scripts/verify_ip4_freeze_seal.py')
    Invoke-Checked $Python @('scripts/verify_release_artifacts.py')
    Write-Output 'Final acceptance passed. No model API calls were made.'
}
finally {
    if ($ComposeAttempted) {
        & docker compose -f $ComposeFile down
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not remove the temporary PostgreSQL container.' }
    }
    Remove-Item -LiteralPath $TempDiagnostic, $TempDomain -Force -ErrorAction SilentlyContinue
    foreach ($Name in $EnvNames) { [Environment]::SetEnvironmentVariable($Name, $OldEnv[$Name], 'Process') }
    if ($PushedLocation) { Pop-Location }
}
