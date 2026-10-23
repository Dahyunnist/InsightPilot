from pathlib import Path
import unittest

from insightpilot.config import Settings
from insightpilot.planner import OpenAICompatiblePlanner, PlanError


class PlannerPromptBudgetTests(unittest.TestCase):
    def test_utf8_prompt_byte_budget_blocks_request_before_network(self):
        planner = OpenAICompatiblePlanner(Settings(
            api_key="test", base_url="https://invalid.example", model="test-model",
            timeout_seconds=1, max_completion_tokens=1024,
            database_path=Path("unused.sqlite")), max_prompt_bytes_per_call=1)
        with self.assertRaisesRegex(PlanError, "configured byte budget"):
            planner._complete("退款订单率", ["approved_refund_order_rate"],
                              ["approved_refund_order_rate"])
        self.assertEqual(planner.last_call_count, 0)


if __name__ == "__main__":
    unittest.main()
