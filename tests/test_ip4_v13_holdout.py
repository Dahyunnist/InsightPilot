import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data.build_ip4_v13_holdout import CASES, MARKETING_SEED, MAX_PROMPT_BYTES_PER_CALL, RETAIL_SEED, build
from data.multidomain_seed import create_multidomain_database
from insightpilot.config import Settings
from insightpilot.metrics import retrieve_metrics
from insightpilot.planner import OpenAICompatiblePlanner


ROOT = Path(__file__).resolve().parents[1]


class IP4V13HoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.database = create_multidomain_database(
            cls.root / "v13.sqlite", seed=MARKETING_SEED, retail_seed=RETAIL_SEED)
        cls.output = cls.root / "evals"
        cls.manifest = build(cls.database, cls.output)
        cls.blind = {row["case_id"]: row for row in (
            json.loads(line) for line in (cls.output / "ip4-v13-blind.jsonl").read_text(encoding="utf-8").splitlines())}
        cls.gold = {row["case_id"]: row for row in (
            json.loads(line) for line in (cls.output / "ip4-v13-gold.jsonl").read_text(encoding="utf-8").splitlines())}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_manifest_hashes_domains_new_seeds_and_edge_cases(self):
        self.assertEqual(len(CASES), 22)
        self.assertEqual(self.manifest["domain_counts"], {"refunds": 12, "marketing_roi": 10})
        self.assertEqual(self.manifest["answerable_case_count"], 14)
        self.assertEqual(self.manifest["guard_case_count"], 8)
        self.assertNotEqual(self.manifest["retail_seed"], 20261020)
        self.assertNotEqual(self.manifest["marketing_seed"], 20261027)
        self.assertEqual(self.manifest["exact_normalized_prompt_overlap_count"], 0)
        self.assertEqual(set(self.blind), set(self.gold))
        for name, key in (("ip4-v13-blind.jsonl", "blind_sha256"),
                          ("ip4-v13-gold.jsonl", "gold_sha256")):
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(),
                             self.manifest[key])
        coverage = self.manifest["edge_coverage"]
        self.assertGreater(coverage["refund_reversals_for_marketing_cohorts"], 0)
        self.assertGreater(coverage["orders_without_campaign_attribution"], 0)
        self.assertTrue(coverage["duplicate_business_event_case_ids"])
        self.assertTrue(coverage["null_roi_case_ids"])
        self.assertEqual(len(coverage["ambiguity_guard_case_ids"]), 5)
        self.assertTrue(all(set(row) == {"case_id", "question"} for row in self.blind.values()))

    def test_answerable_questions_retrieve_expected_metric_and_parse_window(self):
        by_id = {case["case_id"]: case for case in CASES}
        for case_id, gold in self.gold.items():
            if gold.get("expected_status") != "answered":
                continue
            case = by_id[case_id]
            question = self.blind[case_id]["question"]
            metric_ids = [metric.metric_id for metric in retrieve_metrics(question)]
            with self.subTest(case_id=case_id):
                self.assertIn(gold["metric"], metric_ids)
                planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
                planner._complete = lambda _question, candidates, _allowed, item=gold: {
                    "clarify": False, "metric_id": item["metric"],
                    "start_date": item["analysis_start"],
                    "end_date_exclusive": item["analysis_end_exclusive"],
                    "comparison_periods": [], "region": item["region"],
                    "dimension": item["dimension"],
                }
                plan = planner.plan(question)
                self.assertEqual(plan.metric_id, gold["metric"])
                self.assertEqual((plan.start_date, plan.end_date),
                                 (gold["analysis_start"], gold["analysis_end_exclusive"]))
                self.assertEqual((plan.dimension, plan.region), (gold["dimension"], gold["region"]))

    def test_seeded_fixture_changes_retail_and_marketing_fingerprints(self):
        other = create_multidomain_database(self.root / "other-seeds.sqlite", seed=MARKETING_SEED + 1,
                                            retail_seed=RETAIL_SEED + 1, order_count=1_000)
        from data.multidomain_seed import quality_report
        self.assertNotEqual(quality_report(self.database)["data_fingerprint"],
                            quality_report(other)["data_fingerprint"])

    def test_production_prompt_payload_fits_locked_input_byte_ceiling(self):
        by_question = {row["question"]: self.gold[case_id] for case_id, row in self.blind.items()}
        prompt_sizes = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        def fake_urlopen(request, timeout):
            body = json.loads(request.data)
            user_payload = json.loads(body["messages"][1]["content"])
            gold = by_question[user_payload["question"]]
            prompt_sizes.append(len(body["messages"][0]["content"].encode("utf-8")) +
                                len(body["messages"][1]["content"].encode("utf-8")))
            response = {"choices": [{"message": {"content": json.dumps({
                "clarify": False, "metric_id": gold["metric"],
                "start_date": gold["analysis_start"], "end_date_exclusive": gold["analysis_end_exclusive"],
                "comparison_periods": [], "region": gold["region"], "dimension": gold["dimension"],
            })}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            fake = FakeResponse()
            fake.payload = json.dumps(response).encode("utf-8")
            return fake

        settings = Settings("offline-test-key", "https://example.invalid", "offline-test-model", 1, 1024,
                            self.root / "unused.sqlite")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for case_id, gold in self.gold.items():
                if gold.get("expected_status") != "answered":
                    continue
                planner = OpenAICompatiblePlanner(settings,
                                                  max_prompt_bytes_per_call=MAX_PROMPT_BYTES_PER_CALL)
                planner.plan(self.blind[case_id]["question"])
        self.assertEqual(len(prompt_sizes), 14)
        self.assertLessEqual(max(prompt_sizes), MAX_PROMPT_BYTES_PER_CALL,
                             f"largest production prompt: {max(prompt_sizes)} bytes")

    def test_v13_preregistration_remains_immutable_but_runtime_lock_is_stale(self):
        lock = json.loads((ROOT / "reports/ip4-v13-model-preregistration.json").read_text(encoding="utf-8"))
        seal_path = ROOT / "reports/ip4-v13-gold-seal.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "frozen_pre_model")
        self.assertEqual(lock["model_calls"], 0)
        self.assertFalse(lock["old_ip4_touched"])
        self.assertFalse(lock["v12_score_reused"])
        self.assertEqual(lock["max_api_calls"], 44)
        self.assertEqual(lock["execution_attempts_per_case"], 1)
        self.assertEqual(lock["planned_max_api_calls"], 22)
        self.assertEqual(lock["input_tokens_per_request"], 3_000)
        self.assertEqual(lock["max_prompt_bytes_per_call"], MAX_PROMPT_BYTES_PER_CALL)
        self.assertLessEqual(lock["worst_case_reserve_usd"], lock["global_remaining_before_usd"])
        self.assertLessEqual(lock["worst_case_reserve_cny"], lock["max_reserve_cny_limit"])
        self.assertTrue(lock["provider_balance_reserve_check"]["sufficient"])
        self.assertEqual(seal["status"], "sealed_pre_model")
        self.assertEqual(lock["artifacts"]["gold_seal_sha256"],
                         hashlib.sha256(seal_path.read_bytes()).hexdigest())
        self.assertNotEqual(lock["artifacts"]["model_runner_sha256"],
                         hashlib.sha256((ROOT / "scripts/run_ip4_v13_model_blind.py").read_bytes()).hexdigest())
        self.assertEqual(lock["artifacts"]["scorer_sha256"],
                         hashlib.sha256((ROOT / "insightpilot/enterprise_evaluate.py").read_bytes()).hexdigest())
        self.assertNotEqual(lock["artifacts"]["agent_runtime_sha256"],
                            hashlib.sha256((ROOT / "insightpilot/agent.py").read_bytes()).hexdigest())
        self.assertEqual(lock["artifacts"]["requirements_sha256"],
                         hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
