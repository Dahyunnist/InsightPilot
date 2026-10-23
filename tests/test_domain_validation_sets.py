from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data.build_domain_validation_sets import build
from insightpilot.agent import InsightPilot
from insightpilot.ip4_evaluate import compare
from insightpilot.rule_baseline import RulePlanner


class DomainValidationSetTests(unittest.TestCase):
    def test_domain_sets_are_separate_and_python_gold_matches_sqlite_baseline(self):
        root = Path(__file__).resolve().parents[1]
        database = root / "data/insightpilot-multidomain.sqlite"
        with TemporaryDirectory() as temp:
            output = Path(temp) / "evals"
            manifest = build(database, output)
            self.assertEqual(set(manifest["domains"]), {"refunds", "marketing_roi"})
            for domain, expected_cases in (("refunds", 6), ("marketing_roi", 6)):
                prefix = output / f"{domain}"
                metadata = manifest["domains"][domain]
                cases = [json.loads(row) for row in (prefix.with_name(f"{domain}-blind-v1.jsonl")
                    .read_text(encoding="utf-8").splitlines())]
                gold = {row["case_id"]: row for row in (json.loads(line) for line in
                    prefix.with_name(f"{domain}-gold-v1.jsonl").read_text(encoding="utf-8").splitlines())}
                self.assertEqual(len(cases), expected_cases)
                self.assertEqual(metadata["answerable_count"], 4)
                self.assertEqual(metadata["guard_count"], 2)
                pilot = InsightPilot(RulePlanner(), database)
                results = [pilot.analyze(case["question"]) for case in cases]
                scores = [compare(case, gold[case["case_id"]], result)
                          for case, result in zip(cases, results)]
                self.assertEqual(sum(score["passed"] for score in scores), expected_cases)
                self.assertTrue(all(result["model_call_count"] == 0 for result in results))


if __name__ == "__main__":
    unittest.main()
