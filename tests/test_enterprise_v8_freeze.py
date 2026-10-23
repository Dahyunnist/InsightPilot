import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EnterpriseV8FreezeTests(unittest.TestCase):
    def test_v8_blind_gold_and_preregistration_hashes_match(self):
        lock = json.loads((ROOT / "reports/enterprise-v8-model-preregistration.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "evals/enterprise-holdout-manifest-v8.json").read_text(encoding="utf-8"))
        for filename, key in (("evals/enterprise-blind-v8.jsonl", "blind_sha256"),
                              ("evals/enterprise-gold-v8.jsonl", "gold_sha256"),
                              ("evals/enterprise-holdout-manifest-v8.json", "manifest_sha256")):
            digest = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
            self.assertEqual(digest, lock[key])
        self.assertEqual(manifest["data_fingerprint"], lock["data_fingerprint"])
        self.assertEqual(manifest["exact_prompt_overlap_count"], 0)
        self.assertTrue(lock["gold_not_in_agent_context"])

    def test_v8_preregistration_reserve_fits_global_cap(self):
        lock = json.loads((ROOT / "reports/enterprise-v8-model-preregistration.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["max_api_calls"], 40)
        self.assertLessEqual(lock["worst_case_reserve_usd"], lock["global_remaining_before_usd"])
        self.assertAlmostEqual(lock["global_remaining_after_reserve_usd"],
                               lock["global_remaining_before_usd"] - lock["worst_case_reserve_usd"])
        self.assertFalse(lock["old_ip4_touched"])


if __name__ == "__main__":
    unittest.main()
