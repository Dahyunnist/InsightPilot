from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


class IP1IP3HostEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.path = self.root / "scripts/run_ip1_ip3_host_validation.ps1"
        self.script = self.path.read_text(encoding="utf-8-sig")

    def test_entrypoint_preflights_once_before_stages_and_stores_artifacts_in_tmp(self):
        script = self.script
        self.assertIn(".tmp\\host-ip1-ip3-", script)
        self.assertIn("summary.json", script)
        self.assertIn("host-validation.log", script)
        self.assertIn("docker info --format", script)
        self.assertEqual(script.count("docker info --format"), 1)
        self.assertLess(script.index("docker info --format"), script.index("run_ip1_rls.ps1"))
        self.assertLess(script.index("docker info --format"), script.index("run_ip3_compose.ps1"))
        self.assertIn("blocked_docker_daemon_unavailable", script)
        self.assertIn("ip1-data-meta-timeout.json", (self.root / "scripts/run_ip1_rls.ps1").read_text())
        self.assertIn("ip3-mcp-cancellation.json", (self.root / "scripts/run_ip3_compose.ps1").read_text(encoding="utf-8-sig"))
        self.assertIn("model_calls = 0", script)

    def test_entrypoint_redacts_logs_and_runs_child_windows_hidden(self):
        script = self.script
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn("postgres(?:ql)?://", script)
        self.assertIn("Bearer\\s+", script)
        self.assertIn("credential-related output", script)
        self.assertIn("Remove-Item -LiteralPath $rawOut, $rawErr", script)
        self.assertNotIn("down -v", script.lower())
        self.assertNotIn("volume rm", script.lower())

    def test_entrypoint_parses_with_windows_powershell(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell, "PowerShell host is required")
        env = os.environ.copy()
        env["IP1_IP3_SCRIPT_PATH"] = str(self.path)
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile(" 
            "$env:IP1_IP3_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
            "Write-Output IP1_IP3_PS_PARSE_OK"
        )
        result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP1_IP3_PS_PARSE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
