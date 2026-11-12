import json
import unittest
from pathlib import Path


class AttemptManifestTests(unittest.TestCase):
    def test_unknown_attempt_is_not_completed_or_zero_cost(self):
        path = Path(__file__).resolve().parents[1] / "reports/ip4-v13-attempt-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "attempt_incomplete_unknown")
        self.assertEqual(manifest["model_request_outcome"], "unknown_after_IncompleteRead")
        self.assertNotEqual(manifest["status"], "completed")
        self.assertIsNone(manifest["cost_usd"])
        self.assertFalse(manifest["sensitive_payloads_recorded"])


if __name__ == "__main__":
    unittest.main()
