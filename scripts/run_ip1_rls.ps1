param(
    [switch]$Execute,
    [switch]$ConfirmDatabaseCreation
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compose = Join-Path $root 'docker-compose.ip4.yml'
$envNames = @('INSIGHTPILOT_IP4_ADMIN_PASSWORD','INSIGHTPILOT_IP4_ADMIN_DSN',
    'INSIGHTPILOT_IP4_APP_ROLE','INSIGHTPILOT_IP4_APP_PASSWORD','INSIGHTPILOT_IP4_APP_DSN')
$oldValues = @{}
foreach ($name in $envNames) { $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = $false
try {
    Push-Location $root
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required.' }
    if (-not $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD) { $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = [guid]::NewGuid().ToString('N') }
    & docker compose -f $compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid.' }
    $rendered = @(& docker compose -f $compose config)
    if ($LASTEXITCODE -ne 0 -or ($rendered -join "`n") -notmatch '127\.0\.0\.1:55432:5432') {
        throw 'Refusing non-local PostgreSQL target.'
    }
    if (-not $Execute) {
        Write-Output 'DRY RUN OK: local synthetic PostgreSQL only; no service started.'
        return
    }
    if (-not $ConfirmDatabaseCreation) {
        throw 'Execution requires -ConfirmDatabaseCreation.'
    }
    & docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon unavailable. Start Docker Desktop, then rerun scripts\run_ip1_rls.ps1.' }
    $existing = @(& docker ps -a --filter 'name=^insightpilot-ip4-postgres$' --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker containers.' }
    if ($existing) { throw 'Container name conflict; refusing to touch existing service.' }
    $password = [guid]::NewGuid().ToString('N')
    $appPassword = [guid]::NewGuid().ToString('N')
    $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = $password
    $env:INSIGHTPILOT_IP4_ADMIN_DSN = "postgresql://postgres:$password@127.0.0.1:55432/insightpilot_ip4"
    $env:INSIGHTPILOT_IP4_APP_ROLE = 'insightpilot_ip1_acceptance'
    $env:INSIGHTPILOT_IP4_APP_PASSWORD = $appPassword
    $env:INSIGHTPILOT_IP4_APP_DSN = "postgresql://insightpilot_ip1_acceptance:$appPassword@127.0.0.1:55432/insightpilot_ip4"
    $started = $true
    & docker compose -f $compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL service start failed.' }
    & (Join-Path $root '.venv\Scripts\python.exe') scripts\prepare_ip4_postgres.py
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic PostgreSQL preparation failed.' }
    & (Join-Path $root '.venv\Scripts\python.exe') scripts\verify_ip4_postgres.py
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL RLS acceptance failed.' }
} finally {
    if ($started) {
        try {
            & docker compose -f $compose stop postgres *> $null
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not stop the test PostgreSQL service; original result is preserved.' }
        } catch {
            Write-Warning 'Could not stop the test PostgreSQL service; original result is preserved.'
        }
    }
    foreach ($name in $envNames) {
        if ($null -eq $oldValues[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $oldValues[$name], 'Process')
        }
    }
    if ((Get-Location).Path -eq $root) { Pop-Location }
}
