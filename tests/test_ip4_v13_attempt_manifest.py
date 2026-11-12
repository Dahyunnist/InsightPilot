import json
import unittest
from pathlib import Path


class AttemptManifestTests(unittest.TestCase):
    def test_review_blocked_attempt_is_not_completed_or_zero_cost(self):
        path = Path(__file__).resolve().parents[1] / "reports/ip4-v13-attempt-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "blocked_by_review")
        self.assertIsNone(manifest["provider_received"])
        self.assertNotEqual(manifest["status"], "completed")
        self.assertIsNone(manifest["cost_usd"])
        self.assertFalse(manifest["sensitive_payloads_recorded"])

    def test_blocked_review_audit_keeps_receipt_and_cost_unknown(self):
        path = Path(__file__).resolve().parents[1] / "reports/ip4-v13-attempt-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "blocked_by_review")
        self.assertIsNone(manifest["provider_received"])
        self.assertIsNone(manifest["cost_usd"])


if __name__ == "__main__":
    unittest.main()
