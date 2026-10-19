import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.seed import DATA_VERSION, create_database


ROOT = Path(__file__).resolve().parents[1]


class S0FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = create_database(Path(self.tempdir.name) / "fixture.sqlite")
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_fixture_is_versioned_and_counts_are_fixed(self):
        version = self.conn.execute("SELECT value FROM data_meta WHERE key='data_version'").fetchone()[0]
        self.assertEqual(version, DATA_VERSION)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM orders").fetchone()[0], 30)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM refunds").fetchone()[0], 32)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM products").fetchone()[0], 3)

    def test_hand_check_amount_refund_rate_and_category_contribution(self):
        # Hand-check: 50,000 / 90,000 cents = 55.5556%; category approved refunds total 50,000.
        row = self.conn.execute("""
            WITH refund_total AS (
                SELECT SUM(r.amount_cents) AS cents
                FROM orders o JOIN refunds r USING(order_id)
                WHERE o.status='paid' AND o.region='华东' AND r.status='approved'
                  AND o.ordered_at >= '2026-10-05T00:00:00Z'
                  AND o.ordered_at <  '2026-10-12T00:00:00Z'
            ), paid_total AS (
                SELECT SUM(paid_amount_cents) AS cents
                FROM orders
                WHERE status='paid' AND region='华东'
                  AND ordered_at >= '2026-10-05T00:00:00Z'
                  AND ordered_at <  '2026-10-12T00:00:00Z'
            )
            SELECT refund_total.cents, paid_total.cents FROM refund_total, paid_total
        """).fetchone()
        self.assertEqual(row, (50000, 90000))

        rows = self.conn.execute("""
            SELECT p.category, SUM(r.amount_cents)
            FROM orders o JOIN products p USING(product_id)
            JOIN refunds r USING(order_id)
            WHERE o.status='paid' AND o.region='华东' AND r.status='approved'
              AND o.ordered_at >= '2026-10-05T00:00:00Z'
              AND o.ordered_at <  '2026-10-12T00:00:00Z'
            GROUP BY p.category ORDER BY p.category
        """).fetchall()
        self.assertEqual(rows, [("家电", 20000), ("服饰", 10000), ("食品", 20000)])

    def test_ambiguous_cases_never_have_executable_metric(self):
        cases = [json.loads(line) for line in (ROOT / "evals/cases.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(c["kind"] == "normal" for c in cases), 12)
        self.assertEqual(sum(c["kind"] in ("ambiguity", "refusal") for c in cases), 4)
        for case in cases:
            if case["kind"] in ("ambiguity", "refusal"):
                self.assertIsNone(case["metric"], case["id"])


if __name__ == "__main__":
    unittest.main()
