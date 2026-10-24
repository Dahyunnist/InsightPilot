import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from insightpilot.metrics import METRICS, retrieve_metrics
from insightpilot.planner import OpenAICompatiblePlanner

ROOT = Path(__file__).resolve().parents[1]


class EnterpriseV12FreezeTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "reports/enterprise-v12-model-preregistration.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((ROOT / "evals/enterprise-holdout-manifest-v12.json").read_text(encoding="utf-8"))
        self.cases = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-blind-v12.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
        self.gold = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-gold-v12.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}

    def test_frozen_hashes_new_seed_and_no_exact_prompt_overlap(self):
        self.assertEqual(self.lock["seed"], 20261027)
        self.assertEqual(self.lock["exact_prompt_overlap_count"], 0)
        self.assertEqual(self.manifest["exact_prompt_overlap_count"], 0)
        self.assertEqual(self.manifest["data_fingerprint"], self.lock["data_fingerprint"])
        for path, key in (("evals/enterprise-blind-v12.jsonl", "blind_sha256"),
                          ("evals/enterprise-gold-v12.jsonl", "gold_sha256"),
                          ("evals/enterprise-holdout-manifest-v12.json", "manifest_sha256")):
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), self.lock[key])
        self.assertTrue(self.lock["gold_not_in_agent_context"])

    def test_all_answerable_prompt_date_windows_match_gold(self):
        answerable = {case_id: row for case_id, row in self.gold.items() if "metric" in row}
        self.assertEqual(len(answerable), 11)
        for case_id, gold in answerable.items():
            with self.subTest(case_id=case_id):
                metric = METRICS[gold["metric"]]
                planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
                planner._complete = lambda *_args, item=gold: {
                    "clarify": False, "metric_id": item["metric"],
                    "start_date": item["analysis_start"],
                    "end_date_exclusive": item["analysis_end_exclusive"],
                    "comparison_periods": [], "region": item.get("region"),
                    "dimension": item.get("dimension"),
                }
                with patch("insightpilot.planner.retrieve_metrics", return_value=[metric]):
                    plan = planner.plan(self.cases[case_id]["question"])
                self.assertEqual((plan.start_date, plan.end_date),
                                 (gold["analysis_start"], gold["analysis_end_exclusive"]))

    def test_v12_holdout_exercises_fixed_x11_retrieval(self):
        question = self.cases["X11"]["question"]
        self.assertEqual([metric.metric_id for metric in retrieve_metrics(question)],
                         ["approved_refund_event_amount"])

    def test_global_cap_and_live_provider_balance_cover_worst_case_reserve(self):
        self.assertEqual(self.lock["global_cap_usd"], 5.0)
        self.assertEqual(self.lock["global_remaining_before_usd"], 4.67035375)
        self.assertEqual(self.lock["max_api_calls"], 40)
        self.assertLessEqual(self.lock["worst_case_reserve_usd"], self.lock["global_remaining_before_usd"])
        self.assertAlmostEqual(self.lock["global_remaining_after_reserve_usd"], 4.59720175)
        snapshot = self.lock["provider_balance_snapshot"]
        self.assertTrue(snapshot["is_available"])
        check = self.lock["provider_balance_reserve_check"]
        self.assertTrue(check["sufficient"])
        self.assertEqual(check["currency"], "CNY")
        self.assertGreaterEqual(check["available"], check["required"])
        self.assertFalse(self.lock["old_ip4_touched"])


if __name__ == "__main__":
    unittest.main()
