import unittest
from pathlib import Path


class IP1RlsRunnerTests(unittest.TestCase):
    def test_runner_scopes_cleanup_and_never_removes_volumes(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/run_ip1_rls.ps1").read_text(encoding="utf-8")
        sql = (root / "docs/postgresql-rls-bootstrap.sql").read_text(encoding="utf-8").upper()
        compose = (root / "docker-compose.ip4.yml").read_text(encoding="utf-8")
        self.assertIn("docker compose -f $compose stop postgres", script)
        self.assertNotIn("DOWN -V", script.upper())
        self.assertNotIn("VOLUME RM", script.upper())
        self.assertIn("127.0.0.1:55432:5432", compose)
        self.assertIn("tmpfs:", compose)
        self.assertNotIn("docker run", script.lower())
        self.assertNotIn("docker compose down", script.lower())
        self.assertNotIn(".env", script.lower())
        self.assertIn("[switch]$Execute", script)
        self.assertIn("[switch]$ConfirmDatabaseCreation", script)
        self.assertIn("if (-not $Execute)", script)
        self.assertIn("Refusing non-local PostgreSQL target", script)
        self.assertIn("if (-not $ConfirmDatabaseCreation)", script)
        self.assertLess(script.index("if (-not $Execute)"), script.index("docker compose -f $compose up -d postgres"))
        self.assertLess(script.index("if (-not $ConfirmDatabaseCreation)"), script.index("docker compose -f $compose up -d postgres"))
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("CURRENT_SETTING('APP.TENANT_ID'", sql)
        self.assertIn("INSIGHTPILOT_READER", sql)


if __name__ == "__main__":
    unittest.main()
