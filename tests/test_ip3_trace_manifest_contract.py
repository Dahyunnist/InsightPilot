import json
import unittest
from pathlib import Path

from insightpilot.tracing import _JSONL_EVENTS, _TRACE_STAGES, _TRACE_STATUSES


ROOT = Path(__file__).resolve().parents[1]


class TraceManifestContractTests(unittest.TestCase):
    def test_trace_events_and_blocked_attempt_manifest_keep_distinct_valid_contracts(self):
        trace_source = (ROOT / "insightpilot/tracing.py").read_text(encoding="utf-8")
        self.assertTrue({"analysis.started", "analysis.completed", "analysis.failed"}.issubset(_JSONL_EVENTS))
        self.assertTrue({"planning", "query", "verification"}.issubset(_TRACE_STAGES))
        self.assertIn("cancelled", _TRACE_STATUSES)
        self.assertIn("run_id", trace_source)
        manifest = json.loads((ROOT / "reports/ip4-v13-attempt-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "blocked_by_review")
        self.assertIsNone(manifest["provider_received"])
        self.assertIsNone(manifest["cost_usd"])
        self.assertIsNone(manifest["completed_at_utc"])


if __name__ == "__main__":
    unittest.main()
