import json
import hashlib
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/ip4-retrieval-ablation-model-2026-11-16-r2.json"
FREEZE = ROOT / "reports/ip4-retrieval-ablation-freeze-2026-11-16.json"
LEDGER = ROOT / "reports/model-experiment-cost-ledger.json"
AUDIT = ROOT / "reports/ip4-retrieval-ablation-audit-2026-11-16.json"
RUN_MANIFEST = ROOT / "reports/ip4-retrieval-ablation-run-manifest-2026-11-16-r2.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_host_report_matches_frozen_inputs_and_sanitized_payload_boundary():
    report = _read(REPORT)
    freeze = _read(FREEZE)

    assert report["status"] == "completed"
    assert report["suite_id"] == freeze["suite_id"]
    assert report["manifest_sha256"] == freeze["manifest_sha256"]
    assert report["blind_sha256"] == freeze["blind_sha256"]
    assert report["gold_sha256_local_only"] == freeze["gold_sha256_local_only"]
    assert report["data_fingerprint"] == freeze["data_fingerprint"]
    assert report["request_payload_fields"] == [
        "question", "candidate_metrics", "allowed_metric_ids"
    ]
    assert report["request_excludes_case_id_gold_expected_status_database_rows_sql_and_query_results"] is True

    forbidden = {"question", "gold", "expected_status", "database_rows", "sql", "query_results", "raw_response"}
    assert not forbidden.intersection(report)
    for arm in report["arms"]:
        for case in arm["cases"]:
            assert not forbidden.intersection(case)


def test_report_calls_scores_failures_and_ledger_reconcile():
    report = _read(REPORT)
    ledger = _read(LEDGER)["ip4_retrieval_ablation_model"]
    arms = {arm["arm"]: arm for arm in report["arms"]}

    assert report["planned_case_runs"] == 44
    assert report["maximum_api_calls"] == 66
    assert report["observed_api_calls"] == 36
    assert len(arms["retrieval_on"]["cases"]) == 22
    assert len(arms["retrieval_off"]["cases"]) == 22
    assert (arms["retrieval_on"]["passed"], arms["retrieval_on"]["total"]) == (21, 22)
    assert (arms["retrieval_on"]["answerable_passed"], arms["retrieval_on"]["answerable_total"]) == (13, 14)
    assert (arms["retrieval_on"]["guard_passed"], arms["retrieval_on"]["guard_total"]) == (8, 8)
    assert (arms["retrieval_off"]["passed"], arms["retrieval_off"]["total"]) == (9, 22)
    assert (arms["retrieval_off"]["answerable_passed"], arms["retrieval_off"]["answerable_total"]) == (2, 14)
    assert (arms["retrieval_off"]["guard_passed"], arms["retrieval_off"]["guard_total"]) == (7, 8)
    assert len(arms["retrieval_on"]["failures"]) == 1
    assert len(arms["retrieval_off"]["failures"]) == 13

    assert ledger["status"] == "completed"
    assert ledger["report"] == "reports/ip4-retrieval-ablation-model-2026-11-16-r2.json"
    assert ledger["suite_id"] == report["suite_id"]
    assert ledger["model_calls"] == report["observed_api_calls"]
    assert ledger["passed"] == {name: f'{arms[name]["passed"]}/{arms[name]["total"]}' for name in arms}
    assert ledger["reserve_usd"] == report["reserve_usd"]
    assert ledger["peak_price_estimate_usd"] == report["observed_peak_price_estimate_usd"]
    assert ledger["remaining_before_usd"] == report["remaining_before_usd"]
    assert ledger["remaining_after_usd"] == report["remaining_after_usd"]
    assert round(report["remaining_before_usd"] - report["remaining_after_usd"], 7) == report["observed_peak_price_estimate_usd"]


def test_failed_assertion_counts_and_run_id_gap_are_explicit():
    report = _read(REPORT)
    arms = {arm["arm"]: arm for arm in report["arms"]}

    assert "run_id" not in report
    counts = {}
    for name, arm in arms.items():
        failed = Counter()
        for case in arm["failures"]:
            for key, value in case["score"].items():
                if key.endswith("_ok") and value is False:
                    failed[key] += 1
        counts[name] = dict(failed)
    assert counts["retrieval_on"] == {
        "status_ok": 1, "metric_ok": 1, "dimension_ok": 1, "numeric_ok": 1, "date_window_ok": 1
    }
    assert counts["retrieval_off"] == {
        "status_ok": 12, "metric_ok": 12, "dimension_ok": 11,
        "numeric_ok": 12, "date_window_ok": 11, "region_ok": 1
    }

    audit = _read(AUDIT)
    assert audit["audit_status"] == "passed"
    assert audit["run_id_present"] is False
    assert audit["sample_boundary"]["case_count"] == 22
    assert audit["sample_boundary"]["generalization_claim"] is False
    assert audit["agent_model_requests_for_this_audit"] == 0
    manifest = _read(RUN_MANIFEST)
    assert audit["run_manifest_sidecar"]["sha256"] == hashlib.sha256(RUN_MANIFEST.read_bytes()).hexdigest()
    assert manifest["schema_version"] == 2
    assert manifest["run_id"] is None and manifest["trace_id"] is None and manifest["span_id"] is None
    assert manifest["trace_event_evidence"]["source_status"] == "unavailable"
    assert manifest["sample_boundary"]["case_count"] == 22
