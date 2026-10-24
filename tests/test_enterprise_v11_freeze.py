import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from insightpilot.metrics import METRICS
from insightpilot.planner import OpenAICompatiblePlanner

ROOT = Path(__file__).resolve().parents[1]


class EnterpriseV11FreezeTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "reports/enterprise-v11-model-preregistration.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((ROOT / "evals/enterprise-holdout-manifest-v11.json").read_text(encoding="utf-8"))
        self.cases = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-blind-v11.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
        self.gold = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-gold-v11.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}

    def test_frozen_hashes_new_seed_and_no_exact_prompt_overlap(self):
        self.assertEqual(self.lock["seed"], 20261026)
        self.assertEqual(self.lock["exact_prompt_overlap_count"], 0)
        self.assertEqual(self.manifest["exact_prompt_overlap_count"], 0)
        self.assertEqual(self.manifest["data_fingerprint"], self.lock["data_fingerprint"])
        for path, key in (("evals/enterprise-blind-v11.jsonl", "blind_sha256"),
                          ("evals/enterprise-gold-v11.jsonl", "gold_sha256"),
                          ("evals/enterprise-holdout-manifest-v11.json", "manifest_sha256")):
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), self.lock[key])
        self.assertTrue(self.lock["gold_not_in_agent_context"])

    def test_every_answerable_prompt_parses_to_its_independent_gold_window(self):
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

    def test_v11_full_worst_case_reserve_fits_remaining_five_dollar_cap(self):
        self.assertEqual(self.lock["global_cap_usd"], 5.0)
        self.assertEqual(self.lock["global_remaining_before_usd"], 4.68258385)
        self.assertEqual(self.lock["max_api_calls"], 40)
        self.assertLessEqual(self.lock["worst_case_reserve_usd"], self.lock["global_remaining_before_usd"])
        self.assertAlmostEqual(self.lock["global_remaining_after_reserve_usd"],
                               self.lock["global_remaining_before_usd"] - self.lock["worst_case_reserve_usd"])
        self.assertFalse(self.lock["old_ip4_touched"])


if __name__ == "__main__":
    unittest.main()
