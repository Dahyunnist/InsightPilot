import json
import unittest

from insightpilot.metrics import METRICS, catalog_document, retrieve_metrics
from insightpilot.planner import OpenAICompatiblePlanner, PlanError


class S5SemanticTests(unittest.TestCase):
    def test_catalog_has_version_lineage_grain_dimensions_and_sources(self):
        catalog = catalog_document()
        self.assertEqual(catalog["catalog_version"], "insightpilot-semantic-catalog-v1")
        amount = next(metric for metric in catalog["metrics"] if metric["metric_id"] == "approved_refund_amount_rate")
        self.assertEqual(amount["definition_version"], "1.0.0")
        self.assertIn("refunds.amount_cents", amount["lineage"])
        self.assertIn("category", amount["valid_dimensions"])
        self.assertIn("refund_event_rate", catalog["excluded_definitions"])
        json.dumps(catalog, ensure_ascii=False)

    def test_metric_retrieval_keeps_ambiguous_refund_rate_unselected(self):
        ids = {metric.metric_id for metric in retrieve_metrics("2026-10-05 到 2026-10-11 退款率是多少？")}
        self.assertEqual(ids, {"approved_refund_order_rate", "approved_refund_amount_rate"})

    def test_explicit_amount_rate_synonym_retrieves_amount_rate(self):
        ids = {metric.metric_id for metric in retrieve_metrics("指定日期批准退款金额率是多少？")}
        self.assertEqual(ids, {"approved_refund_amount_rate"})

    def test_planner_rejects_dimension_outside_metric_contract(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_: {
            "clarify": False, "metric_id": "approved_refund_event_amount",
            "region": "华东", "dimension": "category",
        }
        with self.assertRaises(PlanError):
            planner.plan("2026-10-05 到 2026-10-11 每天批准退款金额按品类？")


if __name__ == "__main__":
    unittest.main()
