import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from data.build_enterprise_gold import build_gold
from data.enterprise_seed import create_enterprise_database
from insightpilot.agent import InsightPilot, _preflight
from insightpilot.enterprise_evaluate import _compare
from insightpilot.metrics import METRICS
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner, QueryPlan


ROOT = Path(__file__).resolve().parents[1]
BLIND = ROOT / "evals/enterprise-blind-v3.jsonl"
GOLD = ROOT / "evals/enterprise-gold-v3.jsonl"
MANIFEST = ROOT / "evals/enterprise-holdout-manifest-v3.json"


class S13V3FreezeTests(unittest.TestCase):
    def test_frozen_files_hashes_leakage_and_metric_effective_windows(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        blind = [json.loads(line) for line in BLIND.read_text(encoding="utf-8").splitlines()]
        gold = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(manifest["holdout_id"], "enterprise-blind-v3")
        self.assertEqual(manifest["case_count"], 11)
        self.assertEqual(manifest["answerable_case_count"], 4)
        self.assertEqual(manifest["blind_questions_sha256"], hashlib.sha256(BLIND.read_bytes()).hexdigest())
        self.assertEqual(manifest["gold_sha256"], hashlib.sha256(GOLD.read_bytes()).hexdigest())
        self.assertEqual(manifest["exact_question_overlap_with_s4_development"], [])
        self.assertEqual(manifest["exact_question_overlap_with_prior_enterprise_evaluation"], [])
        self.assertGreater(manifest["c08_late_recorded_refunds_over_1d"], 0)
        self.assertEqual({row["case_id"] for row in blind}, {row["case_id"] for row in gold})
        for row in gold:
            if row.get("expected_status", "answered") == "answered":
                self.assertGreaterEqual(row["analysis_start"], row["effective_from"])
                self.assertEqual(row["effective_from"], METRICS[row["metric"]].effective_from)

    def test_v3_builder_recreates_oracle_and_fixed_queries_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = create_enterprise_database(root / "enterprise.sqlite")
            evals = root / "evals"
            evals.mkdir()
            blind_path, gold_path = evals / BLIND.name, evals / GOLD.name
            shutil.copyfile(BLIND, blind_path)
            build_gold(database, gold_path, blind_path)
            cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines()]
            gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()]
            plans = {
                "C04": QueryPlan("approved_refund_campaign_attribution", "2026-11-10", "2026-11-11", "华南", "campaign"),
                "C05": QueryPlan("approved_refund_campaign_attribution", "2027-01-01", "2027-01-03", None, "campaign"),
                "C08": QueryPlan("approved_refund_event_amount", "2026-10-15", "2026-10-22", None, "refund_day_utc"),
                "C09": QueryPlan("approved_refund_campaign_attribution", "2026-10-01", "2027-01-01", "华东", "campaign"),
            }

            class FixedPlanner:
                last_call_usage = None
                last_call_latency_ms = 0
                def plan(self, question):
                    case = next(row for row in cases if row["question"] == question)
                    return plans[case["case_id"]]

            results = []
            for case in cases:
                if case["case_id"] in plans:
                    result = InsightPilot(FixedPlanner(), database).analyze(case["question"])
                    expected = next(row for row in gold_rows if row["case_id"] == case["case_id"])
                    results.append(_compare(case, expected, result))
            manifest = json.loads((evals / "enterprise-holdout-manifest-v3.json").read_text(encoding="utf-8"))
        self.assertTrue(all(row["passed"] for row in results), results)
        self.assertEqual(manifest["c08_late_recorded_refunds_over_1d"],
                         json.loads(MANIFEST.read_text(encoding="utf-8"))["c08_late_recorded_refunds_over_1d"])

    def test_v3_guard_cases_match_expected_status(self):
        planner = object.__new__(OpenAICompatiblePlanner)
        questions = {row["case_id"]: row["question"] for row in
                     (json.loads(line) for line in BLIND.read_text(encoding="utf-8").splitlines())}
        for case_id in ("C01", "C02", "C03", "C10", "C11"):
            with self.subTest(case_id=case_id), self.assertRaises(NeedsClarification):
                planner.plan(questions[case_id])
        self.assertEqual(_preflight(questions["C06"])["status"], "refused")
        self.assertEqual(_preflight(questions["C07"])["reason"], "prompt_injection_attempt")


if __name__ == "__main__":
    unittest.main()
