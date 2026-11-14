import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_ip4_v13_offline import build_preflight, evaluate_no_verifier


ROOT = Path(__file__).resolve().parents[1]


class IP4V13OfflineAuditTests(unittest.TestCase):
    def test_no_verifier_development_arm_is_per_case_and_zero_call(self):
        report = evaluate_no_verifier()
        self.assertEqual("development_only", report["evaluation_role"])
        self.assertFalse(report["is_blind"])
        self.assertEqual(0, report["model_calls"])
        self.assertEqual(0, report["model_requests_sent"])
        self.assertEqual(0, report["verifier_invocations"])
        self.assertEqual(22, report["total"])
        self.assertEqual(22, len(report["cases"]))
        self.assertTrue(all(not row["independent_verifier_invoked"] and row["model_calls"] == 0
                            for row in report["cases"]))

    def test_offline_gate_pins_current_hashes_and_blocks_without_live_balance(self):
        ablation = evaluate_no_verifier()
        with tempfile.TemporaryDirectory() as directory:
            ablation_path = Path(directory) / "ablation.json"
            ablation_path.write_text(json.dumps(ablation), encoding="utf-8")
            gate = build_preflight(ablation, ablation_path)
        self.assertEqual("20261113-r4", gate["revision"])
        self.assertEqual("development_only", gate["evaluation_role"])
        self.assertFalse(gate["is_blind"])
        self.assertFalse(gate["model_evaluation_allowed"])
        self.assertEqual("blocked_pre_model_balance_unverified", gate["status"])
        self.assertEqual("passed", gate["offline_zero_call_gate"]["status"])
        self.assertEqual("blocked_live_balance_required", gate["provider_balance_gate"]["status"])
        self.assertFalse(gate["provider_balance_check_performed"])
        self.assertIsNone(gate["provider_balance_current"])
        self.assertEqual(0, gate["model_calls"])
        self.assertEqual(0, gate["model_requests_sent"])
        self.assertTrue(all(gate["checks"].values()))
        self.assertGreater(gate["old_lock_stale_artifact_keys"].count("agent_runtime_sha256"), 0)
        self.assertEqual(22, gate["planned_max_api_calls"])
        self.assertAlmostEqual(0.0468336, gate["current_run_reserve_usd"])
        self.assertAlmostEqual(0.0936672, gate["required_total_reserve_usd"])
        self.assertAlmostEqual(0.702504, gate["required_total_reserve_cny"])


if __name__ == "__main__":
    unittest.main()
