import json
import unittest
from pathlib import Path


class IP3DockerSmokeReportTests(unittest.TestCase):
    def test_report_preserves_partial_success_and_scope_limits(self):
        report = json.loads((Path(__file__).resolve().parents[1] /
                             "reports/ip3-docker-smoke-20261112.json").read_text(encoding="utf-8"))
        pressure = {row["concurrency"]: row for row in report["results"]}
        self.assertEqual((pressure[20]["http_200"], pressure[20]["http_429"], pressure[20]["http_503"]), (8, 12, 0))
        self.assertTrue(pressure[20]["p95_includes_rejections"])
        self.assertEqual(report["model_calls"], 0)
        self.assertFalse(report["postgresql_or_rls_covered"])
        self.assertEqual(report["idempotency"]["conflict_status"], 409)


if __name__ == "__main__":
    unittest.main()
