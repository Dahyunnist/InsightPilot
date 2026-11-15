import tempfile
import unittest
from pathlib import Path

from scripts.run_ip4_v13_model_blind import resolve_run_identity


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


if __name__ == "__main__":
    unittest.main()
