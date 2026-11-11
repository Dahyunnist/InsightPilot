import json
import tempfile
import unittest
from pathlib import Path

from data.build_ip4_v2_holdout import build
from data.multidomain_seed import create_multidomain_database
from insightpilot.ip4_evaluate import evaluate


class IP4V2HoldoutTests(unittest.TestCase):
    def test_independent_manifest_gold_and_ambiguity_cases_are_reproducible(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".ip4-v2-test-") as temp:
            root = Path(temp)
            database = create_multidomain_database(root / "fixture.sqlite")
            output = root / "evals"
            first = build(database, output)
            blind_path, gold_path = output / "ip4-v2-blind.jsonl", output / "ip4-v2-gold.jsonl"
            blind_bytes, gold_bytes = blind_path.read_bytes(), gold_path.read_bytes()
            second = build(database, output)
            self.assertEqual(first, second)
            self.assertEqual(blind_bytes, blind_path.read_bytes())
            self.assertEqual(gold_bytes, gold_path.read_bytes())
            blind = [json.loads(line) for line in blind_bytes.decode("utf-8").splitlines()]
            gold = [json.loads(line) for line in gold_bytes.decode("utf-8").splitlines()]
            self.assertTrue(all(set(case) == {"case_id", "question"} for case in blind))
            self.assertEqual({row["expected_status"] for row in gold}, {"answered", "clarify"})
            self.assertEqual(first["exact_normalized_prompt_overlap_count"], 0)

            report = evaluate(database, blind_path, gold_path, output / "ip4-v2-manifest.json")
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["case_count"], 4)
            self.assertEqual(report["passed"], 4, report["failures"])
            self.assertEqual(report["answerable"], {"passed": 2, "total": 2})
            self.assertEqual(report["refusal_metrics"]["false_refusals_on_answerable"], [])


if __name__ == "__main__":
    unittest.main()
