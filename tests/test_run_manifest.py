import unittest
import json
from datetime import datetime, timezone
from pathlib import Path

from insightpilot.run_manifest import validate_public_run_manifest, validate_run_manifest, write_validated_reports
from scripts.normalize_ip4_public_manifests import (audit_r3_legacy_reservation,
                                                    build_public_views, normalize_baseline,
                                                    normalize_blocked_attempt)
from scripts.migrate_ip4_r3_legacy_manifest import build as build_r3_migration


class RunManifestTests(unittest.TestCase):
    def valid(self):
        now = datetime.now(timezone.utc).isoformat()
        return {"schema_version": 1, "project": "InsightPilot", "git_sha": "a" * 40,
                "dataset_id": "synthetic-v1", "dataset_sha256": "b" * 64, "split": "test",
                "model_id": "none", "prompt_sha": None, "tool_schema_sha": None, "runtime_digest": None,
                "max_steps": 1, "max_tokens": None, "scorer_sha": "c" * 64,
                "gold_source": "independent", "cost_usd_estimate": 0.0, "run_id": "run-1",
                "trace_id": "trace-1", "started_at": now, "ended_at": now, "status": "completed",
                "evaluation_id": "baseline-v1", "domain": "synthetic", "truth_sha256": "d" * 64,
                "result_fingerprint_sha256": "e" * 64}

    def test_valid_no_model_manifest(self):
        validate_run_manifest(self.valid())

    def test_invalid_hash_cost_status_and_missing_field_fail_closed(self):
        for key, value in (("dataset_sha256", "bad"), ("cost_usd_estimate", None),
                           ("status", "blocked_by_review")):
            with self.subTest(key=key):
                manifest = self.valid()
                manifest[key] = value
                with self.assertRaises(ValueError):
                    validate_run_manifest(manifest)
        manifest = self.valid()
        del manifest["truth_sha256"]
        with self.assertRaises(ValueError):
            validate_run_manifest(manifest)

    def test_invalid_manifest_writes_no_report(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.json"
            invalid = self.valid()
            invalid["cost_usd_estimate"] = None
            with self.assertRaises(ValueError):
                write_validated_reports(invalid, {target: {"result": "synthetic"}})
            self.assertFalse(target.exists())

    def test_v2_normalizes_rule_and_blocked_attempt_without_breaking_v1(self):
        baseline, attempt, coverage = build_public_views()
        root = Path(__file__).resolve().parents[1]
        source_v1 = json.loads((root / "reports/ip4-v13-rule-baseline-manifest.json").read_text(encoding="utf-8"))
        validate_run_manifest(source_v1)
        self.assertEqual([], validate_public_run_manifest(baseline))
        self.assertEqual([], validate_public_run_manifest(attempt))
        self.assertEqual(2, baseline["schema_version"])
        self.assertEqual("none", baseline["model_id"])
        self.assertEqual(0, baseline["model_calls"])
        self.assertFalse(baseline["model_input_received"])
        self.assertEqual("completed", baseline["status"])
        self.assertEqual("failed", baseline["evaluation_outcome"])
        self.assertEqual("none", attempt["model_id"])
        self.assertEqual("deepseek-v4-flash", attempt["planned_model_id"])
        self.assertEqual(0, attempt["model_calls"])
        self.assertFalse(attempt["model_input_received"])
        self.assertIsNone(attempt["run_id"])
        self.assertEqual("blocked", attempt["field_status"]["run_id"])
        self.assertEqual("blocked", attempt["status"])
        self.assertEqual(6, len(coverage["vocabularies"]["shared_v2"]["events"]))
        self.assertTrue(all(item["count"] == 0 and item["coverage"] == "not_covered"
                            for item in coverage["vocabularies"]["shared_v2"]["observed"].values()))
        self.assertNotIn("unknown", json.dumps((baseline, attempt, coverage)).casefold())
        stored = json.loads((root / "reports/ip4-v13-rule-baseline-public-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline, stored)
        stored_attempt = json.loads((root / "reports/ip4-v13-attempt-2026-11-13-r3-public-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(attempt, stored_attempt)
        stored_coverage = json.loads((root / "reports/ip4-v13-event-vocabulary-coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(coverage, stored_coverage)
        one_native_event = normalize_baseline(events=[{"event": "analysis.started"}])
        self.assertEqual(1, one_native_event["native_event_coverage"]["analysis.started"]["count"])
        self.assertEqual(0, one_native_event["trace_event_coverage"]["timeout"]["count"])
        one_shared_event = normalize_baseline(events=[{"event_type": "timeout"}])
        self.assertEqual(1, one_shared_event["trace_event_coverage"]["timeout"]["count"])

    def test_public_manifest_rejects_private_fields_unknown_and_planned_model_as_actual(self):
        import copy
        baseline, attempt, _ = build_public_views()
        private = copy.deepcopy(baseline)
        private["raw_prompt"] = "synthetic question"
        self.assertTrue(any("sensitive field" in error for error in validate_public_run_manifest(private)))
        unknown = copy.deepcopy(baseline)
        unknown["runtime"]["python"] = "unknown"
        self.assertTrue(any("unknown placeholder" in error for error in validate_public_run_manifest(unknown)))
        mislabeled = copy.deepcopy(attempt)
        mislabeled["model_id"] = mislabeled["planned_model_id"]
        self.assertIn("planned model cannot be presented as actual model without calls",
                      validate_public_run_manifest(mislabeled))
        secret = copy.deepcopy(baseline)
        secret["access_token"] = "synthetic-secret"
        self.assertTrue(any("sensitive field" in error for error in validate_public_run_manifest(secret)))
        private_prompt = copy.deepcopy(baseline)
        private_prompt["prompt"] = "synthetic question"
        self.assertTrue(any("sensitive field" in error for error in validate_public_run_manifest(private_prompt)))

    def test_public_null_requires_blocked_or_not_applicable_state(self):
        import copy
        _, attempt, _ = build_public_views()
        invalid = copy.deepcopy(attempt)
        invalid["field_status"].pop("trace_id")
        self.assertTrue(any("trace_id" in error for error in validate_public_run_manifest(invalid)))

    def test_forward_r3_reservation_audit_preserves_mismatch_and_fail_closed_status(self):
        attempt = {"status": "blocked_pre_model_balance_unverified", "prior_preregistration_sha256": "a" * 64,
                   "question_set_sha256": "b" * 64, "gold_sha256": "c" * 64, "prior_reservation_usd": 0.1}
        lock = {"worst_case_reserve_usd": 0.1,
                "artifacts": {"blind_sha256": "b" * 64, "gold_sha256": "c" * 64,
                              "cost_ledger_sha256_before_reservation": "d" * 64}}
        ledger = {"ip4_v13_reservation": {"preregistration_sha256": "a" * 64},
                  "ip4_v13_historical_reservation": {"preregistration_sha256": "a" * 64,
                                                      "worst_case_reserve_usd": 0.1}}
        matched = audit_r3_legacy_reservation(attempt, lock, ledger,
                                              attempt_lock_sha256="a" * 64, lock_file_sha256="a" * 64)
        self.assertEqual("verified", matched["status"])
        ledger["ip4_v13_historical_reservation"]["preregistration_sha256"] = "e" * 64
        mismatched = audit_r3_legacy_reservation(attempt, lock, ledger,
                                                 attempt_lock_sha256="a" * 64, lock_file_sha256="a" * 64)
        self.assertEqual("legacy_history_inconsistent", mismatched["status"])
        self.assertEqual("a" * 64, mismatched["attempt_referenced_preregistration_sha256"])
        self.assertEqual("e" * 64, mismatched["ledger_historical_preregistration_sha256"])

    def test_r3_forward_migration_is_repeatable_read_only_and_does_not_relax_normalizer(self):
        root = Path(__file__).resolve().parents[1]
        source_paths = [root / "reports/ip4-v13-attempt-manifest-2026-11-13-r3.json",
                        root / "reports/ip4-v13-model-preregistration-20261113-r2.json",
                        root / "reports/model-experiment-cost-ledger.json"]
        before = [path.read_bytes() for path in source_paths]
        first, second = build_r3_migration(), build_r3_migration()
        self.assertEqual(first, second)
        audit = first["migration_audit"]
        self.assertEqual("legacy_history_inconsistent", audit["status"])
        self.assertEqual(audit["attempt_referenced_preregistration_sha256"],
                         audit["r2_lock_file_sha256"])
        self.assertNotEqual(audit["attempt_referenced_preregistration_sha256"],
                            audit["ledger_historical_preregistration_sha256"])
        self.assertTrue(first["historical_records_modified"] is False)
        self.assertEqual(before, [path.read_bytes() for path in source_paths])
        with self.assertRaisesRegex(ValueError, "reservation"):
            normalize_blocked_attempt()

    def test_public_manifest_accepts_explicit_request_and_cost_uncertainty(self):
        root = Path(__file__).resolve().parents[1]
        baseline = json.loads((root / "reports/ip4-v13-rule-baseline-public-manifest.json").read_text(encoding="utf-8"))
        baseline["request_outcome"] = {"attempted": 15, "provider_responses_received": 0,
                                        "cost_status": "not_applicable"}
        self.assertEqual([], validate_public_run_manifest(baseline))
        baseline["request_outcome"]["provider_responses_received"] = 16
        self.assertIn("provider responses cannot exceed attempted requests",
                      validate_public_run_manifest(baseline))


if __name__ == "__main__":
    unittest.main()
