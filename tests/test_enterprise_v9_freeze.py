import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EnterpriseV9FreezeTests(unittest.TestCase):
    def test_v9_artifact_hashes_and_seed_match_preregistration(self):
        lock = json.loads((ROOT / "reports/enterprise-v9-model-preregistration.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "evals/enterprise-holdout-manifest-v9.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["seed"], 20261024)
        self.assertEqual(lock["exact_prompt_overlap_count"], 0)
        for path, key in (("evals/enterprise-blind-v9.jsonl", "blind_sha256"),
                          ("evals/enterprise-gold-v9.jsonl", "gold_sha256"),
                          ("evals/enterprise-holdout-manifest-v9.json", "manifest_sha256")):
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), lock[key])
        self.assertEqual(manifest["data_fingerprint"], lock["data_fingerprint"])
        self.assertTrue(lock["gold_not_in_agent_context"])

    def test_v9_full_request_reserve_fits_authorized_five_dollar_cap(self):
        lock = json.loads((ROOT / "reports/enterprise-v9-model-preregistration.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["global_cap_usd"], 5.0)
        self.assertEqual(lock["max_api_calls"], 40)
        self.assertLessEqual(lock["worst_case_reserve_usd"], lock["global_remaining_before_usd"])
        self.assertAlmostEqual(lock["global_remaining_after_reserve_usd"],
                               lock["global_remaining_before_usd"] - lock["worst_case_reserve_usd"])
        self.assertFalse(lock["old_ip4_touched"])


if __name__ == "__main__":
    unittest.main()
