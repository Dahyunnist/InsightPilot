import unittest

from scripts.audit_current_benchmark_evidence import audit


class CurrentBenchmarkEvidenceAuditTests(unittest.TestCase):
    def test_answered_cases_have_consistent_evidence_bundles(self):
        report = audit()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["answered_cases"], 14)
        self.assertEqual(report["complete_evidence_bundles"], 0)
        self.assertEqual(report["checks_passed_by_case_count"]["definition_source_matches"], 14)
        self.assertEqual(report["checks_passed_by_case_count"]["source_heading_exists"], 14)
        self.assertEqual(report["checks_passed_by_case_count"]["lineage_matches_catalog"], 0)
        self.assertEqual(report["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
