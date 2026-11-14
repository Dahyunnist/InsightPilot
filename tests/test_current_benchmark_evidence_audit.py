import unittest

from scripts.audit_current_benchmark_evidence import audit


class CurrentBenchmarkEvidenceAuditTests(unittest.TestCase):
    def test_answered_cases_have_consistent_evidence_bundles(self):
        report = audit()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["answered_cases"], 14)
        self.assertEqual(report["complete_evidence_bundles"], 14)
        self.assertEqual(report["checks_passed_by_case_count"]["definition_source_matches"], 14)
        self.assertEqual(report["checks_passed_by_case_count"]["source_heading_exists"], 14)
        self.assertEqual(report["checks_passed_by_case_count"]["lineage_matches_catalog"], 14)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["catalog_source_commit"], "117cebb3743a6e8cdffebf5894c77101d61c2a81")


if __name__ == "__main__":
    unittest.main()
