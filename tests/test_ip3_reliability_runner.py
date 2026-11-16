from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_ip3_reliability_matrix.ps1"


class IP3ReliabilityRunnerTests(unittest.TestCase):
    def test_runner_is_utf8_bom_and_parses(self):
        raw = SCRIPT_PATH.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell)
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:IP3_SCRIPT_PATH,[ref]$tokens,[ref]$errors)|Out-Null; "
            "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}; "
            "Write-Output IP3_MATRIX_PS_PARSE_OK"
        )
        env = os.environ.copy()
        env["IP3_SCRIPT_PATH"] = str(SCRIPT_PATH)
        result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IP3_MATRIX_PS_PARSE_OK", result.stdout)

    def test_execution_requires_both_switches_and_safety_preflight_precedes_children(self):
        script = SCRIPT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$Execute", script)
        self.assertIn("[switch]$ConfirmSyntheticMatrix", script)
        self.assertLess(script.index("if (-not $Execute -or -not $ConfirmSyntheticMatrix)"),
                        script.index("docker version --format"))
        self.assertLess(script.index("Assert-PortAvailable $port"),
                        script.index("run_ip3_compose.ps1"))
        self.assertLess(script.index("Assert-ProjectEmpty $project"),
                        script.index("run_ip3_compose.ps1"))
        self.assertLess(script.index("docker image inspect $BaseImage"),
                        script.index("run_ip3_compose.ps1"))
        self.assertIn("[IO.Path]::GetTempPath()", script)
        self.assertIn("[guid]::NewGuid()", script)
        self.assertIn("fixture.sqlite", script)
        self.assertNotIn("down -v", script.lower())
        self.assertNotIn("volume rm", script.lower())
        self.assertNotIn(".env", script)
        self.assertIn("assemble_ip3_reliability_matrix.py", script)
        self.assertIn("Invoke-Checked -File $Python -Arguments @(", script)
        self.assertNotIn("Invoke-Checked $Python @(", script)
        self.assertIn("Save-MatrixCleanupReport", script)
        self.assertIn("docker network ls --filter", script)
        self.assertIn("--cleanup-report", script)

    def test_invoke_checked_preserves_output_path_with_spaces(self):
        powershell = shutil.which("powershell")
        self.assertIsNotNone(powershell, "Windows PowerShell 5.1 is required for native argument regression")
        with tempfile.TemporaryDirectory(prefix="ip3 args with spaces ") as temp_name:
            spaced_dir = Path(temp_name)
            child = spaced_dir / "capture arguments.py"
            output = spaced_dir / "captured report.json"
            child.write_text(
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
                "$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Checked'},$true); "
                "if($errors.Count -or $null -eq $function){exit 2}; "
                ". ([scriptblock]::Create($function.Extent.Text)); "
                "Invoke-Checked -File $env:IP3_TEST_PYTHON -Arguments @($env:IP3_TEST_CHILD,'--output',$env:IP3_TEST_OUTPUT)"
            )
            env = os.environ.copy()
            env.update({
                "IP3_SCRIPT_PATH": str(SCRIPT_PATH),
                "IP3_TEST_PYTHON": os.sys.executable,
                "IP3_TEST_CHILD": str(child),
                "IP3_TEST_OUTPUT": str(output),
            })
            result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=ROOT,
                                    env=env, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["output"], str(output))

    def test_compose_child_receives_artifact_directory_as_named_parameter(self):
        powershell = shutil.which("powershell")
        self.assertIsNotNone(powershell, "Windows PowerShell 5.1 is required for script parameter regression")
        script = SCRIPT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("& (Join-Path $ProjectRoot 'scripts\\run_ip3_compose.ps1') -ArtifactDirectory $httpDir", script)
        self.assertNotIn("Invoke-Checked -File (Join-Path $ProjectRoot 'scripts\\run_ip3_compose.ps1')", script)
        with tempfile.TemporaryDirectory(prefix="ip3 child args with spaces ") as temp_name:
            temp = Path(temp_name)
            child = temp / "capture artifact directory.ps1"
            output = temp / "captured directory.txt"
            child.write_text(
                "param([string]$ArtifactDirectory)\n"
                "[IO.File]::WriteAllText($env:IP3_TEST_OUTPUT,$ArtifactDirectory)\n",
                encoding="utf-8",
            )
            command = "& $env:IP3_TEST_CHILD -ArtifactDirectory $env:IP3_TEST_ARTIFACT"
            env = os.environ.copy()
            env.update({"IP3_TEST_CHILD": str(child), "IP3_TEST_ARTIFACT": str(temp / "artifact with spaces"),
                        "IP3_TEST_OUTPUT": str(output)})
            result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), env["IP3_TEST_ARTIFACT"])

    def test_cleanup_report_detects_leftover_project_network(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell)
        with tempfile.TemporaryDirectory(prefix="ip3 cleanup path with spaces ") as temp_name:
            cleanup_path = Path(temp_name) / "cleanup report.json"
            command = (
                "if($PSVersionTable.PSVersion.Major -ne 5){exit 3}; "
                "$tokens=$null; $errors=$null; "
                "$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:IP3_SCRIPT_PATH,[ref]$tokens,[ref]$errors); "
                "$get=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-MatrixCleanupReport'},$true); "
                "$save=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Save-MatrixCleanupReport'},$true); "
                "if($errors.Count -or $null -eq $get -or $null -eq $save){exit 2}; "
                ". ([scriptblock]::Create($get.Extent.Text)); . ([scriptblock]::Create($save.Extent.Text)); "
                "$Projects=@('insightpilot-ip3-compose','insightpilot-ip3-db-outage','insightpilot-ip3-restart'); "
                "function docker { if($args[0] -eq 'network' -and $env:IP3_FAKE_NETWORK){$global:LASTEXITCODE=0; Write-Output $env:IP3_FAKE_NETWORK} else {$global:LASTEXITCODE=0} }; "
                "$null=Save-MatrixCleanupReport $env:IP3_TEST_CLEANUP_PATH"
            )
            env = os.environ.copy()
            env.update({"IP3_SCRIPT_PATH": str(SCRIPT_PATH), "IP3_FAKE_NETWORK": "leftover-test-network",
                        "IP3_TEST_CLEANUP_PATH": str(cleanup_path)})
            result = subprocess.run([powershell, "-NoProfile", "-Command", command], cwd=ROOT,
                                    env=env, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            raw = cleanup_path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "PowerShell 5.1 JSON must be UTF-8 without BOM")
            report = json.loads(raw.decode("utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["checked_projects"][0]["network_count"], 1)
            self.assertFalse(report["checked_projects"][0]["empty"])

if __name__ == "__main__":
    unittest.main()
