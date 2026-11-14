import unittest
import subprocess
import shutil
import os
from pathlib import Path


class IP1RlsRunnerTests(unittest.TestCase):
    def test_local_port_guard_powershell_cases(self):
        root = Path(__file__).resolve().parents[1]
        host = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(host, "PowerShell host is required")
        result = subprocess.run([host, "-NoProfile", "-File",
                                 str(root / "tests/test_ip1_rls_config_guard.ps1")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RLS_CONFIG_GUARD_OK", result.stdout)

    def test_runner_scopes_cleanup_and_never_removes_volumes(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/run_ip1_rls.ps1").read_text(encoding="utf-8")
        guard = (root / "scripts/rls_config_guard.ps1").read_text(encoding="utf-8")
        sql = (root / "docs/postgresql-rls-bootstrap.sql").read_text(encoding="utf-8").upper()
        compose = (root / "docker-compose.ip4.yml").read_text(encoding="utf-8")
        self.assertIn("docker compose --project-name $composeProject -f $compose down --remove-orphans", script)
        self.assertNotIn("DOWN -V", script.upper())
        self.assertNotIn("VOLUME RM", script.upper())
        self.assertIn("127.0.0.1:55432:5432", compose)
        self.assertIn("tmpfs:", compose)
        self.assertNotIn("docker run", script.lower())
        self.assertNotIn("docker compose down -v", script.lower())
        self.assertNotIn(".env", script.lower())
        self.assertIn("[switch]$Execute", script)
        self.assertIn("[switch]$ConfirmDatabaseCreation", script)
        self.assertIn("if (-not $Execute)", script)
        self.assertIn("Refusing non-local PostgreSQL target", script)
        self.assertIn("config --format json", script)
        self.assertIn("rls_config_guard.ps1", script)
        self.assertIn("$port.host_ip -eq '127.0.0.1'", guard)
        self.assertIn("[string]$port.target -eq '5432'", guard)
        self.assertIn("[string]$port.published -eq '55432'", guard)
        self.assertIn("catch { return $false }", guard)
        self.assertIn("if (-not $ConfirmDatabaseCreation)", script)
        self.assertLess(script.index("if (-not $Execute)"),
                        script.index("docker compose --project-name $composeProject -f $compose up -d postgres"))
        self.assertLess(script.index("if (-not $ConfirmDatabaseCreation)"),
                        script.index("docker compose --project-name $composeProject -f $compose up -d postgres"))
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("CURRENT_SETTING('APP.TENANT_ID'", sql)
        self.assertIn("INSIGHTPILOT_READER", sql)
        self.assertLess(script.index("$started = $true"),
                        script.index("docker compose --project-name $composeProject -f $compose up -d postgres"))
        self.assertLess(script.index("scripts\\prepare_ip4_postgres.py"), script.index("scripts\\verify_ip4_postgres.py"))
        self.assertLess(script.index("scripts\\verify_ip4_postgres.py"),
                        script.index("scripts\\verify_ip1_data_meta_timeout.py"))
        self.assertIn("dedicated test PostgreSQL container/network", script)
        self.assertNotIn("volumes:", compose.lower())
        for name in ("INSIGHTPILOT_IP4_ADMIN_PASSWORD", "INSIGHTPILOT_IP4_ADMIN_DSN",
                     "INSIGHTPILOT_IP4_APP_ROLE", "INSIGHTPILOT_IP4_APP_PASSWORD",
                     "INSIGHTPILOT_IP4_APP_DSN"):
            self.assertIn(name, script)

    def test_data_meta_timeout_gate_locks_table_then_checks_recovery(self):
        root = Path(__file__).resolve().parents[1]
        verifier = (root / "scripts/verify_ip1_data_meta_timeout.py").read_text(encoding="utf-8")
        self.assertIn("LOCK TABLE public.data_meta IN ACCESS EXCLUSIVE MODE", verifier)
        self.assertIn("engine.read_data_identity()", verifier)
        self.assertIn("QueryTimeoutError", verifier)
        self.assertIn("pg_cancel_backend", verifier)
        self.assertIn("recovery_engine.read_data_identity()", verifier)
        self.assertIn('"model_calls": 0', verifier)

    def test_rls_runner_parses_with_powershell(self):
        root = Path(__file__).resolve().parents[1]
        host = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(host, "PowerShell host is required")
        env = os.environ.copy()
        env["IP1_RLS_SCRIPT_PATH"] = str(root / "scripts/run_ip1_rls.ps1")
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:IP1_RLS_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
            "Write-Output IP1_RLS_PS_PARSE_OK"
        )
        result = subprocess.run([host, "-NoProfile", "-Command", command], cwd=root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP1_RLS_PS_PARSE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
