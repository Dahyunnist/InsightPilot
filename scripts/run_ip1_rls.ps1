param(
    [switch]$Execute,
    [switch]$ConfirmDatabaseCreation,
    [string]$ArtifactDirectory
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compose = Join-Path $root 'docker-compose.ip4.yml'
$composeProject = 'insightpilot-ip1-host-acceptance'
$artifactRoot = if ($ArtifactDirectory) { $ArtifactDirectory } else { Join-Path $root 'reports' }
$null = New-Item -ItemType Directory -Path $artifactRoot -Force
$envNames = @('INSIGHTPILOT_IP4_ADMIN_PASSWORD','INSIGHTPILOT_IP4_ADMIN_DSN',
    'INSIGHTPILOT_IP4_APP_ROLE','INSIGHTPILOT_IP4_APP_PASSWORD','INSIGHTPILOT_IP4_APP_DSN')
$oldValues = @{}
foreach ($name in $envNames) { $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = $false
. (Join-Path $PSScriptRoot 'rls_config_guard.ps1')
try {
    Push-Location $root
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required.' }
    if (-not $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD) { $env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = [guid]::NewGuid().ToString('N') }
    & docker compose --project-name $composeProject -f $compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid.' }
    $rendered = @(& docker compose --project-name $composeProject -f $compose config --format json)
    if ($LASTEXITCODE -ne 0 -or -not (Test-LocalPostgresPort ($rendered -join "`n"))) {
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
    & docker compose --project-name $composeProject -f $compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL service start failed.' }
    & (Join-Path $root '.venv\Scripts\python.exe') scripts\prepare_ip4_postgres.py
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic PostgreSQL preparation failed.' }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    $postgresReport = Join-Path $artifactRoot 'ip1-ip2-postgres-verification.json'
    & $python scripts\verify_ip4_postgres.py --output $postgresReport
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL RLS acceptance failed.' }
    $identityTimeoutReport = Join-Path $artifactRoot 'ip1-data-meta-timeout.json'
    & $python scripts\verify_ip1_data_meta_timeout.py --output $identityTimeoutReport
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL data_meta statement-timeout acceptance failed.' }
} finally {
    if ($started) {
        try {
            & docker compose --project-name $composeProject -f $compose down --remove-orphans *> $null
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not remove the dedicated test PostgreSQL container/network; original result is preserved.' }
        } catch {
            Write-Warning 'Could not remove the dedicated test PostgreSQL container/network; original result is preserved.'
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
