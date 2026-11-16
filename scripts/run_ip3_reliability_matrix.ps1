param(
    [switch]$Execute,
    [switch]$ConfirmSyntheticMatrix,
    [string]$ArtifactDirectory
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BaseImage = 'python:3.12-slim'
$Projects = @('insightpilot-ip3-compose', 'insightpilot-ip3-db-outage', 'insightpilot-ip3-restart')
$Ports = @(58080, 55436, 58082, 58081)

if (-not $Execute -or -not $ConfirmSyntheticMatrix) {
    Write-Output '未执行。请同时提供 -Execute -ConfirmSyntheticMatrix 才会启动合成矩阵。'
    exit 0
}

if (-not $ArtifactDirectory) {
    $ArtifactDirectory = Join-Path ([IO.Path]::GetTempPath()) ('ip3-reliability-' + [guid]::NewGuid().ToString('N'))
}
$ArtifactDirectory = [IO.Path]::GetFullPath($ArtifactDirectory)
if (Test-Path -LiteralPath $ArtifactDirectory) {
    throw '产物目录已存在；拒绝覆盖。请提供新的唯一目录。'
}
$null = New-Item -ItemType Directory -Path $ArtifactDirectory
$PreviousFixturePath = $env:IP3_RELIABILITY_FIXTURE_PATH
$env:IP3_RELIABILITY_FIXTURE_PATH = Join-Path $ArtifactDirectory 'fixture.sqlite'
$MatrixStarted = $false
$CleanupChecked = $false

function Assert-PortAvailable([int]$Port) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $listener.Start(); $listener.Stop() }
    catch { throw "本机端口 $Port 已占用；拒绝启动矩阵。" }
}

function Assert-ProjectEmpty([string]$Project) {
    $items = @(& docker compose --project-name $Project ps -a -q 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "无法检查专用 Compose 项目 $Project。" }
    if ($items) { throw "专用 Compose 项目 $Project 已有容器；拒绝触碰。" }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "阶段失败，退出码 $LASTEXITCODE：$File" }
}

function Get-MatrixCleanupReport {
    $projectResults = @()
    foreach ($project in $Projects) {
        $containerOutput = @(& docker compose --project-name $project ps -a -q 2>&1)
        $containerQueryPassed = $LASTEXITCODE -eq 0
        $containerIds = @($containerOutput | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })

        $networkOutput = @(& docker network ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}' 2>&1)
        $networkQueryPassed = $LASTEXITCODE -eq 0
        $networkNames = @($networkOutput | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })

        $empty = $containerQueryPassed -and $networkQueryPassed -and
            $containerIds.Count -eq 0 -and $networkNames.Count -eq 0
        $projectResults += [ordered]@{
            project = $project
            container_check = if ($containerQueryPassed) { 'passed' } else { 'failed' }
            container_count = if ($containerQueryPassed) { $containerIds.Count } else { $null }
            network_check = if ($networkQueryPassed) { 'passed' } else { 'failed' }
            network_count = if ($networkQueryPassed) { $networkNames.Count } else { $null }
            empty = $empty
        }
    }
    $allEmpty = @($projectResults | Where-Object { -not $_.empty }).Count -eq 0
    return [ordered]@{
        status = if ($allEmpty) { 'passed' } else { 'failed' }
        checked_projects = $projectResults
        policy = 'verify only; do not remove resources outside child runner cleanup'
    }
}

function Save-MatrixCleanupReport([string]$Path) {
    $cleanup = Get-MatrixCleanupReport
    $json = ($cleanup | ConvertTo-Json -Depth 5) + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
    return $cleanup
}

try {
    Push-Location $ProjectRoot
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '找不到 Docker CLI。' }
    if (-not (Test-Path -LiteralPath $Python)) { throw '项目 Python 环境不存在。' }
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon 不可用。' }
    & docker compose version --short | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 不可用。' }
    foreach ($port in $Ports) { Assert-PortAvailable $port }
    foreach ($project in $Projects) { Assert-ProjectEmpty $project }

    $composeFiles = @('docker-compose.ip3.yml', 'docker-compose.ip3-db-outage.yml', 'docker-compose.ip3-restart.yml')
    foreach ($file in $composeFiles) {
        & docker compose --project-name ('ip3-preflight-' + [guid]::NewGuid().ToString('N')) -f $file config --quiet
        if ($LASTEXITCODE -ne 0) { throw "Compose 配置检查失败：$file" }
    }
    & docker image inspect $BaseImage --format '{{.Id}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '本地缺少 python:3.12-slim；入口不会自动拉取镜像。' }
    & docker image inspect 'postgres:16-alpine' --format '{{.Id}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '本地缺少 postgres:16-alpine；入口不会自动拉取镜像。' }

    $httpDir = Join-Path $ArtifactDirectory 'http'
    $postgresReport = Join-Path $ArtifactDirectory 'postgres.json'
    $restartReport = Join-Path $ArtifactDirectory 'restart.json'
    $summaryReport = Join-Path $ArtifactDirectory 'summary.json'
    $cleanupReport = Join-Path $ArtifactDirectory 'cleanup.json'
    $MatrixStarted = $true
    & (Join-Path $ProjectRoot 'scripts\run_ip3_compose.ps1') -ArtifactDirectory $httpDir
    if ($LASTEXITCODE -ne 0) { throw "阶段失败，退出码 $LASTEXITCODE：scripts\run_ip3_compose.ps1" }
    Invoke-Checked -File $Python -Arguments @('scripts\verify_ip3_postgres_service_outage.py', '--output', $postgresReport,
                                                '--fixture', $env:IP3_RELIABILITY_FIXTURE_PATH)
    Invoke-Checked -File $Python -Arguments @('scripts\verify_ip3_process_restart.py', '--output', $restartReport)
    $cleanup = Save-MatrixCleanupReport $cleanupReport
    $CleanupChecked = $true
    Invoke-Checked -File $Python -Arguments @('scripts\assemble_ip3_reliability_matrix.py', '--http-report',
        (Join-Path $httpDir 'ip3-compose-http.json'), '--postgres-report', $postgresReport,
        '--restart-report', $restartReport, '--cleanup-report', $cleanupReport, '--output', $summaryReport)
    if ($cleanup.status -ne 'passed') { throw "矩阵服务清理未通过；检查 $cleanupReport。" }
    Write-Output "矩阵完成：$summaryReport"
}
finally {
    if ($MatrixStarted -and -not $CleanupChecked) {
        try {
            $cleanupReport = Join-Path $ArtifactDirectory 'cleanup.json'
            $cleanup = Save-MatrixCleanupReport $cleanupReport
            if ($cleanup.status -ne 'passed') { Write-Warning "专用 Compose 清理未通过；检查 $cleanupReport。" }
            else { Write-Output "专用 Compose 清理已核验：$cleanupReport" }
        } catch {
            Write-Warning '无法核验专用 Compose 容器和网络清理。'
        }
    }
    if ($null -eq $PreviousFixturePath) { Remove-Item Env:IP3_RELIABILITY_FIXTURE_PATH -ErrorAction SilentlyContinue }
    else { $env:IP3_RELIABILITY_FIXTURE_PATH = $PreviousFixturePath }
    if ((Get-Location).Path -eq $ProjectRoot) { Pop-Location }
}
