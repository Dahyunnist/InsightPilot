import json
import tempfile
import unittest
from pathlib import Path

from data.build_ip4_holdout import build
from data.multidomain_seed import create_multidomain_database
from insightpilot.ip4_evaluate import evaluate


class IP4HoldoutTests(unittest.TestCase):
    def test_frozen_cross_domain_holdout_rebuilds_and_rule_baseline_passes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            database = create_multidomain_database(root / "fixture.sqlite")
            output = root / "evals"
            first = build(database, output)
            blind_bytes = (output / "ip4-blind-v1.jsonl").read_bytes()
            gold_bytes = (output / "ip4-gold-v1.jsonl").read_bytes()
            second = build(database, output)
            self.assertEqual(first, second)
            self.assertEqual(blind_bytes, (output / "ip4-blind-v1.jsonl").read_bytes())
            self.assertEqual(gold_bytes, (output / "ip4-gold-v1.jsonl").read_bytes())
            blind = [json.loads(line) for line in blind_bytes.decode("utf-8").splitlines()]
            self.assertTrue(all(set(row) == {"case_id", "question"} for row in blind))
            self.assertEqual(first["exact_normalized_prompt_overlap_count"], 0)

            report = evaluate(database, output / "ip4-blind-v1.jsonl", output / "ip4-gold-v1.jsonl",
                              output / "ip4-manifest-v1.json")
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["estimated_model_cost_usd"], 0.0)
            self.assertEqual(report["case_count"], 12)
            self.assertEqual(report["passed"], 12)
            self.assertEqual(report["refusal_metrics"]["false_refusals_on_answerable"], [])


if __name__ == "__main__":
    unittest.main()
