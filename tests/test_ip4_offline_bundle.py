from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data.build_ip4_offline_bundle import build
from scripts.run_ip4_offline_audit import run


class IP4OfflineBundleTests(unittest.TestCase):
    def test_manifest_freezes_domain_inputs_gold_and_regenerated_database(self):
        with TemporaryDirectory(prefix="insightpilot-ip4-bundle-test-") as temp:
            bundle = Path(temp) / "bundle"
            manifest = build(bundle)
            visible = bundle / "agent-visible"
            visible_files = sorted(path.name for path in visible.iterdir())
            self.assertEqual(visible_files, ["marketing_roi-blind.jsonl", "refunds-blind.jsonl"])
            self.assertFalse(any(path.name.endswith("gold.jsonl") for path in visible.rglob("*")))
            self.assertFalse(manifest["agent_input_contract"]["gold_in_agent_visible_directory"])
            self.assertEqual(manifest["case_count"], 22)
            self.assertEqual(manifest["domains"]["refunds"]["case_count"], 12)
            self.assertEqual(manifest["domains"]["marketing_roi"]["case_count"], 10)
            for domain, files in manifest["domains"].items():
                blind = [json.loads(line) for line in (bundle / files["blind_path"])
                         .read_text(encoding="utf-8").splitlines()]
                gold = [json.loads(line) for line in (bundle / files["gold_path"])
                        .read_text(encoding="utf-8").splitlines()]
                self.assertTrue(all(set(row) == {"case_id", "question"} for row in blind))
                self.assertTrue(all(row["domain"] == domain for row in gold))
                self.assertEqual({row["case_id"] for row in blind}, {row["case_id"] for row in gold})
            self.assertEqual(build(bundle), manifest)

    def test_offline_audit_keeps_rule_and_no_verifier_failures(self):
        with TemporaryDirectory(prefix="insightpilot-ip4-audit-test-") as temp:
            root = Path(temp)
            bundle = root / "bundle"
            build(bundle)
            report = run(bundle=bundle, database=root / "v13.sqlite")
            self.assertEqual(report["status"], "pending_postgresql")
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["model_requests_sent"], 0)
            self.assertEqual(report["ruleplanner_sqlite"]["total"], 22)
            self.assertEqual(report["ruleplanner_sqlite"]["passed"], 15)
            self.assertEqual([row["case_id"] for row in report["ruleplanner_sqlite"]["failures"]],
                             [row["case_id"] for row in
                              report["ruleplanner_without_independent_verifier_sqlite"]["failures"]])
            self.assertEqual(report["metric_retrieval_candidate_coverage"]["total_answerable"], 14)
            self.assertEqual(report["model_ablation"]["status"],
                             "pending_explicit_authorization_and_provider_transport")


if __name__ == "__main__":
    unittest.main()
