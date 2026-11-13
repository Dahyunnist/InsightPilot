import unittest
from pathlib import Path


class IP3ComposeRunnerTests(unittest.TestCase):
    def test_preflight_and_cleanup_fail_closed_without_pull_or_volume_delete(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/run_ip3_compose.ps1").read_text(encoding="utf-8")
        self.assertIn("python:3.12-slim", script)
        self.assertIn("docker image inspect $BaseImage", script)
        self.assertIn("脚本不会拉取镜像", script)
        self.assertIn("docker compose -f $ComposeFile ps -q", script)
        self.assertLess(script.index("if ($Existing)"), script.index("$ComposeStarted = $true"))
        self.assertIn("finally", script)
        self.assertIn("docker compose -f $ComposeFile down", script)
        self.assertNotIn("down -v", script.lower())
        self.assertNotIn("docker pull", script.lower())


if __name__ == "__main__":
    unittest.main()
