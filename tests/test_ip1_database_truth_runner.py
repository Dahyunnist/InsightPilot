import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_ip1_database_truth.ps1"


class IP1DatabaseTruthRunnerTests(unittest.TestCase):
    def test_wrapper_uses_isolated_runner_and_checks_all_real_postgres_gates(self):
        source = SCRIPT.read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.ip4.yml").read_text(encoding="utf-8")
        inner = (ROOT / "scripts/run_ip1_rls.ps1").read_text(encoding="utf-8")
        self.assertIn("run_ip1_rls.ps1", source)
        self.assertIn("-Execute -ConfirmDatabaseCreation -ArtifactDirectory $artifactDirectory", source)
        self.assertIn("tenant_a", source)
        self.assertIn("tenant_b", source)
        self.assertIn("tenant_a_reused", source)
        self.assertIn("missing", source)
        self.assertIn("unknown", source)
        self.assertIn("timeout_result -eq 'timed_out'", source)
        self.assertIn("recovery_result -eq 'recovered'", source)
        self.assertIn("postgres_dml_denied", source)
        self.assertIn("postgres_ddl_denied", source)
        self.assertIn("127.0.0.1:55432:5432", compose)
        self.assertIn("pull_policy: never", compose)
        self.assertIn("tmpfs:", compose)
        self.assertNotIn("volumes:", compose.lower())
        self.assertNotIn("down -v", source.lower())
        self.assertNotIn("docker volume", source.lower())
        self.assertNotIn("INSIGHTPILOT_IP4_", source)
        self.assertNotIn(".env", source.lower())
        self.assertIn("down --remove-orphans", inner)
        self.assertNotIn("down -v", inner.lower())

    def test_wrapper_parses_without_running_docker(self):
        host = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(host, "PowerShell host is required")
        env = __import__("os").environ.copy()
        env["IP1_TRUTH_SCRIPT_PATH"] = str(SCRIPT)
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:IP1_TRUTH_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){exit 1}; Write-Output IP1_TRUTH_PS_PARSE_OK"
        )
        result = subprocess.run([host, "-NoProfile", "-Command", command], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP1_TRUTH_PS_PARSE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
