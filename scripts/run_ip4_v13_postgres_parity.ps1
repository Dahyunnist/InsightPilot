param(
    [switch]$Execute,
    [switch]$ConfirmDedicatedDatabase,
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RunId = [guid]::NewGuid().ToString('N')
$ComposeProject = 'insightpilot-v13-' + $RunId.Substring(0, 12)
$ComposeSource = Join-Path $ProjectRoot 'docker-compose.ip4.yml'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Database = Join-Path $ProjectRoot 'data\insightpilot-ip4-v13.sqlite'
$Bootstrap = Join-Path $ProjectRoot 'docs\postgresql-ip4-v13-bootstrap.sql'
$BundleManifestPath = Join-Path $ProjectRoot 'benchmarks\ip4-offline-prep-v1\manifest.json'
$V13ManifestPath = Join-Path $ProjectRoot 'evals\ip4-v13-manifest.json'
$V13BlindPath = Join-Path $ProjectRoot 'evals\ip4-v13-blind.jsonl'
$V13GoldPath = Join-Path $ProjectRoot 'evals\ip4-v13-gold.jsonl'
$ReportPath = if ($ReportPath) { [IO.Path]::GetFullPath($ReportPath) } else {
    Join-Path $ProjectRoot ('reports\ip4-v13-postgres-parity-' + $RunId + '.json')
}
$TempRoot = Join-Path $env:TEMP ('insightpilot-ip4-v13-parity-' + $RunId)
$TempCompose = Join-Path $TempRoot 'compose.yml'
$Stage = 'confirmation'
$Status = 'dry_run'
$CleanupStatus = 'not_started'
$FailureClass = $null
$ComposeOwned = $false
$ReportGenerated = $false
$EnvironmentNames = @('INSIGHTPILOT_IP4_ADMIN_PASSWORD', 'INSIGHTPILOT_IP4_ADMIN_DSN',
    'INSIGHTPILOT_IP4_APP_DSN', 'INSIGHTPILOT_IP4_APP_ROLE', 'INSIGHTPILOT_IP4_APP_PASSWORD',
    'COMPOSE_DISABLE_ENV_FILE')
$PreviousEnvironment = @{}
foreach ($name in $EnvironmentNames) {
    $PreviousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Quote-Argument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutMs,
        [string]$Name,
        [switch]$CaptureStdout
    )
    $stdoutPath = Join-Path $TempRoot ($Name + '.stdout.raw')
    $stderrPath = Join-Path $TempRoot ($Name + '.stderr.raw')
    $argumentLine = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $process = Start-Process -FilePath $FilePath -ArgumentList $argumentLine -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $finished = $process.WaitForExit($TimeoutMs)
    if (-not $finished) {
        try { $process.Kill(); $process.WaitForExit(5000) | Out-Null } catch { }
    }
    $stdout = if ($CaptureStdout -and (Test-Path -LiteralPath $stdoutPath)) {
        [IO.File]::ReadAllText($stdoutPath).Trim()
    } else { '' }
    $stderrClass = $null
    if (Test-Path -LiteralPath $stderrPath) {
        $stderrText = [IO.File]::ReadAllText($stderrPath)
        foreach ($knownClass in @('UndefinedTable', 'UndefinedColumn', 'InsufficientPrivilege',
                                  'InvalidSchemaName', 'DuplicateObject', 'OperationalError',
                                  'ConnectionTimeout', 'TimeoutError', 'ValueError', 'RuntimeError',
                                  'TypeError', 'KeyError', 'AssertionError', 'AttributeError',
                                  'FileNotFoundError', 'ModuleNotFoundError', 'PermissionError')) {
            if ($stderrText.Contains($knownClass)) { $stderrClass = $knownClass; break }
        }
    }
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ finished = $finished; exit_code = if ($finished) { $process.ExitCode } else { $null };
        stdout = $stdout; error_class = $stderrClass }
}

