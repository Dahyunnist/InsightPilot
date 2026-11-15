import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from insightpilot.run_evidence import (
    MINIMUM_FIELDS,
    audit_record_evidence,
    build_ip3_otel_run_evidence,
    project_record_evidence,
    validate_ip3_otel_run_evidence,
)
from scripts.verify_ip3_otel_collector import _new_report_path


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

    def test_future_ip3_record_uses_observed_ids_and_hashes(self):
        evidence = build_ip3_otel_run_evidence(
            ROOT,
            git_sha="a" * 40,
            analysis_response={"body": {"run_id": "run-observed"}},
            trace_summary={"span_links": [{"name": "insightpilot.api.analysis",
                                            "trace_id": "b" * 32, "span_id": "c" * 16}]},
            runtime_facts={"python": "3.12.1", "platform": "linux",
                           "api_image_id": "sha256:" + "d" * 64,
                           "collector_image_id": "sha256:" + "f" * 64},
            dataset_sha256="e" * 64,
        )
        self.assertEqual(evidence["run_id"], "run-observed")
        self.assertEqual(evidence["trace_id"], "b" * 32)
        self.assertEqual(evidence["span_id"], "c" * 16)
        self.assertEqual(len(evidence["runtime_digest"]), 64)
        self.assertEqual(evidence["dataset_sha256"], "e" * 64)
        self.assertIn("seed", evidence["source_sha256"])
        self.assertIsNone(evidence["prompt_sha"])
        self.assertIsNone(evidence["tool_schema_sha"])
        self.assertIsNone(evidence["scorer_sha"])
        self.assertEqual(evidence["field_status"]["prompt_sha"], "not_applicable")
        self.assertEqual(evidence["field_status"]["tool_schema_sha"], "not_applicable")
        self.assertEqual(evidence["field_status"]["scorer_sha"], "not_applicable")
        self.assertEqual(evidence["provider_requests_attempted"], 0)
        self.assertEqual(evidence["provider_responses_received"], 0)
        self.assertEqual(evidence["cost_status"], "not_applicable")
        self.assertEqual(validate_ip3_otel_run_evidence(evidence), [])

        invalid = dict(evidence, provider_responses_received=1)
        self.assertTrue(any("cannot exceed" in error for error in validate_ip3_otel_run_evidence(invalid)))

    def test_missing_future_evidence_stays_null_with_reasons(self):
        evidence = build_ip3_otel_run_evidence(
            ROOT, git_sha=None, analysis_response=None, trace_summary=None,
            runtime_facts=None, dataset_sha256=None)
        for field in ("git_sha", "run_id", "trace_id", "span_id", "dataset_sha256", "runtime_digest"):
            self.assertIsNone(evidence[field])
            self.assertEqual(evidence["field_status"][field], "not_recorded")
            self.assertIn(field, evidence["missing_field_reasons"])
        self.assertEqual(evidence["provider_requests_attempted"], 0)
        self.assertEqual(evidence["provider_responses_received"], 0)
        self.assertEqual(evidence["cost_usd_estimate"], 0)
        self.assertEqual(evidence["cost_status"], "not_applicable")

    def test_projection_discovers_new_immutable_ip3_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_names = [
                "host-validation-ip1-ip3-2026-11-15/run-manifest.json",
                "ip2-boundary-development-manifest-v1.json",
                "ip3-otel-collector.json",
                "ip4-v13-rule-baseline-manifest.json",
                "ip4-r5-transport-audit-2026-11-15.json",
                "ip4-v13-model-preregistration-20261114-r5.json",
            ]
            for relative in report_names:
                source = ROOT / "reports" / relative
                target = root / "reports" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            future = json.loads((root / "reports/ip3-otel-collector.json").read_text(encoding="utf-8"))
            future["run_evidence"] = build_ip3_otel_run_evidence(
                ROOT, git_sha="a" * 40, analysis_response={"body": {"run_id": "new-run"}},
                trace_summary={"span_links": [{"name": "insightpilot.api.analysis",
                                                "trace_id": "b" * 32, "span_id": "c" * 16}]},
                runtime_facts={"python": "3.12"}, dataset_sha256="d" * 64)
            future_path = root / "reports/ip3-otel-collector-run-20261115T010203.json"
            future_path.write_text(json.dumps(future), encoding="utf-8")

            records = project_record_evidence(root)
            projected = next(row for row in records if row["record_id"] == future_path.stem)
            self.assertEqual(projected["run_id"], "new-run")
            self.assertEqual(projected["trace_id"], "b" * 32)
            self.assertEqual(projected["dataset_sha256"], "d" * 64)
            self.assertEqual(projected["provider_requests_attempted"], 0)
            self.assertEqual(projected["cost_status"], "not_applicable")

    def test_new_otel_run_never_overwrites_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "ip3-otel-collector.json"
            base.write_text("historical", encoding="utf-8")
            next_path = _new_report_path(base, "2026-11-15T01:02:03+00:00")
            self.assertNotEqual(next_path, base)
            self.assertFalse(next_path.exists())
            self.assertEqual(base.read_text(encoding="utf-8"), "historical")


if __name__ == "__main__":
    unittest.main()
