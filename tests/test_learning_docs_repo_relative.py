import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LearningDocsRepoRelativeTests(unittest.TestCase):
    def test_host_runner_docs_use_repo_relative_commands(self):
        for relative_path in ("docs/learning/ip1-rls-host-runner.md", "docs/learning/stage-IP3.md"):
            with self.subTest(path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("E:\\job\\insightpilot", text)
        ip1 = (ROOT / "docs/learning/ip1-rls-host-runner.md").read_text(encoding="utf-8")
        ip3 = (ROOT / "docs/learning/stage-IP3.md").read_text(encoding="utf-8")
        self.assertIn(r".\scripts\run_ip1_rls.ps1", ip1)
        self.assertIn(r".\scripts\run_ip3_compose.ps1", ip3)


if __name__ == "__main__":
    unittest.main()
