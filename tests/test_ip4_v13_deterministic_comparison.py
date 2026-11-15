from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.run_ip4_v13_deterministic_comparison import DEFAULT_DATABASE, run


class IP4V13DeterministicComparisonTests(unittest.TestCase):
    def test_frozen_rules_compare_without_model_or_payload_disclosure(self):
        with TemporaryDirectory(prefix="ip4-v13-deterministic-") as temporary:
            report = run(database=DEFAULT_DATABASE, output=Path(temporary) / "comparison.json")
            stored = json.loads((Path(temporary) / "comparison.json").read_text(encoding="utf-8"))

        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["model_requests_sent"], 0)
        self.assertEqual(report["evaluation_type"], "deterministic_rule_policy_comparison_not_llm")
        self.assertEqual(report["frozen_hashes"]["blind_sha256"],
                         "c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66")
        self.assertEqual(report["frozen_hashes"]["gold_sha256"],
                         "f10959a4be664a69f39d6ff3079b6d3d93705e310e09be5abdc6c2b0fe335df2")
        with_verifier = report["arms"]["ruleplanner_with_independent_verifier"]
        without_verifier = report["arms"]["ruleplanner_without_independent_verifier"]
        self.assertEqual((with_verifier["passed"], with_verifier["total"]), (15, 22))
        self.assertEqual((without_verifier["passed"], without_verifier["total"]), (15, 22))
        self.assertEqual(with_verifier["false_refusals"], [
            "IP4V13-R02", "IP4V13-R05", "IP4V13-R06", "IP4V13-R07"])
        self.assertEqual(with_verifier["false_accepts"], ["IP4V13-M09"])
        self.assertEqual([row["case_id"] for row in with_verifier["failures"]],
                         ["IP4V13-R02", "IP4V13-R04", "IP4V13-R05", "IP4V13-R06",
                          "IP4V13-R07", "IP4V13-M01", "IP4V13-M09"])
        self.assertTrue(all("sql_verified" not in row["failed_checks"]
                            for row in without_verifier["failures"]))
        def keys(value):
            if isinstance(value, dict):
                return {key.casefold() for key in value} | set().union(*(keys(child) for child in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(child) for child in value)) if value else set()
            return set()

        emitted_keys = keys(stored)
        self.assertFalse({"question", "questions", "gold", "sql", "parameters", "rows"} & emitted_keys)


if __name__ == "__main__":
    unittest.main()
