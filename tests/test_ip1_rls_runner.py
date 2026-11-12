import unittest
from pathlib import Path


class IP1RlsRunnerTests(unittest.TestCase):
    def test_runner_scopes_cleanup_and_never_removes_volumes(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/run_ip1_rls.ps1").read_text(encoding="utf-8")
        sql = (root / "docs/postgresql-rls-bootstrap.sql").read_text(encoding="utf-8").upper()
        self.assertIn("docker compose -f $compose stop postgres", script)
        self.assertNotIn("DOWN -V", script.upper())
        self.assertNotIn("VOLUME RM", script.upper())
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("CURRENT_SETTING('APP.TENANT_ID'", sql)
        self.assertIn("INSIGHTPILOT_READER", sql)


if __name__ == "__main__":
    unittest.main()
