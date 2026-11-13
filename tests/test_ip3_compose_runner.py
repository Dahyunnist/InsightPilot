import os
import shutil
import subprocess
import unittest
from pathlib import Path


class IP3ComposeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.script_path = self.root / "scripts/run_ip3_compose.ps1"
        self.script = self.script_path.read_text(encoding="utf-8-sig")

    def test_script_is_utf8_and_parses_with_windows_powershell(self):
        script_bytes = self.script_path.read_bytes()
        self.assertTrue(script_bytes.startswith(b"\xef\xbb\xbf"), "PowerShell 5.1 needs a UTF-8 BOM")
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell, "PowerShell host is required")
        env = os.environ.copy()
        env["IP3_SCRIPT_PATH"] = str(self.script_path)
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:IP3_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
            "Write-Output IP3_PS_PARSE_OK"
        )
        result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP3_PS_PARSE_OK", result.stdout)

    def test_preflight_build_start_and_cleanup_are_safe_to_rerun(self):
        script = self.script
        dockerfile = (self.root / "Dockerfile.ip3").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("$ComposeProject = 'insightpilot-ip3-compose'", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile ps -a -q", script)
        self.assertIn("docker image inspect $BaseImage", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile build --pull=false api-probe", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile build --pull api-probe", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile up -d --wait --pull never", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile down", script)
        self.assertIn(r"scripts\probe_ip3_http.py --output $SmokeReport", script)
        self.assertLess(script.index("if ($Existing)"), script.index("build --pull api-probe"))
        self.assertLess(script.index("docker image inspect $BaseImage"), script.index("$ComposeStarted = $true"))
        self.assertLess(script.index("build --pull=false api-probe"), script.index("$ComposeStarted = $true"))
        self.assertLess(script.index("$ComposeStarted = $true"), script.index(" up -d --wait --pull never"))
        self.assertIn("finally", script)
        self.assertNotIn("down -v", script.lower())
        self.assertNotIn("docker compose down -v", script.lower())


if __name__ == "__main__":
    unittest.main()
