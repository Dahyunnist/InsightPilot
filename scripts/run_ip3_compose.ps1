$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ComposeProject = 'insightpilot-ip3-compose'
$ComposeFile = Join-Path $ProjectRoot 'docker-compose.ip3.yml'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BaseImage = 'python:3.12-slim'
$ComposeStarted = $false
$PushedLocation = $false

function Stop-WithFallback {
    param([string]$Reason)
    throw "$Reason`nIP-3 Compose 测量未运行。功能冒烟回退（不测容器性能）：.\.venv\Scripts\python.exe scripts\smoke_local.py"
}

try {
    Push-Location $ProjectRoot
    $PushedLocation = $true

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Stop-WithFallback '缺少 Docker CLI。'
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        Stop-WithFallback '项目虚拟环境不存在；先按 README 安装依赖。'
    }

    $DockerServer = @(& docker version --format '{{.Server.Version}}' 2>&1)
    if ($LASTEXITCODE -ne 0 -or -not $DockerServer) {
        Stop-WithFallback "Docker Engine 不可访问：$($DockerServer -join ' ')"
    }
    $ComposeVersion = @(& docker compose version --short 2>&1)
    if ($LASTEXITCODE -ne 0 -or -not $ComposeVersion) {
        Stop-WithFallback "缺少可用的 Docker Compose v2：$($ComposeVersion -join ' ')"
    }

    & docker compose --project-name $ComposeProject -f $ComposeFile config --quiet
    if ($LASTEXITCODE -ne 0) {
        Stop-WithFallback 'IP-3 Compose 配置无效。'
    }
    $Existing = @(& docker compose --project-name $ComposeProject -f $ComposeFile ps -a -q 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Stop-WithFallback "无法检查 IP-3 Compose 服务：$($Existing -join ' ')"
    }
    if ($Existing) {
        Stop-WithFallback '当前 Compose 项目已有容器；为避免影响现有容器，本次停止。'
    }

    $LocalBaseImage = @(& docker image inspect $BaseImage --format '{{.Id}}' 2>&1)
    if ($LASTEXITCODE -eq 0 -and $LocalBaseImage) {
        & docker compose --project-name $ComposeProject -f $ComposeFile build --pull=false api-probe
    } else {
        & docker compose --project-name $ComposeProject -f $ComposeFile build --pull api-probe
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'IP-3 镜像 pull/build 失败。检查 Docker Hub 与包索引网络后可安全重跑。'
    }

    $ComposeStarted = $true
    & docker compose --project-name $ComposeProject -f $ComposeFile up -d --wait --pull never
    if ($LASTEXITCODE -ne 0) {
        throw "IP-3 Compose 启动失败（退出码 $LASTEXITCODE）。检查 Docker 构建输出和包索引网络。"
    }
    $SmokeReport = Join-Path $ProjectRoot ('reports\ip3-compose-http-' + (Get-Date -Format 'yyyyMMdd') + '.json')
    & $Python scripts\probe_ip3_http.py --output $SmokeReport
    if ($LASTEXITCODE -ne 0) {
        throw "IP-3 HTTP 验收失败（退出码 $LASTEXITCODE）。"
    }
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 2
}
finally {
    if ($ComposeStarted) {
        & docker compose --project-name $ComposeProject -f $ComposeFile down
        if ($LASTEXITCODE -ne 0) { Write-Warning '无法清理 IP-3 Compose 容器。' }
    }
    if ($PushedLocation) { Pop-Location }
}
