import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IP1RlsBootstrapContractTests(unittest.TestCase):
    def test_tenant_tables_have_forced_rls_policy_and_reader_grant(self):
        sql = (ROOT / "docs/postgresql-rls-bootstrap.sql").read_text(encoding="utf-8")
        tables = (
            "orders", "refunds", "products", "campaign_attributions", "campaigns",
            "campaign_spend_events", "refund_reversals", "customers",
            "inventory_daily_snapshots", "inventory_cogs_events",
        )
        for table in tables:
            with self.subTest(table=table):
                self.assertRegex(sql, rf"ALTER TABLE public\.{table} ENABLE ROW LEVEL SECURITY;")
                self.assertRegex(sql, rf"ALTER TABLE public\.{table} FORCE ROW LEVEL SECURITY;")
                self.assertRegex(
                    sql,
                    rf"CREATE POLICY insightpilot_tenant_isolation ON public\.{table} TO insightpilot_reader\s+USING \(tenant_id = current_setting\('app\.tenant_id', true\)\);",
                )
                if table != "customers":
                    self.assertRegex(sql, rf"GRANT SELECT \([^;]+\)\s+ON TABLE public\.{table} TO insightpilot_reader;")
        self.assertNotRegex(sql, r"GRANT SELECT [^;]+ ON TABLE public\.customers TO insightpilot_reader;")

    def test_global_metadata_has_no_tenant_policy(self):
        sql = (ROOT / "docs/postgresql-rls-bootstrap.sql").read_text(encoding="utf-8")
        self.assertIn("GRANT SELECT (key, value) ON TABLE public.data_meta TO insightpilot_reader;", sql)
        self.assertNotRegex(sql, r"ALTER TABLE public\.data_meta (?:ENABLE|FORCE) ROW LEVEL SECURITY")
        self.assertNotRegex(sql, r"CREATE POLICY [^;]+ ON public\.data_meta")


if __name__ == "__main__":
    unittest.main()
