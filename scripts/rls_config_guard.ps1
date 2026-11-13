function Test-LocalPostgresPort {
    param([string]$ConfigJson)
    try {
        $config = $ConfigJson | ConvertFrom-Json -ErrorAction Stop
        foreach ($port in $config.services.postgres.ports) {
            if ($port.host_ip -eq '127.0.0.1' -and [string]$port.target -eq '5432' -and
                [string]$port.published -eq '55432') { return $true }
        }
    } catch { return $false }
    return $false
}
