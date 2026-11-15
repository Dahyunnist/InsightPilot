import tempfile
import unittest
import json
import contextlib
import os
import io
import sys
from pathlib import Path
from unittest.mock import patch

from scripts.run_ip4_v13_model_blind import main, resolve_run_identity


class RunIdentityTests(unittest.TestCase):
    def test_default_run_identity_has_unique_nonexisting_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_id, first_report = resolve_run_identity(None, None, root)
            second_id, second_report = resolve_run_identity(None, None, root)
            self.assertNotEqual(first_id, second_id)
            self.assertNotEqual(first_report, second_report)
            self.assertFalse(first_report.exists())

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "existing.json"
            report.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                resolve_run_identity("approved-run-1", Path("existing.json"), root)
            self.assertEqual("preserve", report.read_text(encoding="utf-8"))

    def test_run_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "safe filename"):
                resolve_run_identity("../unsafe", None, Path(directory))

    def test_dry_run_stale_lock_stops_before_evaluation_files_or_network(self):
        for status in ("partial_transport_blocked", "frozen_pre_model"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                lock = {
                    "status": status, "case_count": 22, "planned_max_api_calls": 22,
                    "worst_case_reserve_usd": 0.0936672, "global_cap_usd": 5.0,
                }
                lock_path = root / "lock.json"
                lock_path.write_text(json.dumps(lock), encoding="utf-8")
                report_path = root / "run.json"
                stdout = io.StringIO()
                original_read_text = Path.read_text

                def guarded_read(path, *args, **kwargs):
                    if "ip4-v13-blind.jsonl" in str(path) or "ip4-v13-gold.jsonl" in str(path):
                        raise AssertionError("dry-run touched evaluation inputs")
                    return original_read_text(path, *args, **kwargs)

                with patch.object(sys, "argv", ["runner", "--dry-run", "--run-id", "offline-run",
                                                  "--preregistration", str(lock_path), "--report", str(report_path)]), \
                        patch.object(Path, "read_text", guarded_read), \
                        patch("scripts.run_ip4_v13_model_blind.check_provider_balance",
                              side_effect=AssertionError("dry-run attempted network")), \
                        contextlib.redirect_stdout(stdout):
                    main()

                result = json.loads(stdout.getvalue())
                self.assertEqual(0, result["model_requests_sent"])
                self.assertEqual("offline-run", result["run_id"])
                self.assertEqual(os.path.normcase(str(report_path)),
                                 os.path.normcase(result["report_path"]))
                self.assertFalse(report_path.exists())
                if status == "frozen_pre_model":
                    self.assertEqual("dry_preflight_passed", result["status"])
                    self.assertEqual(22, result["case_count"])
                    self.assertEqual(22, result["max_actual_model_calls"])
                    self.assertLessEqual(result["reserved_worst_case_usd"], 0.1873344)
                    self.assertIn("gold", result["excludes"])
                else:
                    self.assertEqual("blocked_stale_lock", result["status"])


if __name__ == "__main__":
    unittest.main()
