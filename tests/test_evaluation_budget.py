import unittest

from insightpilot.enterprise_evaluate import EvaluationBudget


class EvaluationBudgetTests(unittest.TestCase):
    def test_reserves_peak_cost_before_each_request_and_stops_at_cap(self):
        budget = EvaluationBudget(cap_usd=0.004, max_calls=10)
        budget.reserve()
        budget.reserve()
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            budget.reserve()
        self.assertEqual(budget.calls, 2)
        self.assertLessEqual(budget.reserved_usd, budget.cap_usd)

    def test_records_token_based_peak_cost(self):
        budget = EvaluationBudget()
        budget.reserve()
        budget.record({"prompt_tokens": 1000, "completion_tokens": 100})
        self.assertAlmostEqual(budget.estimated_usd, 0.00042)


if __name__ == "__main__":
    unittest.main()
