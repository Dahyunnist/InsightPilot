from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_postgres_runner_is_scoped_to_ephemeral_resources():
    runner = (ROOT / "scripts/run_ip2_inventory_postgres.ps1").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.ip2-inventory.yml").read_text(encoding="utf-8")
    loader = (ROOT / "scripts/prepare_ip4_postgres.py").read_text(encoding="utf-8")
    integration = (ROOT / "tests/test_ip2_inventory_postgres_integration.py").read_text(encoding="utf-8")

    assert "insightpilot-ip2-inventory-$runId" in runner
    assert "insightpilot_ip2_inventory_$runId" in runner
    assert "label=com.docker.compose.project=$composeProject" in runner
    assert "down --remove-orphans" in runner
    assert "down -v" not in runner.lower()
    assert "COMPOSE_DISABLE_ENV_FILE" in runner
    assert "127.0.0.1::5432" in compose
    assert "tmpfs:" in compose
    assert "volumes:" not in compose.lower()
    assert "re.fullmatch(r\"insightpilot_ip2_inventory_[a-f0-9]{10}\"" in loader
    assert "INSIGHTPILOT_IP2_INVENTORY_DATABASE" in integration
    assert "INSIGHTPILOT_IP4_ADMIN_DSN" not in integration


def test_inventory_postgres_runner_dry_run_and_powershell_parse():
    host = shutil.which("pwsh") or shutil.which("powershell")
    assert host is not None, "PowerShell host is required"
    dry_run = subprocess.run([host, "-NoProfile", "-File",
                              str(ROOT / "scripts/run_ip2_inventory_postgres.ps1")],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert dry_run.returncode == 0, dry_run.stderr
    assert "random project, random database, loopback port, tmpfs storage" in dry_run.stdout

    script = str(ROOT / "scripts/run_ip2_inventory_postgres.ps1")
    env = {**__import__("os").environ, "IP2_RUNNER_PATH": script}
    command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:IP2_RUNNER_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
        "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
        "Write-Output IP2_RUNNER_PS_PARSE_OK"
    )
    parsed = subprocess.run([host, "-NoProfile", "-Command", command], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=15)
    assert parsed.returncode == 0, parsed.stderr
    assert "IP2_RUNNER_PS_PARSE_OK" in parsed.stdout


def test_inventory_random_database_name_pattern_is_ten_hex_characters():
    assert re.fullmatch("insightpilot_ip2_inventory_[a-f0-9]{10}",
                        "insightpilot_ip2_inventory_0123456789")
    assert not re.fullmatch("insightpilot_ip2_inventory_[a-f0-9]{10}", "insightpilot_ip4")
