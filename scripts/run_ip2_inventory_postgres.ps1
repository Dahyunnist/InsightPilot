param([switch]$Execute)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compose = Join-Path $root 'docker-compose.ip2-inventory.yml'
$runId = [guid]::NewGuid().ToString('N').Substring(0, 10)
$composeProject = "insightpilot-ip2-inventory-$runId"
$containerName = "insightpilot-ip2-inventory-$runId"
$databaseName = "insightpilot_ip2_inventory_$runId"
$envNames = @('INSIGHTPILOT_IP2_INVENTORY_CONTAINER','INSIGHTPILOT_IP2_INVENTORY_DATABASE',
    'INSIGHTPILOT_IP2_INVENTORY_ADMIN_PASSWORD','INSIGHTPILOT_IP2_INVENTORY_ADMIN_DSN',
    'INSIGHTPILOT_IP2_INVENTORY_APP_ROLE','INSIGHTPILOT_IP2_INVENTORY_APP_PASSWORD',
    'INSIGHTPILOT_IP2_INVENTORY_APP_DSN','INSIGHTPILOT_RUN_IP2_INVENTORY_POSTGRES_INTEGRATION',
    'INSIGHTPILOT_IP4_ADMIN_DSN','INSIGHTPILOT_IP4_APP_DSN','INSIGHTPILOT_IP4_APP_ROLE',
    'INSIGHTPILOT_RLS_EXPECTED_DATABASE','COMPOSE_DISABLE_ENV_FILE')
