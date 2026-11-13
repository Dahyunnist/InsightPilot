import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from data.inventory_fixture import create_inventory_fixture
from insightpilot.metrics import METRICS, catalog_document
from insightpilot.runner import read_data_identity
from scripts.audit_ip2_catalog import build_reports


ROOT = Path(__file__).resolve().parents[1]


class IP2CatalogAuditTests(unittest.TestCase):
    def test_every_metric_has_version_scope_sources_time_and_freshness(self):
        known = set(catalog_document()["data_contract_versions"])
        for metric in METRICS.values():
            with self.subTest(metric=metric.metric_id):
                self.assertTrue(metric.definition_version)
                date.fromisoformat(metric.effective_from)
                ZoneInfo(metric.timezone)
                self.assertTrue(metric.definition_source.startswith("docs/product-contract.md#"))
                self.assertTrue((ROOT / metric.definition_source.split("#", 1)[0]).is_file())
                self.assertTrue(metric.source_tables)
                self.assertTrue(metric.lineage)
                self.assertTrue(metric.freshness)
                self.assertTrue(metric.required_data_versions)
                self.assertLessEqual(set(metric.required_data_versions), known)
        amount_rate = METRICS["approved_refund_amount_rate"]
        self.assertIn("orders.paid_amount_cents", amount_rate.lineage)

    def test_inventory_fingerprint_includes_additive_fixture_rows_and_is_reproducible(self):
        with tempfile.TemporaryDirectory(prefix="ip2-catalog-", dir=ROOT / "data") as temp_dir:
            first = create_inventory_fixture(Path(temp_dir) / "first.sqlite")
            second = create_inventory_fixture(Path(temp_dir) / "second.sqlite")
            version, fingerprint = read_data_identity(first)
            self.assertEqual(version, "insightpilot-inventory-v1")
            self.assertEqual(64, len(fingerprint))
            self.assertEqual(read_data_identity(first), read_data_identity(second))

    def test_frozen_boundary_bundle_is_development_only_and_hash_linked(self):
        boundaries, report = build_reports()
        self.assertEqual("ip2-cross-domain-boundary-development-v1", boundaries["suite_id"])
        self.assertFalse(boundaries["is_blind"])
        self.assertEqual(0, boundaries["model_calls"])
        self.assertEqual("passed", report["status"])
        self.assertEqual("insightpilot-semantic-catalog-v3", report["catalog_version"])
        self.assertEqual(0, report["ambiguous_refund_rate_probe"]["model_calls"])
        self.assertEqual("clarified", report["ambiguous_refund_rate_probe"]["status"])
        self.assertEqual({"refunds", "marketing_roi", "inventory_turnover"}, set(boundaries["sources"]))
        inventory = boundaries["sources"]["inventory_turnover"]
        self.assertFalse(inventory["is_blind"])
        inventory_dir = ROOT / "evals/ip2-inventory-turnover-dev-v1"
        manifest = json.loads((inventory_dir / "inventory-turnover-dev-manifest-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(3, manifest["case_count"])
        gold = {row["case_id"]: row for row in (
            json.loads(line) for line in (inventory_dir / manifest["gold_path"]).read_text(encoding="utf-8").splitlines())}
        food = next(row for row in gold["IP2-INV-D03"]["rows"] if row["group_value"] == "食品")
        self.assertEqual(0, food["numerator_cents"])
        self.assertIsNone(food["denominator_cents"])
        self.assertIsNone(food["value"])

        boundary_path = ROOT / "reports/ip2-boundary-development-manifest-v1.json"
        catalog_path = ROOT / "reports/ip2-metric-catalog-audit-v1.json"
        self.assertEqual(boundaries, json.loads(boundary_path.read_text(encoding="utf-8")))
        self.assertEqual(report, json.loads(catalog_path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
