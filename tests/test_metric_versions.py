import unittest
from dataclasses import replace

from insightpilot.metrics import METRICS, METRIC_VERSIONS, get_metric, register_metric_version


class MetricVersionTests(unittest.TestCase):
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
