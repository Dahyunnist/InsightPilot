import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from insightpilot.enterprise_evaluate import _compare
from insightpilot.freeze import sha256_text
from insightpilot.planner import OpenAICompatiblePlanner
from insightpilot.config import Settings
from insightpilot.current_benchmark import _reserve_experiment_budget
from data.build_enterprise_v4 import build as build_v4
from data.enterprise_seed import create_enterprise_database


class S15BenchmarkAuditTests(unittest.TestCase):
    def setUp(self):
        self.case = {"case_id": "A01"}
        self.gold = {
            "metric": "approved_refund_order_rate", "dimension": "category", "region": "华东",
            "analysis_start": "2026-10-01", "analysis_end_exclusive": "2026-11-01",
            "expected": {"食品": {"numerator": 2, "denominator": 5, "value": 0.4}},
        }
        self.result = {
            "status": "answered", "metric": {"id": self.gold["metric"]},
            "plan": {"dimension": "category", "region": "华东", "start_date_inclusive": "2026-10-01",
                     "end_date_exclusive": "2026-11-01"},
            "rows": [{"group_value": "食品", "numerator": 2, "denominator": 5, "value": 0.4}],
            "verification": {"passed": True},
        }

    def test_score_rejects_wrong_dimension_region_dates_and_missing_verifier(self):
        self.assertTrue(_compare(self.case, self.gold, self.result)["passed"])
        for field, value in (("dimension", "channel"), ("region", "华南"),
                             ("end_date_exclusive", "2026-10-31")):
            changed = {**self.result, "plan": {**self.result["plan"], field: value}}
            self.assertFalse(_compare(self.case, self.gold, changed)["passed"], field)
        changed = {**self.result, "verification": {"passed": False}}
        self.assertFalse(_compare(self.case, self.gold, changed)["passed"])

    def test_refusal_score_requires_expected_reason(self):
        gold = {"expected_status": "refused", "expected_reason": "restricted_personal_data"}
        self.assertTrue(_compare(self.case, gold, {"status": "refused", "reason": "restricted_personal_data"})["passed"])
        self.assertFalse(_compare(self.case, gold, {"status": "refused", "reason": "prompt_injection_attempt"})["passed"])

    def test_text_hash_is_stable_across_newline_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lf, crlf = root / "lf.txt", root / "crlf.txt"
            lf.write_bytes("问题一\n问题二\n".encode())
            crlf.write_bytes("问题一\r\n问题二\r\n".encode())
            self.assertEqual(sha256_text(lf), sha256_text(crlf))

    def test_v4_builder_freezes_thirty_unique_prompts_and_independent_gold(self):
        source = Path(__file__).resolve().parents[1] / "evals/enterprise-blind-v4.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            (root / "evals/enterprise-blind-v4.jsonl").write_bytes(source.read_bytes())
            database = create_enterprise_database(root / "enterprise.sqlite", order_count=5000)
            manifest = build_v4(database, root)
            gold = [json.loads(line) for line in (root / "evals/enterprise-gold-v4.jsonl").read_text(encoding="utf-8").splitlines()]
            cases = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(manifest["case_count"], 30)
        self.assertEqual(manifest["exact_prompt_overlap_count"], 0)
        self.assertEqual(len({case["question"] for case in cases}), 30)
        self.assertEqual(len(gold), 30)
        by_id = {row["case_id"]: row for row in gold}
        self.assertEqual(by_id["N15"]["expected"]["全部"], {"numerator": 0, "denominator": 0, "value": None})
        self.assertEqual(by_id["G12"]["expected_reason"], "prompt_injection_attempt")

    def test_invalid_json_retry_records_both_actual_requests(self):
        settings = Settings("test", "https://example.invalid/v1", "test-model", 1, 100, Path("unused"))
        planner = OpenAICompatiblePlanner(settings)
        plans = ["{", json.dumps({
            "clarify": False, "metric_id": "approved_refund_order_rate", "start_date": "2026-10-01",
            "end_date_exclusive": "2026-10-08", "comparison_periods": [], "region": "华东", "dimension": "region",
        })]
        responses = []
        for content in plans:
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps({"model": "test-model", "usage": {"prompt_tokens": 10,
                "completion_tokens": 5, "total_tokens": 15, "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 10}, "choices": [{"message": {"content": content}}]}).encode()
            responses.append(response)
        with patch("insightpilot.planner.urllib.request.urlopen", side_effect=responses) as request:
            plan = planner.plan("2026-10-01 到 2026-10-07 华东批准退款订单率")
        self.assertIsNone(plan.dimension)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(planner.last_call_count, 2)
        self.assertEqual(len(planner.last_calls), 2)

    def test_model_experiment_reservation_respects_shared_cap_and_blocks_repeat(self):
        settings = Settings("test", "https://example.invalid/v1", "test-model", 1, 1200, Path("unused"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = {"cap_usd": 4.0, "prior_experiment_reserve_usd": 3.99,
                      "max_prompt_tokens_per_request": 2000, "max_attempts_per_case": 2,
                      "current_v4_run": {"status": "not_started"}}
            reports = root / "reports"
            reports.mkdir()
            (reports / "model-experiment-cost-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exceeds remaining"):
                _reserve_experiment_budget(root, settings, reports, 30)
            ledger["prior_experiment_reserve_usd"] = 0.10
            (reports / "model-experiment-cost-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
            _reserve_experiment_budget(root, settings, reports, 30)
            with self.assertRaisesRegex(ValueError, "already reserved"):
                _reserve_experiment_budget(root, settings, reports, 30)


if __name__ == "__main__":
    unittest.main()