function Get-SafeSummary {
    param([string]$RunStatus, [string]$RunStage)
    $summary = [ordered]@{
        status = $RunStatus
        stage = $RunStage
        model_calls = 0
        cleanup = $CleanupStatus
    }
    if ($FailureClass) { $summary.failure_class = $FailureClass }
    if ($ReportGenerated -and (Test-Path -LiteralPath $ReportPath)) {
        try {
            $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
            $summary.case_count = $report.case_count
            $summary.postgres_parity_status = $report.database_parity.status
            $summary.tenants = [ordered]@{}
            foreach ($tenant in $report.database_parity.tenants.PSObject.Properties) {
                $summary.tenants[$tenant.Name] = [ordered]@{
                    passed = $tenant.Value.passed
                    total = $tenant.Value.total
                    failures = @($tenant.Value.failures)
                }
            }
        } catch { }
    }
    return $summary
}

if (-not $Execute) {
    Get-SafeSummary 'dry_run_requires_confirmation' $Stage | ConvertTo-Json -Depth 10
    return
}
if (-not $ConfirmDedicatedDatabase) {
    Get-SafeSummary 'blocked_confirmation_required' $Stage | ConvertTo-Json -Depth 10
    return
}

$Status = 'blocked'
try {
    [Environment]::SetEnvironmentVariable('COMPOSE_DISABLE_ENV_FILE', 'true', 'Process')
    $null = New-Item -ItemType Directory -Path $TempRoot -Force
    if (Test-Path -LiteralPath $ReportPath) { throw 'report_path_already_exists' }
    $Stage = 'frozen_artifacts'
    foreach ($path in @($ComposeSource, $Python, $Database, $Bootstrap, $BundleManifestPath,
                        $V13ManifestPath, $V13BlindPath, $V13GoldPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'required_artifact_missing' }
    }
    $bundle = Get-Content -LiteralPath $BundleManifestPath -Raw | ConvertFrom-Json
    $source = Get-Content -LiteralPath $V13ManifestPath -Raw | ConvertFrom-Json
    $hashes = @{
        manifest_sha256 = (Get-FileHash -LiteralPath $V13ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        blind_sha256 = (Get-FileHash -LiteralPath $V13BlindPath -Algorithm SHA256).Hash.ToLowerInvariant()
        gold_sha256 = (Get-FileHash -LiteralPath $V13GoldPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $dbHash = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($bundle.source_v13.manifest_sha256 -ne $hashes.manifest_sha256 -or
        $bundle.source_v13.blind_sha256 -ne $hashes.blind_sha256 -or
        $bundle.source_v13.gold_sha256 -ne $hashes.gold_sha256 -or
        $source.case_count -ne 22 -or $bundle.case_count -ne 22 -or
        $bundle.data.sqlite_sha256 -ne $dbHash) {
        throw 'frozen_hash_or_case_count_mismatch'
    }

    $Stage = 'local_dependencies'
    $pythonCheck = Invoke-BoundedProcess $Python @('-c', 'import psycopg, psycopg_pool, sqlglot') 20000 'python-check'
    if (-not $pythonCheck.finished -or $pythonCheck.exit_code -ne 0) { throw 'project_dependencies_unavailable' }
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { throw 'docker_cli_unavailable' }
    $dockerInfo = Invoke-BoundedProcess $docker.Source @('info', '--format', '{{.ServerVersion}}') 15000 'docker-info' -CaptureStdout
    if (-not $dockerInfo.finished) { throw 'docker_preflight_timeout' }
    if ($dockerInfo.exit_code -ne 0 -or -not $dockerInfo.stdout) { throw 'docker_engine_unavailable' }
    $image = Invoke-BoundedProcess $docker.Source @('image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}') 15000 'image-check' -CaptureStdout
    if (-not $image.finished -or $image.exit_code -ne 0 -or -not $image.stdout) { throw 'postgres_image_not_cached_no_pull_performed' }

    $Stage = 'isolated_compose'
    $null = New-Item -ItemType Directory -Path $TempRoot -Force
    $composeText = [IO.File]::ReadAllText($ComposeSource)
    $composeText = [regex]::Replace($composeText, '(?m)^\s*container_name\s*:\s*[^\r\n]*\r?\n', '')
    $composeText = $composeText.Replace('127.0.0.1:55432:5432', '127.0.0.1::5432')
    [IO.File]::WriteAllText($TempCompose, $composeText, [Text.UTF8Encoding]::new($true))
    if ($composeText -match '(?m)^\s*container_name\s*:' -or
        $composeText -notmatch '127\.0\.0\.1::5432' -or
        $composeText -notmatch '(?m)^\s*tmpfs\s*:') { throw 'isolated_compose_safety_check_failed' }
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $ReportPath) -Force

    $adminPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    [Environment]::SetEnvironmentVariable('INSIGHTPILOT_IP4_ADMIN_PASSWORD', $adminPassword, 'Process')
    $composeArgs = @('compose', '--project-name', $ComposeProject, '-f', $TempCompose)
    $config = Invoke-BoundedProcess $docker.Source ($composeArgs + @('config', '--quiet')) 15000 'compose-config'
    if (-not $config.finished -or $config.exit_code -ne 0) { throw 'compose_config_failed' }
    $existing = Invoke-BoundedProcess $docker.Source ($composeArgs + @('ps', '-a', '-q')) 15000 'compose-existing' -CaptureStdout
    if (-not $existing.finished -or $existing.exit_code -ne 0 -or $existing.stdout) { throw 'random_compose_project_not_empty' }

    $Stage = 'temporary_database'
    $appPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $appRole = 'insightpilot_v13_runner'
    [Environment]::SetEnvironmentVariable('INSIGHTPILOT_IP4_APP_ROLE', $appRole, 'Process')
    [Environment]::SetEnvironmentVariable('INSIGHTPILOT_IP4_APP_PASSWORD', $appPassword, 'Process')
    $ComposeOwned = $true
    $up = Invoke-BoundedProcess $docker.Source ($composeArgs + @('up', '-d', '--wait', '--wait-timeout', '60', '--pull', 'never', 'postgres')) 90000 'compose-up'
    if (-not $up.finished -or $up.exit_code -ne 0) { throw 'temporary_postgres_start_failed' }

    $portResult = Invoke-BoundedProcess $docker.Source ($composeArgs + @('port', 'postgres', '5432')) 15000 'compose-port' -CaptureStdout
    if (-not $portResult.finished -or $portResult.exit_code -ne 0 -or $portResult.stdout -notmatch ':(\d+)$') {
        throw 'temporary_postgres_port_unavailable'
    }
    $port = $Matches[1]
    $adminDsn = "postgresql://postgres:$adminPassword@127.0.0.1:$port/insightpilot_ip4"
    $appDsn = "postgresql://${appRole}:$appPassword@127.0.0.1:$port/insightpilot_ip4"
    [Environment]::SetEnvironmentVariable('INSIGHTPILOT_IP4_ADMIN_DSN', $adminDsn, 'Process')
    [Environment]::SetEnvironmentVariable('INSIGHTPILOT_IP4_APP_DSN', $appDsn, 'Process')

    $Stage = 'load_synthetic_fixture'
    $loader = Invoke-BoundedProcess $Python @('scripts/prepare_ip4_postgres.py', '--database', $Database,
        '--bootstrap', $Bootstrap) 180000 'fixture-load'
    if (-not $loader.finished -or $loader.exit_code -ne 0) {
        $FailureClass = $loader.error_class
        throw 'synthetic_fixture_load_failed'
    }

    $Stage = 'sqlite_postgresql_parity'
    $audit = Invoke-BoundedProcess $Python @('scripts/run_ip4_v13_deterministic_comparison.py',
        '--database', $Database, '--output', $ReportPath, '--run-id', $RunId) 180000 'parity-audit'
    if (-not $audit.finished -or $audit.exit_code -ne 0 -or -not (Test-Path -LiteralPath $ReportPath)) {
        $FailureClass = $audit.error_class
        throw 'offline_parity_audit_failed'
    }
    $ReportGenerated = $true
    $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
    if ($report.status -ne 'completed' -or $report.deterministic_parity_status -ne 'passed' -or
        $report.model_calls -ne 0 -or $report.model_requests_sent -ne 0 -or
        $report.database_parity.status -ne 'passed') { throw 'sqlite_postgresql_parity_failed' }
    $Status = 'passed'
} catch {
    $Status = if ($_.Exception.Message -match '^(docker_|postgres_image|project_dependencies|frozen_|isolated_compose|compose_|report_path_)') {
        'blocked'
    } else { 'failed' }
} finally {
    if ($ComposeOwned) {
        $docker = Get-Command docker -ErrorAction SilentlyContinue
        if ($docker -and (Test-Path -LiteralPath $TempCompose)) {
            $downArgs = @('compose', '--project-name', $ComposeProject, '-f', $TempCompose, 'down', '--remove-orphans')
            $cleanup = Invoke-BoundedProcess $docker.Source $downArgs 30000 'compose-cleanup'
            if ($cleanup.finished -and $cleanup.exit_code -eq 0) {
                $leftContainers = Invoke-BoundedProcess $docker.Source @('ps', '-a', '--filter',
                    "label=com.docker.compose.project=$ComposeProject", '--format', '{{.ID}}') 15000 'container-cleanup-check' -CaptureStdout
                $leftNetworks = Invoke-BoundedProcess $docker.Source @('network', 'ls', '--filter',
                    "label=com.docker.compose.project=$ComposeProject", '--format', '{{.ID}}') 15000 'network-cleanup-check' -CaptureStdout
                $CleanupStatus = if ($leftContainers.finished -and $leftContainers.exit_code -eq 0 -and
                    -not $leftContainers.stdout -and $leftNetworks.finished -and $leftNetworks.exit_code -eq 0 -and
                    -not $leftNetworks.stdout) { 'passed_project_only' } else { 'blocked_project_cleanup' }
            } else { $CleanupStatus = 'blocked_project_cleanup' }
        } else {
            $CleanupStatus = 'blocked_compose_file_missing'
        }
    } else {
        $CleanupStatus = 'not_required'
    }
    foreach ($name in $EnvironmentNames) {
        [Environment]::SetEnvironmentVariable($name, $PreviousEnvironment[$name], 'Process')
    }
    if ($ReportGenerated -and $CleanupStatus -eq 'passed_project_only' -and
        (Test-Path -LiteralPath $ReportPath)) {
        $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
        $report | Add-Member -NotePropertyName execution_environment -NotePropertyValue ([ordered]@{
            run_id = $RunId
            compose_project = $ComposeProject
            env_file_autoload_disabled = $true
            host_binding = '127.0.0.1'
            postgres_storage = 'tmpfs'
            host_data_volume = $false
            project_cleanup = 'passed_project_only'
            remaining_project_containers = 0
            remaining_project_networks = 0
        }) -Force
        [IO.File]::WriteAllText($ReportPath, ($report | ConvertTo-Json -Depth 20) + "`n",
            [Text.UTF8Encoding]::new($false))
    }
    if ($CleanupStatus -eq 'passed_project_only' -and (Test-Path -LiteralPath $TempCompose)) {
        Remove-Item -LiteralPath $TempCompose -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $TempRoot -Force -ErrorAction SilentlyContinue
    } elseif ($CleanupStatus -eq 'not_required' -and (Test-Path -LiteralPath $TempRoot)) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$safeSummary = Get-SafeSummary $Status $Stage
if ($CleanupStatus -ne 'passed_project_only' -and $CleanupStatus -ne 'not_required') {
    $safeSummary.status = 'cleanup_blocked'
}
$safeSummary | ConvertTo-Json -Depth 12
if ($safeSummary.status -ne 'passed') { exit 1 }
