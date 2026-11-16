import json
import os
import shutil
import subprocess
import tempfile
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
        compose = (self.root / "docker-compose.ip3.yml").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("$ComposeProject = 'insightpilot-ip3-compose'", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile ps -a -q", script)
        self.assertIn("docker image inspect $BaseImage", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile build --pull=false api-probe", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile build --pull api-probe", script)
        self.assertIn("docker compose --project-name $ComposeProject -f $ComposeFile up -d --wait --wait-timeout 60 --pull never", script)
        self.assertIn("'down', '--remove-orphans', '--timeout', '10'", script)
        self.assertIn("$CleanupProcess.WaitForExit(30000)", script)
        self.assertIn("Invoke-HttpProbe -Python $Python -ProbeScript 'scripts\\probe_ip3_http.py' -ReportPath $SmokeReport", script)
        self.assertIn("$probeArguments = @($ProbeScript, '--output', $ReportPath)", script)
        self.assertIn("[string]$ArtifactDirectory", script)
        self.assertIn("scripts/probe_ip3_mcp_cancel.py", script)
        self.assertIn("ip3-mcp-cancellation.log", script)
        self.assertLess(script.index("ip3-mcp-cancellation.log"), script.index("if ($McpExitCode -ne 0)"))
        self.assertIn("Start-Process -FilePath $DockerPath", script)
        self.assertIn("-RedirectStandardOutput $McpRawOut -RedirectStandardError $McpRawErr", script)
        self.assertIn("Remove-Item -LiteralPath $McpRawOut, $McpRawErr", script)
        self.assertIn("$McpProcess.WaitForExit(120000)", script)
        self.assertIn("$McpProcess.Kill()", script)
        self.assertIn("IP-3 MCP cancellation smoke timed out after 120 seconds", script)
        self.assertIn("$CleanupProcess.WaitForExit(30000)", script)
        self.assertIn("'down', '--remove-orphans', '--timeout', '10'", script)
        self.assertIn("Remove-Item -LiteralPath $CleanupRawOut, $CleanupRawErr", script)
        self.assertIn("core_cancel_event_observed", script)
        self.assertLess(script.index("probe_ip3_http.py"), script.index("probe_ip3_mcp_cancel.py"))
        self.assertLess(script.index("if ($Existing)"), script.index("build --pull api-probe"))
        self.assertLess(script.index("docker image inspect $BaseImage"), script.index("$ComposeStarted = $true"))
        self.assertLess(script.index("build --pull=false api-probe"), script.index("$ComposeStarted = $true"))
        self.assertLess(script.index("$ComposeStarted = $true"), script.index(" up -d --wait --wait-timeout 60 --pull never"))
        self.assertIn("finally", script)
        self.assertNotIn("down -v", script.lower())
        self.assertNotIn("docker compose down -v", script.lower())
        dockerfile = (self.root / "Dockerfile.ip3").read_text(encoding="utf-8")
        self.assertIn("COPY scripts/probe_ip3_mcp_cancel.py", dockerfile)
        self.assertIn('IP3_CANCEL_DELAY_MS: "4000"', compose)

    def test_http_probe_passes_space_path_as_one_output_argument_in_powershell_51(self):
        powershell = shutil.which("powershell")
        self.assertIsNotNone(powershell, "Windows PowerShell 5.1 is required for native argument regression")
        with tempfile.TemporaryDirectory(prefix="ip3 probe path with spaces ") as temp_name:
            spaced_dir = Path(temp_name)
            probe = spaced_dir / "fake probe.py"
            report = spaced_dir / "http report.json"
            probe.write_text(
                "import argparse, json\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output', required=True)\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text(json.dumps({'output': args.output}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            command = (
                "if($PSVersionTable.PSVersion.Major -ne 5){exit 3}; "
                "$tokens=$null; $errors=$null; "
                "$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:IP3_SCRIPT_PATH,[ref]$tokens,[ref]$errors); "
                "$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-HttpProbe'},$true); "
                "if($errors.Count -or $null -eq $function){exit 2}; "
                ". ([scriptblock]::Create($function.Extent.Text)); "
                "Invoke-HttpProbe -Python $env:IP3_TEST_PYTHON -ProbeScript $env:IP3_TEST_PROBE -ReportPath $env:IP3_TEST_REPORT"
            )
            env = os.environ.copy()
            env.update({
                "IP3_SCRIPT_PATH": str(self.script_path),
                "IP3_TEST_PYTHON": os.sys.executable,
                "IP3_TEST_PROBE": str(probe),
                "IP3_TEST_REPORT": str(report),
            })
            result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=self.root,
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report.is_file())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["output"], str(report))


if __name__ == "__main__":
    unittest.main()
