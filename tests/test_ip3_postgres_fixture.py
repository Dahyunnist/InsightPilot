from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from data.ip3_reliability_fixture import DATA_FINGERPRINT, DATA_VERSION, create_fixture


class IP3PostgresFixtureTests(unittest.TestCase):
    def test_empty_fixture_contains_tables_required_by_postgres_loader(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = create_fixture(Path(temp_name) / "fixture.sqlite")
            with closing(sqlite3.connect(path)) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                self.assertTrue({"orders", "refunds", "products", "data_meta", "refund_reversals",
                                 "campaign_attributions", "campaign_spend_events"}.issubset(tables))
                meta = dict(connection.execute("SELECT key, value FROM data_meta"))
                self.assertEqual(meta["data_version"], DATA_VERSION)
                self.assertEqual(meta["data_fingerprint"], DATA_FINGERPRINT)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM refunds WHERE status='approved'").fetchone()[0], 1)
                self.assertEqual(meta["snapshot_at"], "2026-10-12T00:00:00Z")
                for table in ("refund_reversals", "campaign_attributions", "campaign_spend_events"):
                    self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
