import json
import tempfile
import unittest
from pathlib import Path

from data.build_ip4_v3_holdout import build
from data.multidomain_seed import create_multidomain_database
from insightpilot.agent import InsightPilot
from insightpilot.rule_baseline import RulePlanner


class IP4V3HoldoutTests(unittest.TestCase):
    def test_expanded_independent_gold_is_stable_and_hash_locked(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".ip4-v3-test-") as temp:
            root = Path(temp)
            database = create_multidomain_database(root / "fixture.sqlite")
            output = root / "evals"
            first = build(database, output)
            blind_path = output / "ip4-v3-blind.jsonl"
            gold_path = output / "ip4-v3-gold.jsonl"
            blind_bytes, gold_bytes = blind_path.read_bytes(), gold_path.read_bytes()
            second = build(database, output)
            self.assertEqual(first, second)
            self.assertEqual(blind_bytes, blind_path.read_bytes())
            self.assertEqual(gold_bytes, gold_path.read_bytes())

            blind = [json.loads(line) for line in blind_bytes.decode("utf-8").splitlines()]
            gold = [json.loads(line) for line in gold_bytes.decode("utf-8").splitlines()]
            self.assertEqual(len(blind), 24)
            self.assertEqual(len(gold), 24)
            self.assertEqual(first["domain_counts"], {"refunds": 12, "marketing_roi": 12})
            self.assertEqual(first["answerable_case_count"], 16)
            self.assertEqual(first["guard_case_count"], 8)
            self.assertTrue(all(set(case) == {"case_id", "question"} for case in blind))
            self.assertEqual({row["expected_status"] for row in gold}, {"answered", "clarify", "refused"})
            self.assertEqual(first["exact_normalized_prompt_overlap_count"], 0)

            question_by_id = {row["case_id"]: row["question"] for row in blind}
            pilot = InsightPilot(RulePlanner(), database)
            for row in gold:
                if row["expected_status"] == "answered":
                    continue
                result = pilot.analyze(question_by_id[row["case_id"]])
                self.assertEqual(result["status"], row["expected_status"], row["case_id"])
                if row.get("expected_reason"):
                    self.assertEqual(result["reason"], row["expected_reason"], row["case_id"])
                self.assertEqual(result["model_call_count"], 0)

            manifest = json.loads((output / "ip4-v3-manifest.json").read_text(encoding="utf-8"))
            import hashlib
            self.assertEqual(hashlib.sha256(blind_bytes).hexdigest(), manifest["blind_sha256"])
            self.assertEqual(hashlib.sha256(gold_bytes).hexdigest(), manifest["gold_sha256"])


if __name__ == "__main__":
    unittest.main()
