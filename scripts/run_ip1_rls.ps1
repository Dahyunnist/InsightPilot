param(
    [switch]$Execute,
    [switch]$ConfirmDatabaseCreation
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compose = Join-Path $root 'docker-compose.ip4.yml'
$envName = 'INSIGHTPILOT_IP4_ADMIN_PASSWORD'
$oldValue = [Environment]::GetEnvironmentVariable($envName, 'Process')
$started = $false
try {
    Push-Location $root
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required.' }
    if (-not $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD) {
        [Environment]::SetEnvironmentVariable($envName, ([guid]::NewGuid().ToString('N')), 'Process')
    }
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
    & docker compose -f $compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL service start failed.' }
    $started = $true
    & (Join-Path $root '.venv\Scripts\python.exe') scripts\verify_ip4_postgres.py
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL RLS acceptance failed.' }
} finally {
    if ($started) {
        & docker compose -f $compose stop postgres
    }
    [Environment]::SetEnvironmentVariable($envName, $oldValue, 'Process')
    if ((Get-Location).Path -eq $root) { Pop-Location }
}
