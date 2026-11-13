import unittest
from dataclasses import replace

from insightpilot.metrics import METRICS, METRIC_VERSIONS, get_metric, register_metric_version
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner


class MetricVersionTests(unittest.TestCase):
    class CountingPlanner(OpenAICompatiblePlanner):
        def __init__(self):
            self.use_metric_retrieval = True
            self.calls = 0

        def _complete(self, _question, _candidates, _allowed):
            self.calls += 1
            return {"clarify": False, "metric_id": "approved_refund_amount_rate",
                    "start_date": "2026-10-01", "end_date_exclusive": "2026-10-02",
                    "comparison_periods": [], "region": None, "dimension": None}

    def test_effective_date_is_allowed_and_binds_current_metric_version(self):
        planner = self.CountingPlanner()
        plan = planner.plan("2026-10-01 至 2026-10-01 批准退款金额率")

        self.assertEqual(planner.calls, 1)
        self.assertEqual(plan.metric_version, METRICS[plan.metric_id].definition_version)

    def test_day_before_effective_date_clarifies_before_model(self):
        planner = self.CountingPlanner()

        with self.assertRaises(NeedsClarification):
            planner.plan("2026-09-30 至 2026-09-30 批准退款金额率")

        self.assertEqual(planner.calls, 0)

    def test_interval_crossing_effective_date_clarifies_before_model(self):
        planner = self.CountingPlanner()

        with self.assertRaises(NeedsClarification):
            planner.plan("2026-09-30 至 2026-10-01 批准退款金额率")

        self.assertEqual(planner.calls, 0)

    def test_reversed_dates_clarify_before_model(self):
        planner = self.CountingPlanner()

        with self.assertRaises(NeedsClarification):
            planner.plan("2026-10-02 至 2026-10-01 批准退款金额率")

        self.assertEqual(planner.calls, 0)

    def test_current_and_legacy_default_version_selection(self):
        current = METRICS["approved_refund_amount_rate"]
        self.assertEqual(get_metric(current.metric_id, current.definition_version), current)
        self.assertEqual(get_metric(current.metric_id), current)

    def test_unknown_version_fails_closed(self):
        with self.assertRaisesRegex(KeyError, "unknown metric version"):
            get_metric("approved_refund_amount_rate", "99.0.0")

    def test_registered_history_does_not_replace_current_definition(self):
        current = METRICS["approved_refund_amount_rate"]
        historical = replace(current, definition_version="0.9.0", formula="historical synthetic formula")
        register_metric_version(historical)
        self.assertEqual(get_metric(current.metric_id, "0.9.0").formula, "historical synthetic formula")
        self.assertEqual(get_metric(current.metric_id, current.definition_version), current)
        with self.assertRaises(ValueError):
            register_metric_version(historical)


if __name__ == "__main__":
    unittest.main()
