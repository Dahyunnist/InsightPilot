$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\rls_config_guard.ps1')
$valid = '{"services":{"postgres":{"ports":[{"host_ip":"127.0.0.1","target":5432,"published":"55432"}]}}}'
$remote = '{"services":{"postgres":{"ports":[{"host_ip":"0.0.0.0","target":5432,"published":"55432"}]}}}'
$wrongPort = '{"services":{"postgres":{"ports":[{"host_ip":"127.0.0.1","target":5432,"published":"5432"}]}}}'
if (-not (Test-LocalPostgresPort $valid)) { throw 'valid local port rejected' }
if (Test-LocalPostgresPort $remote) { throw 'remote binding accepted' }
if (Test-LocalPostgresPort $wrongPort) { throw 'wrong port accepted' }
if (Test-LocalPostgresPort '{invalid') { throw 'invalid JSON accepted' }
'RLS_CONFIG_GUARD_OK'
