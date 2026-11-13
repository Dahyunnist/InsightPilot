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

    def test_blocked_report_contract_never_turns_unknown_cost_or_calls_into_success(self):
        manifest = json.loads((ROOT / "reports/ip4-v13-attempt-manifest.json").read_text(encoding="utf-8"))
        runner = (ROOT / "scripts/run_ip4_v13_model_blind.py").read_text(encoding="utf-8")
        self.assertEqual(manifest["status"], "blocked_by_review")
        self.assertIsNone(manifest["provider_received"])
        self.assertIsNone(manifest["cost_usd"])
        self.assertNotEqual(manifest["status"], "completed")
        self.assertIn("frozen v13 artifact hash mismatch; no request sent", runner)
        self.assertIn("report_path.write_text", runner)


if __name__ == "__main__":
    unittest.main()

