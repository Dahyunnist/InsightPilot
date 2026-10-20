import unittest

from insightpilot.enterprise_evaluate import _compare_without_verification, _wilson_interval


class S14EvaluationStatisticsTests(unittest.TestCase):
    def test_wilson_interval_shows_small_sample_uncertainty(self):
        self.assertEqual(_wilson_interval(11, 11), [0.7412, 1.0])
        self.assertEqual(_wilson_interval(4, 4), [0.5101, 1.0])

    def test_no_verifier_scoring_recovers_region_from_bound_parameter(self):
        case = {"case_id": "C09"}
        gold = {"metric": "approved_refund_campaign_attribution", "dimension": "campaign", "region": "华东",
                "expected": {"Campaign 01": {"numerator_cents": 100, "denominator_cents": 200, "value": 0.5}}}
        result = {"status": "answered", "metric": {"id": "approved_refund_campaign_attribution"},
                  "plan": {"dimension": "campaign"}, "parameters": ["2026-10-01T00:00:00Z", "华东"],
                  "rows": [{"group_value": "Campaign 01", "numerator_cents": 100,
                            "denominator_cents": 200, "value": 0.5}],
                  "verification": {"passed": None, "disabled": True}}
        comparison, adjustment = _compare_without_verification(case, gold, result)
        self.assertTrue(comparison["passed"])
        self.assertFalse(comparison["sql_verified"])
        self.assertEqual(adjustment["value"], "华东")
        self.assertEqual(adjustment["source"], "region literal found in parameterized SQL bindings")


if __name__ == "__main__":
    unittest.main()
