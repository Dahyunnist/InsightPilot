$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$artifactDirectory = Join-Path $root ('.tmp\ip1-database-truth-' + $stamp + '-' + $suffix)
$null = New-Item -ItemType Directory -Path $artifactDirectory -Force
$runner = Join-Path $PSScriptRoot 'run_ip1_rls.ps1'
$runnerLog = Join-Path $artifactDirectory 'runner.log'

function Read-Report {
    param([string]$Name)
    $path = Join-Path $artifactDirectory $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'A required PostgreSQL acceptance report is missing.'
    }
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
}

try {
    & $runner -Execute -ConfirmDatabaseCreation -ArtifactDirectory $artifactDirectory *> $runnerLog
    if ($LASTEXITCODE -ne 0) { throw 'The isolated PostgreSQL runner returned a failure.' }

    $canary = Read-Report 'ip1-tenant-rls-canary.json'
    $postgres = Read-Report 'ip1-ip2-postgres-verification.json'
    $timeout = Read-Report 'ip1-data-meta-timeout.json'

    $tenantPositive = @('tenant_a', 'tenant_b', 'tenant_a_reused') | ForEach-Object {
        $canary.observations.$_.visible_count -eq 1 -and
        $canary.observations.$_.visibility_matches_context -and
        $canary.observations.$_.effective_role_is_reader
    }
    $tenantNegative = @('missing', 'unknown') | ForEach-Object {
        $canary.observations.$_.visible_count -eq 0 -and
        $canary.observations.$_.visibility_matches_context
    }
    $tenantQueries = @('tenant-a', 'tenant-b') | ForEach-Object {
        $row = $postgres.tenants.$_
        $row -and $row.passed -eq $row.total -and $row.total -gt 0
    }

    $passed = ($canary.status -eq 'passed' -and
        (@($tenantPositive | Where-Object { -not $_ }).Count -eq 0) -and
        (@($tenantNegative | Where-Object { -not $_ }).Count -eq 0) -and
        $postgres.status -eq 'passed' -and
        $postgres.missing_tenant_context -eq 'rejected' -and
        $postgres.postgres_dml_denied -and $postgres.postgres_ddl_denied -and
        $postgres.statement_timeout -eq 'timed_out' -and
        (@($tenantQueries | Where-Object { -not $_ }).Count -eq 0) -and
        $timeout.status -eq 'passed' -and $timeout.timeout_result -eq 'timed_out' -and
        $timeout.recovery_result -eq 'recovered')
    if (-not $passed) { throw 'PostgreSQL tenant/RLS/timeout acceptance did not meet its gate.' }

    [ordered]@{
        status = 'passed'
        postgresql_connection = 'passed'
        tenant_rls_positive = 'tenant-a, tenant-b, pooled tenant-a: isolated'
        tenant_rls_negative = 'missing and unknown tenant: zero rows'
        read_only = 'DML and DDL rejected'
        data_meta_statement_timeout = 'timed_out'
        identity_recovery = 'recovered'
        model_calls = 0
        artifacts = ('.tmp\' + [IO.Path]::GetFileName($artifactDirectory))
    } | ConvertTo-Json -Depth 4
} catch {
    throw ('IP-1 PostgreSQL acceptance failed. Review local artifacts at .tmp\' +
        [IO.Path]::GetFileName($artifactDirectory) + '.')
}