$oldValues = @{}
foreach ($name in $envNames) { $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = $false
$validationPassed = $false
$canaryReport = Join-Path $root "reports\ip2-inventory-postgres-canary-$runId.json"
$summaryReport = Join-Path $root "reports\ip2-inventory-postgres-acceptance-$runId.json"
try {
    Push-Location $root
    $env:COMPOSE_DISABLE_ENV_FILE = 'true'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required.' }
    $env:INSIGHTPILOT_IP2_INVENTORY_CONTAINER = $containerName
    $env:INSIGHTPILOT_IP2_INVENTORY_DATABASE = $databaseName
    $env:INSIGHTPILOT_IP2_INVENTORY_ADMIN_PASSWORD = [guid]::NewGuid().ToString('N')
    & docker compose --project-name $composeProject -f $compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid.' }
    $config = @(& docker compose --project-name $composeProject -f $compose config --format json)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect rendered Compose configuration.' }
    $configObject = ($config -join "`n") | ConvertFrom-Json
    if ($configObject.services.postgres.volumes -or
        $configObject.services.postgres.tmpfs -notcontains '/var/lib/postgresql/data' -or
        $configObject.services.postgres.ports[0].host_ip -ne '127.0.0.1') {
        throw 'Refusing Compose target without loopback-only port and tmpfs-only database storage.'
    }
    if (-not $Execute) {
        Write-Output 'DRY RUN OK: random project, random database, loopback port, tmpfs storage.'
        return
    }
    & docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon unavailable.' }
    $existing = @(& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format '{{.ID}}')
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'Cannot prove this random Compose project is unused.' }
    $network = @(& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format '{{.ID}}')
    if ($LASTEXITCODE -ne 0 -or $network) { throw 'Cannot prove this random Compose network is unused.' }

    $env:INSIGHTPILOT_IP2_INVENTORY_APP_ROLE = 'ip2_inventory_reader'
    $env:INSIGHTPILOT_IP2_INVENTORY_APP_PASSWORD = [guid]::NewGuid().ToString('N')
    $env:INSIGHTPILOT_RUN_IP2_INVENTORY_POSTGRES_INTEGRATION = '1'
    $started = $true
    & docker compose --project-name $composeProject -f $compose up -d --wait --wait-timeout 60 postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL service start failed.' }
    $published = @(& docker compose --project-name $composeProject -f $compose port postgres 5432)
    if ($LASTEXITCODE -ne 0 -or $published.Count -ne 1 -or $published[0] -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw 'Cannot prove the temporary PostgreSQL port is loopback-only.'
    }
    $port = $Matches[1]
    $env:INSIGHTPILOT_IP2_INVENTORY_ADMIN_DSN = "postgresql://postgres:$($env:INSIGHTPILOT_IP2_INVENTORY_ADMIN_PASSWORD)@127.0.0.1:$port/$databaseName"
    $env:INSIGHTPILOT_IP2_INVENTORY_APP_DSN = "postgresql://$($env:INSIGHTPILOT_IP2_INVENTORY_APP_ROLE):$($env:INSIGHTPILOT_IP2_INVENTORY_APP_PASSWORD)@127.0.0.1:$port/$databaseName"
    $env:INSIGHTPILOT_IP4_ADMIN_DSN = $env:INSIGHTPILOT_IP2_INVENTORY_ADMIN_DSN
    $env:INSIGHTPILOT_IP4_APP_DSN = $env:INSIGHTPILOT_IP2_INVENTORY_APP_DSN
    $env:INSIGHTPILOT_IP4_APP_ROLE = $env:INSIGHTPILOT_IP2_INVENTORY_APP_ROLE
    $env:INSIGHTPILOT_RLS_EXPECTED_DATABASE = $databaseName
    $python = Join-Path $root '.venv\Scripts\python.exe'
    & $python -m pytest -p no:cacheprovider tests\test_ip2_inventory_postgres_integration.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Inventory SQLite/PostgreSQL parity or tenant RLS canary failed.' }
    & $python scripts\verify_ip1_rls_canary.py --output $canaryReport
    if ($LASTEXITCODE -ne 0) { throw 'Two-tenant RLS canary failed.' }
    $validationPassed = $true
    Write-Output 'IP2 INVENTORY POSTGRES PARITY AND TWO-TENANT RLS CANARY PASSED; model calls 0.'
} finally {
    if ($started) {
        & docker compose --project-name $composeProject -f $compose down --remove-orphans *> $null
        $downSucceeded = $LASTEXITCODE -eq 0
        $left = @(& docker ps -a --filter "label=com.docker.compose.project=$composeProject" --format '{{.ID}}')
        $containersQuerySucceeded = $LASTEXITCODE -eq 0
        $leftNetworks = @(& docker network ls --filter "label=com.docker.compose.project=$composeProject" --format '{{.ID}}')
        $networksQuerySucceeded = $LASTEXITCODE -eq 0
        if ($left.Count -or $leftNetworks.Count) { Write-Warning 'This run left a container or network; no other project was touched.' }
        $cleanupPassed = $downSucceeded -and $containersQuerySucceeded -and $networksQuerySucceeded -and
            $left.Count -eq 0 -and $leftNetworks.Count -eq 0
        if (-not $downSucceeded) { Write-Warning "Could not remove only this run's Compose container/network." }
        if ($validationPassed -and $cleanupPassed) {
            $canary = Get-Content -LiteralPath $canaryReport -Raw | ConvertFrom-Json
            $summary = [ordered]@{
                status = 'passed'
                completed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
                compose_project = $composeProject
                database = $databaseName
                postgres_version = $canary.postgres_version
                synthetic_data_only = $true
                model_calls = 0
                isolation = [ordered]@{ host_binding = '127.0.0.1'; storage = 'tmpfs'; host_data_volume = $false }
                inventory_sqlite_postgres_parity = [ordered]@{
                    status = 'passed'; tenants = @('tenant-a', 'tenant-b')
                    compared = @('group_value', 'numerator_cents', 'denominator_cents', 'value')
                }
                inventory_rls = [ordered]@{
                    status = 'passed'; missing_tenant_rejected = $true; no_context_visible_rows = 0
                    cross_tenant_rows_visible = 0; reader_dml_denied = $true
                }
                tenant_rls_canary = [ordered]@{
                    status = $canary.status; pool_connection_reused = $canary.pool_connection_reused
                    observations = $canary.observations; report_path = [IO.Path]::GetFileName($canaryReport)
                    report_sha256 = (Get-FileHash -LiteralPath $canaryReport -Algorithm SHA256).Hash.ToLower()
                }
                cleanup = [ordered]@{ status = 'passed'; remaining_containers = 0; remaining_networks = 0 }
            }
            $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryReport -Encoding utf8
            Write-Output "Acceptance report: $([IO.Path]::GetFileName($summaryReport))"
        }
    }
    foreach ($name in $envNames) {
        if ($null -eq $oldValues[$name]) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
        else { [Environment]::SetEnvironmentVariable($name, $oldValues[$name], 'Process') }
    }
    if ((Get-Location).Path -eq $root) { Pop-Location }
}
