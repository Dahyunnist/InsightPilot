import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EnterpriseV10FreezeTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "reports/enterprise-v10-model-preregistration.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((ROOT / "evals/enterprise-holdout-manifest-v10.json").read_text(encoding="utf-8"))
        self.cases = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-blind-v10.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
        self.gold = {row["case_id"]: row for row in (
            json.loads(line) for line in (ROOT / "evals/enterprise-gold-v10.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}

    def test_frozen_hashes_seed_and_dataset_match(self):
        self.assertEqual(self.lock["seed"], 20261025)
        self.assertEqual(self.lock["exact_prompt_overlap_count"], 0)
        self.assertEqual(self.manifest["data_fingerprint"], self.lock["data_fingerprint"])
        for path, key in (("evals/enterprise-blind-v10.jsonl", "blind_sha256"),
                          ("evals/enterprise-gold-v10.jsonl", "gold_sha256"),
                          ("evals/enterprise-holdout-manifest-v10.json", "manifest_sha256")):
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), self.lock[key])
        self.assertTrue(self.lock["gold_not_in_agent_context"])

    def test_answerable_question_dates_match_gold_windows(self):
        date_evidence = {
            "X01": "2026年10月", "X02": "2026年10月", "X03": "2026年10月",
            "X04": "2026-10-15至2026-10-21", "X05": "2026-10-15到2026-10-21",
            "X06": "2026年10月", "X08": "2026年10月",
            "X09": "2026年10月、11月、12月", "X10": "2026年10月至12月",
            "X11": "2026-11-15至2026-11-21", "X12": "2028年1月1日—7日",
        }
        for case_id, date_text in date_evidence.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(self.gold[case_id].get("expected_status", "answered"), "answered")
                self.assertIn(date_text, self.cases[case_id]["question"])
        self.assertEqual(self.gold["X04"]["analysis_start"], "2026-10-15")
        self.assertEqual(self.gold["X04"]["analysis_end_exclusive"], "2026-10-22")
        self.assertEqual(self.gold["X11"]["analysis_start"], "2026-11-15")
        self.assertEqual(self.gold["X11"]["analysis_end_exclusive"], "2026-11-22")

    def test_v10_reserve_fits_authorized_cap_and_v9_score_is_invalid(self):
        self.assertEqual(self.lock["global_cap_usd"], 5.0)
        self.assertEqual(self.lock["max_api_calls"], 40)
        self.assertLessEqual(self.lock["worst_case_reserve_usd"], self.lock["global_remaining_before_usd"])
        self.assertAlmostEqual(self.lock["global_remaining_after_reserve_usd"],
                               self.lock["global_remaining_before_usd"] - self.lock["worst_case_reserve_usd"])
        self.assertFalse(self.lock["old_ip4_touched"])
        v9 = json.loads((ROOT / "reports/enterprise-v9-model-blind-report.json").read_text(encoding="utf-8"))
        self.assertFalse(v9["score_valid"])
        self.assertEqual(v9["model_calls"], 10)


if __name__ == "__main__":
    unittest.main()
