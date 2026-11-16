from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


class IP4V13PostgresParityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.path = self.root / "scripts/run_ip4_v13_postgres_parity.ps1"
        self.script = self.path.read_text(encoding="utf-8")

    def test_requires_explicit_confirmation_and_pins_frozen_inputs(self):
        self.assertIn("[switch]$Execute", self.script)
        self.assertIn("[switch]$ConfirmDedicatedDatabase", self.script)
        self.assertIn("if (-not $Execute)", self.script)
        self.assertIn("if (-not $ConfirmDedicatedDatabase)", self.script)
        self.assertIn("bundle.source_v13.manifest_sha256", self.script)
        self.assertIn("bundle.source_v13.blind_sha256", self.script)
        self.assertIn("bundle.source_v13.gold_sha256", self.script)
        self.assertIn("$bundle.data.sqlite_sha256 -ne $dbHash", self.script)
        self.assertIn("$source.case_count -ne 22", self.script)
        self.assertIn("$Bootstrap = Join-Path $ProjectRoot 'docs\\postgresql-ip4-v13-bootstrap.sql'", self.script)
        self.assertIn("'--bootstrap', $Bootstrap", self.script)

    def test_compose_is_random_loopback_tmpfs_and_cleanup_is_project_scoped(self):
        self.assertIn("$ComposeProject = 'insightpilot-v13-' + $RunId.Substring(0, 12)", self.script)
        self.assertIn("127.0.0.1:55432:5432", self.script)
        self.assertIn("127.0.0.1::5432", self.script)
        self.assertIn("tmpfs", self.script)
        self.assertIn("$ComposeOwned = $true", self.script)
        self.assertIn("finally {", self.script)
        self.assertIn("$ComposeProject, '-f', $TempCompose, 'down', '--remove-orphans'", self.script)
        self.assertNotIn("down -v", self.script.lower())
        self.assertNotIn("volume rm", self.script.lower())
        self.assertNotIn("docker rm", self.script.lower())
        self.assertIn("--pull', 'never'", self.script)
        self.assertIn("--wait-timeout', '60'", self.script)
        self.assertIn("90000 'compose-up'", self.script)
        self.assertIn("30000 'compose-cleanup'", self.script)
        self.assertLess(self.script.index("$ComposeOwned = $true"), self.script.index("'up', '-d'"))
        self.assertIn("foreach ($name in $EnvironmentNames)", self.script)
        self.assertIn("'COMPOSE_DISABLE_ENV_FILE'", self.script)
        self.assertIn("SetEnvironmentVariable('COMPOSE_DISABLE_ENV_FILE', 'true', 'Process')", self.script)
        self.assertIn("if (Test-Path -LiteralPath $ReportPath) { throw 'report_path_already_exists' }", self.script)
        self.assertIn("if ($ReportGenerated -and (Test-Path -LiteralPath $ReportPath))", self.script)

    def test_report_path_uses_only_ids_categories_and_status(self):
        self.assertIn("$summary.postgres_parity_status = $report.database_parity.status", self.script)
        self.assertIn("$summary.tenants[$tenant.Name]", self.script)
        self.assertIn("failures = @($tenant.Value.failures)", self.script)
        self.assertNotIn("$summary.frozen_hashes", self.script)
        self.assertNotIn("$summary.sqlite_ruleplanner", self.script)
        self.assertNotIn("Write-Output $adminDsn", self.script)
        self.assertNotIn("Write-Output $appDsn", self.script)
        self.assertNotIn("Write-Output $composeText", self.script)
        self.assertIn("$report.model_requests_sent -ne 0", self.script)
        self.assertIn("'--run-id', $RunId", self.script)
        self.assertIn("env_file_autoload_disabled = $true", self.script)
        self.assertIn("remaining_project_containers = 0", self.script)
        self.assertIn("remaining_project_networks = 0", self.script)

    def test_script_parses_with_windows_powershell(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell)
        env = os.environ.copy()
        env["IP4_PARITY_SCRIPT_PATH"] = str(self.path)
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:IP4_PARITY_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
            "Write-Output IP4_PARITY_PS_PARSE_OK"
        )
        result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP4_PARITY_PS_PARSE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
