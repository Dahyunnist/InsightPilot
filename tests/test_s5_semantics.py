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

    def test_common_business_paraphrases_retrieve_supported_metrics(self):
        cases = {
            "2026-10-01 到 2026-10-31 各品类有批准退款订单的比例是多少？": "approved_refund_order_rate",
            "2026-10-01 到 2026-10-31 各渠道获批退款金额与实收金额之比是多少？": "approved_refund_amount_rate",
            "2026-10-01 到 2026-10-31 查询各种退款处理状态对应的金额。": "refund_amount_by_status",
            "2026-10-01 到 2026-10-31 已付款订单和发生批准退款的订单各有多少？": "approved_refund_order_counts",
            "2026-10-01 到 2026-10-31 实际退款日期内批准金额合计是多少？": "approved_refund_event_amount",
            "2026-10-01 至 2026-10-31 付款成功订单数及存在退款的去重订单数是多少？": "approved_refund_order_counts",
            "2026-10-01 至 2026-10-31 逐日列出退款事件中获批金额。": "approved_refund_event_amount",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual([metric.metric_id for metric in retrieve_metrics(question)], [expected])

    def test_paid_amount_ratio_does_not_retrieve_plain_refund_amount(self):
        for question in ("批准退款金额占实付金额的比率", "批准退款金额与实付金额之比"):
            with self.subTest(question=question):
                ids = {metric.metric_id for metric in retrieve_metrics(question)}
                self.assertEqual(ids, {"approved_refund_amount_rate"})

    def test_region_filter_is_not_misread_as_region_group(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_: {
            "clarify": False, "metric_id": "approved_refund_order_rate",
            "start_date": "2026-11-01", "end_date_exclusive": "2026-12-01",
            "comparison_periods": [], "region": "华南", "dimension": "region",
        }
        plan = planner.plan("2026-11-01 到 2026-11-30 华南批准退款订单率是多少？")
        self.assertEqual(plan.region, "华南")
        self.assertIsNone(plan.dimension)

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
