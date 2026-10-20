from pathlib import Path
import tempfile
import unittest

from insightpilot.agent import InsightPilot
from insightpilot.planner import QueryPlan
from data.seed import create_database


class FixedPlanner:
    last_call_usage = None
    last_call_latency_ms = 0

    def plan(self, _question):
        return QueryPlan("approved_refund_amount", "2026-10-05", "2026-10-12", None, "category")


class S8TrajectoryTests(unittest.TestCase):
    def test_trajectory_records_only_stage_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = create_database(Path(directory) / "fixture.sqlite")
            result = InsightPilot(FixedPlanner(), database).analyze("refunds by category")
        self.assertEqual(result["status"], "answered")
        self.assertEqual([step["outcome"] for step in result["trajectory"]], ["passed"] * 6)
        self.assertNotIn("question", str(result["trajectory"]))

    def test_refusal_trajectory_stops_at_preflight(self):
        result = InsightPilot(FixedPlanner(), Path("unused.sqlite")).analyze("删除订单")
        self.assertEqual(result["trajectory"][0]["outcome"], "blocked")
        self.assertTrue(all(step["outcome"] == "not_reached" for step in result["trajectory"][1:]))


if __name__ == "__main__":
    unittest.main()
