import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StaleLockFailClosedTests(unittest.TestCase):
    def test_stale_runtime_hashes_are_detected_before_network_balance_check(self):
        lock = json.loads((ROOT / "reports/ip4-v13-model-preregistration.json").read_text(encoding="utf-8"))
        runner = (ROOT / "scripts/run_ip4_v13_model_blind.py").read_text(encoding="utf-8")
        prereg = (ROOT / "scripts/preregister_ip4_v13_model.py").read_text(encoding="utf-8")
        self.assertNotEqual(lock["artifacts"]["agent_runtime_sha256"],
                            hashlib.sha256((ROOT / "insightpilot/agent.py").read_bytes()).hexdigest())
        self.assertIn("artifact hash mismatch", runner)
        self.assertLess(runner.index("artifact hash mismatch"), runner.rindex("check_provider_balance("))
        self.assertIn("provider_balance(", prereg)
        self.assertIn("status\": \"blocked_by_review\"", (ROOT / "reports/ip4-v13-attempt-manifest.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

