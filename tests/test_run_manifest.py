import unittest
from datetime import datetime, timezone

from insightpilot.run_manifest import validate_run_manifest


class RunManifestTests(unittest.TestCase):
    def valid(self):
        now = datetime.now(timezone.utc).isoformat()
        return {"schema_version": 1, "project": "InsightPilot", "git_sha": "a" * 40,
                "dataset_id": "synthetic-v1", "dataset_sha256": "b" * 64, "split": "test",
                "model_id": "none", "prompt_sha": None, "tool_schema_sha": None, "runtime_digest": None,
                "max_steps": 1, "max_tokens": None, "scorer_sha": "c" * 64,
                "gold_source": "independent", "cost_usd_estimate": 0.0, "run_id": "run-1",
                "trace_id": "trace-1", "started_at": now, "ended_at": now, "status": "completed",
                "evaluation_id": "baseline-v1", "domain": "synthetic", "truth_sha256": "d" * 64,
                "result_fingerprint_sha256": "e" * 64}

    def test_valid_no_model_manifest(self):
        validate_run_manifest(self.valid())

    def test_invalid_hash_cost_status_and_missing_field_fail_closed(self):
        for key, value in (("dataset_sha256", "bad"), ("cost_usd_estimate", None),
                           ("status", "blocked_by_review")):
            with self.subTest(key=key):
                manifest = self.valid()
                manifest[key] = value
                with self.assertRaises(ValueError):
                    validate_run_manifest(manifest)
        manifest = self.valid()
        del manifest["truth_sha256"]
        with self.assertRaises(ValueError):
            validate_run_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
