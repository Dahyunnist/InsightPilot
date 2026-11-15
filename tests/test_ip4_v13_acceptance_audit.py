import ast
import contextlib
import hashlib
import io
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner
from scripts.audit_ip4_v13_acceptance import audit, main as run_audit
from scripts.run_ip4_v13_model_blind import build_dry_run_summary, main as run_model


ROOT = Path(__file__).resolve().parents[1]


def _method_sha(source: str, name: str) -> str:
    module = ast.parse(source)
    owner = next(node for node in module.body
                 if isinstance(node, ast.ClassDef) and node.name == "OpenAICompatiblePlanner")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    return hashlib.sha256(ast.get_source_segment(source, method).encode("utf-8")).hexdigest()


class IP4V13AcceptanceAuditTests(unittest.TestCase):
    def test_frozen_hashes_and_strata_are_reported_without_exposing_questions(self):
        report = audit()
        self.assertEqual(report["case_count"], 22)
        self.assertEqual(report["domain_counts"], {"marketing_roi": 10, "refunds": 12})
        self.assertEqual(report["expected_status_counts"], {"answered": 14, "clarify": 5, "refused": 3})
        self.assertEqual(report["ambiguity_guard_count"], 5)
        self.assertTrue(report["checks"]["frozen_v13_hashes_match_manifest_seal_and_lock"])
        self.assertTrue(report["checks"]["blind_gold_case_ids_and_blind_schema_match"])
        self.assertFalse(report["blind_questions_emitted"])
        self.assertFalse(report["blind_or_gold_modified"])
        self.assertNotIn("question", report)

    def test_failure_classification_and_budget_gate_are_distinct_from_model_score(self):
        report = audit()
        self.assertTrue(report["checks"]["prior_transport_failures_are_unscored"])
        self.assertEqual(report["failure_classification"]["attempted"], 15)
        self.assertEqual(report["failure_classification"]["provider_responses"], 0)
        self.assertEqual(report["failure_classification"]["transport_attempts_not_scored"], 15)
        self.assertIn("numeric_errors", report["failure_classification"][
            "scored_failure_classes_supported_by_runner"])
        self.assertEqual(report["failure_classification"]["observed_scored_model_failures"], 0)
        self.assertTrue(report["checks"]["budget_caps_and_zero_call_preflight_hold"])
        self.assertEqual(report["budget_gate"]["global_cap_usd"], 5.0)
        self.assertEqual(report["model_calls_this_audit"], 0)

    def test_missing_postgres_parity_stays_blocked(self):
        report = audit()
        self.assertFalse(report["checks"]["postgres_22_case_v13_gold_parity"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("postgres_22_case_v13_gold_parity", report["blockers"])
        self.assertIn("execution_code_lock_matches", report["blockers"])
        self.assertEqual({"planner_sha256", "model_runner_sha256", "query_runtime_sha256"},
                         set(report["execution_code_lock"]["mismatches"]))
        self.assertFalse(report["execution_code_lock"]["relock_allowed_without_rebaseline"])
        self.assertEqual(report["v14_model_run"],
                         "not_started_pending_owner_review_and_live_balance_preflight")

    def test_dry_run_budget_gate_rejects_over_cap_before_any_request(self):
        lock = {"status": "frozen_pre_model", "case_count": 22, "planned_max_api_calls": 22,
                "worst_case_reserve_usd": 0.1873344, "global_cap_usd": 5.0}
        accepted = build_dry_run_summary("offline-audit", "unused.json", lock)
        self.assertEqual(accepted["status"], "dry_preflight_passed")
        self.assertEqual(accepted["model_requests_sent"], 0)

        lock["worst_case_reserve_usd"] = 0.1873345
        with self.assertRaisesRegex(SystemExit, "exceeds approved question or budget limits"):
            build_dry_run_summary("offline-audit", "unused.json", lock)

    def test_audit_command_writes_classified_blocked_report_and_exits_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "audit.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                run_audit(report_path)
            self.assertEqual(2, raised.exception.code)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual("blocked", report["status"])
            self.assertIn("postgres_22_case_v13_gold_parity", report["blockers"])
            self.assertIn("execution_code_lock_matches", report["blockers"])
            self.assertEqual(0, report["model_calls_this_audit"])
            self.assertNotIn("question", report)

    def test_stale_runtime_lock_stops_before_balance_or_model_request(self):
        lock_path = Path("reports/ip4-v13-model-preregistration-20261114-r5.json")
        lock_before = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "must-not-exist.json"
            argv = ["runner", "--run-id", "locked-audit", "--report", str(report_path),
                    "--preregistration", str(lock_path)]
            with patch.object(sys, "argv", argv), \
                    patch("scripts.run_ip4_v13_model_blind.check_provider_balance",
                          side_effect=AssertionError("stale lock must stop before balance check")), \
                    self.assertRaisesRegex(SystemExit,
                                           "registered scorer, planner, runner, builder, or baseline changed"):
                run_model()
            self.assertFalse(report_path.exists())
        self.assertEqual(lock_before, hashlib.sha256(lock_path.read_bytes()).hexdigest())

    def test_m09_new_preflight_clarifies_before_any_model_call(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        question = "2026-10-01 至 2026-10-31 营销净 ROI 和毛 ROI 分别是多少？"
        with patch.object(OpenAICompatiblePlanner, "_complete",
                          side_effect=AssertionError("M09 must stop before model call")) as complete:
            with self.assertRaisesRegex(NeedsClarification, "只定义营销净 ROI"):
                planner.plan(question)
        self.assertEqual(complete.call_count, 0)
        self.assertEqual(planner.last_call_count, 0)

    def test_r5_prompt_builder_and_scorer_are_stable_while_plan_path_changed(self):
        lock_path = ROOT / "reports/ip4-v13-model-preregistration-20261114-r5.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        frozen_source = subprocess.check_output(
            ["git", "show", f"{lock['freeze_commit_sha']}:insightpilot/planner.py"],
            cwd=ROOT, text=True, encoding="utf-8")
        current_source = (ROOT / "insightpilot/planner.py").read_text(encoding="utf-8")
        self.assertEqual(_method_sha(frozen_source, "_complete"), _method_sha(current_source, "_complete"))
        self.assertNotEqual(_method_sha(frozen_source, "plan"), _method_sha(current_source, "plan"))
        scorer_sha = hashlib.sha256((ROOT / "insightpilot/enterprise_evaluate.py").read_bytes()).hexdigest()
        self.assertEqual(lock["artifacts"]["scorer_sha256"], scorer_sha)


if __name__ == "__main__":
    unittest.main()
