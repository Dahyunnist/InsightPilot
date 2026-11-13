import json
import unittest
from pathlib import Path


class IP3DockerSmokeReportTests(unittest.TestCase):
    def test_20261113_attempt_records_registry_block_without_claiming_rls(self):
        root = Path(__file__).resolve().parents[1]
        ip3 = json.loads((root / "reports/ip3-docker-smoke-20261113.json").read_text(encoding="utf-8"))
        rls = json.loads((root / "reports/ip1-rls-attempt-20261113.json").read_text(encoding="utf-8"))
        self.assertEqual(ip3["status"], "blocked_before_container_start")
        self.assertFalse(ip3["pull_build_completed"])
        self.assertFalse(ip3["container_started"])
        self.assertFalse(ip3["postgresql_or_rls_covered"])
        self.assertEqual(rls["status_after_attempt"], "pending_host_verification")
        self.assertFalse(rls["rls_verification_started"])
        self.assertFalse(rls["volume_deleted"])

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
