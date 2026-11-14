param([string]$ArtifactDirectory)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$ArtifactRoot = if ($ArtifactDirectory) {
    [IO.Path]::GetFullPath($ArtifactDirectory)
} else {
    Join-Path $env:TEMP ('insightpilot-host-ip1-ip3-' + $RunId)
}
$null = New-Item -ItemType Directory -Path $ArtifactRoot -Force
$SummaryPath = Join-Path $ArtifactRoot 'summary.json'
$LogPath = Join-Path $ArtifactRoot 'host-validation.log'
$RunId | Set-Content -LiteralPath $LogPath -Encoding UTF8
$PowerShellHost = (Get-Process -Id $PID).Path
if (-not $PowerShellHost) { $PowerShellHost = (Get-Command powershell.exe -ErrorAction Stop).Source }
$Summary = [ordered]@{
    status = 'not_started'
    docker = 'not_checked'
    model_calls = 0
    ip1 = 'not_started'
    ip3 = 'not_started'
    artifacts = $ArtifactRoot
}

function Write-Summary {
    $Summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
}

function Redact-Text {
    param([string]$Text)
    $safe = $Text -replace '(?i)(postgres(?:ql)?://)[^/\s:@]+:[^@\s]+@', '$1[REDACTED]@'
    $safe = $safe -replace '(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+', '$1[REDACTED]'
    $lines = foreach ($line in ($safe -split "`r?`n")) {
        if ($line -match '(?i)INSIGHTPILOT_IP4_(ADMIN|APP)_(PASSWORD|DSN)') {
            '[REDACTED credential-related output]'
        } else {
            $line
        }
    }
    return ($lines -join [Environment]::NewLine)
}

function Invoke-StageProcess {
    param(
        [string]$Name,
        [string]$ScriptPath,
        [string[]]$ScriptArguments
    )
    $rawOut = Join-Path $ArtifactRoot ($Name + '.stdout.raw')
    $rawErr = Join-Path $ArtifactRoot ($Name + '.stderr.raw')
    $safeLog = Join-Path $ArtifactRoot ($Name + '.log')
    $quotedScript = '"' + $ScriptPath + '"'
    $quotedArguments = @($ScriptArguments | ForEach-Object {
        if ($_ -match '\s') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
    })
    $argumentLine = '-NoProfile -ExecutionPolicy Bypass -File ' + $quotedScript
    if ($quotedArguments.Count) { $argumentLine += ' ' + ($quotedArguments -join ' ') }
    $process = Start-Process -FilePath $PowerShellHost -ArgumentList $argumentLine `
        -WorkingDirectory $ProjectRoot -PassThru -Wait -WindowStyle Hidden `
        -RedirectStandardOutput $rawOut -RedirectStandardError $rawErr
    $rawText = ''
    if (Test-Path -LiteralPath $rawOut) { $rawText += [IO.File]::ReadAllText($rawOut) }
    if (Test-Path -LiteralPath $rawErr) { $rawText += [Environment]::NewLine + [IO.File]::ReadAllText($rawErr) }
    (Redact-Text $rawText) | Set-Content -LiteralPath $safeLog -Encoding UTF8
    Add-Content -LiteralPath $LogPath -Value ("{0}: exit_code={1}; sanitized_log={2}" -f $Name, $process.ExitCode, $safeLog) -Encoding UTF8
    Remove-Item -LiteralPath $rawOut, $rawErr -Force -ErrorAction SilentlyContinue
    return $process.ExitCode
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $Summary.status = 'blocked_docker_cli_unavailable'
        $Summary.docker = 'unavailable'
        Add-Content -LiteralPath $LogPath -Value 'Docker CLI unavailable; no stage was started.' -Encoding UTF8
        Write-Output 'Docker CLI unavailable; no stage was started.'
        Write-Summary
        exit 2
    }
    $dockerInfo = @(& docker info --format '{{.ServerVersion}}' 2>&1)
    if ($LASTEXITCODE -ne 0 -or -not $dockerInfo) {
        $Summary.status = 'blocked_docker_daemon_unavailable'
        $Summary.docker = 'unavailable'
        $sanitized = Redact-Text ($dockerInfo -join [Environment]::NewLine)
        $sanitized | Set-Content -LiteralPath $LogPath -Encoding UTF8
        Write-Summary
        Write-Output ('Docker daemon unavailable; no container was started. Artifacts: ' + $ArtifactRoot)
        exit 2
    }
    $Summary.docker = 'available'

    $ip1Output = Join-Path $ArtifactRoot 'ip1'
    $null = New-Item -ItemType Directory -Path $ip1Output -Force
    $ip1Args = @('-Execute', '-ConfirmDatabaseCreation', '-ArtifactDirectory', $ip1Output)
    $ip1Code = Invoke-StageProcess 'ip1-rls' (Join-Path $ProjectRoot 'scripts\run_ip1_rls.ps1') $ip1Args
    $Summary.ip1 = if ($ip1Code -eq 0) { 'passed' } else { 'failed' }
    if ($ip1Code -ne 0) { throw 'IP-1 host validation failed; see sanitized ip1-rls.log.' }

    $ip3Output = Join-Path $ArtifactRoot 'ip3'
    $null = New-Item -ItemType Directory -Path $ip3Output -Force
    $ip3Args = @('-ArtifactDirectory', $ip3Output)
    try {
        $ip3Code = Invoke-StageProcess 'ip3-compose' (Join-Path $ProjectRoot 'scripts\run_ip3_compose.ps1') $ip3Args
    } catch {
        $Summary.ip3 = 'failed'
        throw
    }
    $Summary.ip3 = if ($ip3Code -eq 0) { 'passed' } else { 'failed' }
    if ($ip3Code -ne 0) { throw 'IP-3 host validation failed; see sanitized ip3-compose.log.' }

    $Summary.status = 'passed'
    Add-Content -LiteralPath $LogPath -Value 'IP-1 and IP-3 host validations passed.' -Encoding UTF8
    Write-Summary
    Write-Output ('IP-1/IP-3 host validation passed. Artifacts: ' + $ArtifactRoot)
} catch {
    if ($Summary.status -eq 'not_started') { $Summary.status = 'failed' }
    if ($Summary.ip1 -eq 'not_started' -and $Summary.docker -eq 'available') { $Summary.ip1 = 'failed' }
    Add-Content -LiteralPath $LogPath -Value (Redact-Text $_.Exception.Message) -Encoding UTF8
    Write-Summary
    Write-Error -Message ($_.Exception.Message + ' Artifacts: ' + $ArtifactRoot) -ErrorAction Continue
    exit 1
}
