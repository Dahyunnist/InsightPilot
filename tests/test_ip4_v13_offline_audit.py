import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.audit_ip4_v13_offline as offline_audit
from scripts.audit_ip4_v13_offline import build_preflight, evaluate_no_verifier


ROOT = Path(__file__).resolve().parents[1]


class IP4V13OfflineAuditTests(unittest.TestCase):
    def test_r5_transport_failures_are_not_scored_as_model_results(self):
        report = json.loads((ROOT / "reports/ip4-v13-model-evaluation-r5.json").read_text(encoding="utf-8-sig"))
        self.assertEqual("partial_transport_blocked", report["status"])
        self.assertEqual(15, report["model_requests_attempted"])
        self.assertEqual(0, report["provider_responses_received"])
        self.assertEqual("0/14", report["model_answerable_outputs"])
        self.assertEqual("7/8; RulePlanner preflight only, not model output",
                         report["deterministic_guard_result"])
        self.assertIsNone(report["confirmed_actual_cost_usd"])
        self.assertEqual(0.031932, report["peak_price_worst_case_reserve_usd"])
        transport_rows = [attempt for case in report["cases"] for attempt in case["attempts"]
                          if attempt.get("evaluation_disposition") == "not_scored_transport_error"]
        self.assertEqual(15, len(transport_rows))
        self.assertTrue(all(attempt["score"]["passed"] is None for attempt in transport_rows))

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
            ledger_path = Path(directory) / "ledger.json"
            ledger_path.write_bytes((ROOT / "reports/model-experiment-cost-ledger.json").read_bytes())
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["ip4_v13_reservation"]["status"] = "reserved"
            ledger["ip4_v13_reservation"]["worst_case_reserve_usd"] = 0.0936672
            ledger["ip4_v13_reservation"]["blind_sha256"] = ablation["questions_sha256"]
            ledger["ip4_v13_reservation"]["gold_sha256"] = ablation["gold_sha256"]
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            with patch.object(offline_audit, "LEDGER", ledger_path):
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
