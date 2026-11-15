import hashlib
import json
import unittest
from pathlib import Path

from insightpilot.run_evidence import MINIMUM_FIELDS, audit_record_evidence, project_record_evidence


ROOT = Path(__file__).resolve().parents[1]


class RunEvidenceProjectionTests(unittest.TestCase):
    def test_projects_actual_ip1_to_ip4_sources_with_explicit_missing_values(self):
        records = {row["record_id"]: row for row in project_record_evidence(ROOT)}
        self.assertEqual(len(records), 5)
        for row in records.values():
            self.assertTrue(set(MINIMUM_FIELDS) <= row.keys())
            self.assertTrue(row["stages"])
            self.assertTrue(row["record_kind"])

        host = records["ip1-ip3-host-validation"]
        self.assertEqual(host["run_id"], "ip1-ip3-host-2026-11-15-090505-0ce3b8a2")
        self.assertIsNone(host["dataset_sha256"])
        self.assertEqual(host["provider_requests_attempted"], 0)

        dev = records["ip2-boundary-development-v1"]
        self.assertEqual(dev["record_kind"], "development_manifest")
        self.assertEqual(dev["status"], "not_run")
        self.assertIsNone(dev["run_id"])

        otel = records["ip3-otel-acceptance-2026-11-11"]
        self.assertEqual(otel["trace_id"], "fd7d77f37052870d603aaceaa04a92b7")
        self.assertEqual(otel["span_id"], "0573c423078c3ff6")
        self.assertIsNone(otel["run_id"])

        rule = records["ip4-v13-rule-baseline"]
        self.assertIsNone(rule["trace_id"])
        self.assertEqual(rule["legacy_source_derived_trace_id"], "c6e6ec988d17b645f1b75886d5097a39")

        r5 = records["ip4-v13-r5-transport"]
        self.assertEqual(r5["provider_requests_attempted"], 15)
        self.assertEqual(r5["provider_responses_received"], 0)
        self.assertIsNone(r5["cost_usd_estimate"])
        self.assertEqual(r5["cost_status"], "unconfirmed")
        self.assertEqual(r5["delivery_status"], "unknown")

    def test_audit_is_offline_and_does_not_change_frozen_sources(self):
        frozen = [ROOT / "evals/ip4-v13-blind.jsonl", ROOT / "evals/ip4-v13-gold.jsonl",
                  ROOT / "evals/ip4-v13-manifest.json"]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen}
        audit = audit_record_evidence(ROOT)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen}
        self.assertTrue(audit["offline_only"])
        self.assertEqual(before, after)
        self.assertEqual(audit["summary"]["record_count"], 5)
        self.assertEqual(audit["summary"]["real_trace_records"], ["ip3-otel-acceptance-2026-11-11"])

    def test_audit_json_is_serializable(self):
        json.dumps(audit_record_evidence(ROOT), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
