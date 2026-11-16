from dataclasses import replace
import unittest
from unittest.mock import patch

from insightpilot.metrics import METRICS
from scripts.audit_ip2_catalog import _catalog_errors


class IP2CatalogMetadataContractTests(unittest.TestCase):
    def test_current_catalog_metadata_is_consistent(self):
        self.assertEqual([], _catalog_errors())

    def test_missing_heading_and_lineage_source_are_reported(self):
        metric = METRICS["approved_refund_campaign_attribution"]
        invalid = replace(metric, definition_source="docs/product-contract.md#missing-heading",
                          source_tables=("orders",))
        with patch.dict(METRICS, {metric.metric_id: invalid}):
            errors = _catalog_errors()
        self.assertTrue(any("definition source file or anchor missing" in error for error in errors))
        self.assertTrue(any("lineage table missing from source_tables" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
