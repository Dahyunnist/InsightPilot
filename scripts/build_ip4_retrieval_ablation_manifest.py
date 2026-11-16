"""Build a privacy-safe manifest sidecar for the completed IP4 ablation."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.run_manifest import (  # noqa: E402
    IP_TRACE_EVENT_TYPES,
    PUBLIC_EVENT_TYPES,
    validate_public_run_manifest,
)

REPORT_PATH = ROOT / "reports/ip4-retrieval-ablation-model-2026-11-16-r2.json"
FREEZE_PATH = ROOT / "reports/ip4-retrieval-ablation-freeze-2026-11-16.json"
OUTPUT_PATH = ROOT / "reports/ip4-retrieval-ablation-run-manifest-2026-11-16-r2.json"


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def event_coverage(vocabulary: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {name: {"count": 0, "coverage": "not_covered"} for name in vocabulary}


def build_manifest() -> dict[str, Any]:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if report.get("status") != "completed" or freeze.get("suite_id") != report.get("suite_id"):
        raise ValueError("IP4 report and freeze status/suite do not match")
    for key in ("manifest_sha256", "blind_sha256", "gold_sha256_local_only", "data_fingerprint"):
        freeze_key = key
        if report.get(key) != freeze.get(freeze_key):
            raise ValueError(f"IP4 report/freeze mismatch: {key}")
    if any(key in report for key in ("run_id", "trace_id", "span_id", "evaluation_id")):
        raise ValueError("IP4 report unexpectedly contains IDs; review sidecar mapping")

    arms = {arm["arm"]: arm for arm in report["arms"]}
    call_counts = {
        name: sum(case["model_calls"] for case in arm["cases"])
        for name, arm in arms.items()
    }
    usage_items = [usage for arm in arms.values() for case in arm["cases"]
                   for usage in case.get("usage", [])]
    calls = sum(call_counts.values())
    if calls != report["observed_api_calls"] or len(usage_items) != calls:
        raise ValueError("IP4 call and usage records do not reconcile")
    response_count = sum(isinstance(usage, dict) for usage in usage_items)
    if response_count != calls:
        raise ValueError("provider response count cannot be proven from per-call usage")

    planned = report["planned_external_analyses_by_arm"]
    arm_audit = {}
    for name, arm in arms.items():
        terminal_statuses = Counter(case.get("actual_status") for case in arm["cases"])
        arm_audit[name] = {
            "case_runs": len(arm["cases"]),
            "model_calls": call_counts[name],
            "planned_external_analyses": planned[name],
            "calls_over_one_per_planned_analysis_unclassified": call_counts[name] - planned[name],
            "terminal_status_counts": dict(sorted(terminal_statuses.items())),
            "passed": arm["passed"],
            "total": arm["total"],
        }

    field_status = {
        "git_sha": "not_recorded",
        "run_id": "not_recorded",
        "trace_id": "not_recorded",
        "span_id": "not_recorded",
        "evaluation_id": "not_recorded",
        "environment": "not_recorded",
        "dataset_id": "recorded",
        "dataset_sha256": "not_recorded",
        "data_fingerprint": "recorded",
        "split": "recorded",
        "model_id": "not_recorded",
        "prompt_sha256": "not_recorded",
        "tool_schema_sha256": "not_applicable",
        "scorer_sha256": "not_recorded",
        "runtime.digest_sha256": "not_recorded",
        "runtime.python": "not_recorded",
        "runtime.platform": "not_recorded",
        "runtime.lock_sha256": "not_recorded",
        "budget.cap_usd": "recorded",
        "budget.spent_usd": "not_recorded",
        "budget.reserved_usd": "recorded",
        "gold_sha256": "not_recorded",
        "started_at": "not_recorded",
        "completed_at": "recorded",
        "max_steps": "not_recorded",
        "max_tokens": "recorded",
        "normalizer_sha256": "recorded",
        "schema_sha256": "recorded",
    }
    manifest = {
        "schema_version": 2,
        "project": "InsightPilot",
        "run_id": None,
        "trace_id": None,
        "span_id": None,
        "evaluation_id": None,
        "environment": None,
        "git_sha": None,
        "source_tree_clean": None,
        "dataset_id": report["suite_id"],
        "dataset_sha256": None,
        "data_fingerprint": report["data_fingerprint"],
        "split": "frozen_synthetic_retrieval_ablation",
        "model_id": None,
        "planned_model_id": report["model"],
        "prompt_sha256": None,
        "tool_schema_sha256": None,
        "scorer_sha256": None,
        "runtime": {
            "digest_sha256": None,
            "python": None,
            "python_implementation": None,
            "platform": None,
            "lock_sha256": None,
        },
        "budget": {
            "cap_usd": freeze["local_preflight"]["authorized_peak_cap_usd"],
            "spent_usd": None,
            "reserved_usd": report["reserve_usd"],
            "price_estimate_usd": report["observed_peak_price_estimate_usd"],
            "remaining_before_usd": report["remaining_before_usd"],
            "remaining_after_usd": report["remaining_after_usd"],
            "status": "completed_estimate_actual_cost_unconfirmed",
        },
        "gold_source": "independent_synthetic_local_only",
        "gold_sha256": None,
        "cost_usd": None,
        "cost_status": "unconfirmed",
        "model_calls": calls,
        "model_input_received": calls > 0,
        "request_outcome": {
            "attempted": calls,
            "provider_responses_received": response_count,
            "cost_status": "unconfirmed",
        },
        "max_steps": None,
        "max_tokens": report["completion_token_cap"],
        "status": "completed",
        "source_status": report["status"],
        "evaluation_outcome": "completed_with_case_failures",
        "started_at": None,
        "completed_at": report["created_at_utc"],
        "field_status": field_status,
        "blocked_fields": sorted(key for key, state in field_status.items() if state == "blocked"),
        "trace_event_coverage": event_coverage(PUBLIC_EVENT_TYPES),
        "native_event_coverage": event_coverage(IP_TRACE_EVENT_TYPES),
        "trace_event_evidence": {
            "source_status": "unavailable",
            "source_logs_recorded": False,
            "interpretation": "0 means not observed in retained trace evidence; it does not prove the event did not occur.",
            "unavailable_events": {
                "timeout": {"count": None, "status": "unavailable",
                            "reason": "Failed case records do not retain timeout event codes."},
                "retry": {"count": None, "status": "unavailable",
                          "reason": "Three calls exceed planned first analyses in retrieval_off; causes were not retained."},
                "budget_exceeded": {"count": None, "status": "unavailable",
                                    "reason": "Report shows calls and estimate within limits, but retains no budget event trace."},
                "cancel": {"count": None, "status": "unavailable",
                           "reason": "No cancelled terminal cases are reported; no cancel event trace was retained."},
                "unsafe_action_blocked": {"count": None, "status": "unavailable",
                                          "reason": "Guard outcomes are scored; no event trace was retained."},
                "checkpoint_resume": {"count": None, "status": "unavailable",
                                      "reason": "Progress checkpoints are not included in the completed report."},
                "analysis.started": {"count": None, "status": "unavailable",
                                     "reason": "No native trace JSONL was retained."},
                "analysis.completed": {"count": None, "status": "unavailable",
                                       "reason": "No native trace JSONL was retained."},
                "analysis.failed": {"count": None, "status": "unavailable",
                                    "reason": "No native trace JSONL was retained."},
            },
            "run_id_note": "The runner called analyze without retaining its generated per-case run IDs.",
            "trace_id_note": "The report and freeze do not record trace IDs or spans.",
        },
        "report_level_control_checks": {
            "observed_api_calls": calls,
            "maximum_api_calls": report["maximum_api_calls"],
            "calls_within_cap": calls <= report["maximum_api_calls"],
            "price_estimate_usd": report["observed_peak_price_estimate_usd"],
            "reserve_usd": report["reserve_usd"],
            "estimate_within_reserve": report["observed_peak_price_estimate_usd"] <= report["reserve_usd"],
            "estimate_within_authorized_cap": report["observed_peak_price_estimate_usd"] <=
                freeze["local_preflight"]["authorized_peak_cap_usd"],
            "calls_over_planned_analyses_by_arm_unclassified": {
                name: call_counts[name] - planned[name] for name in arms
            },
        },
        "sample_boundary": {
            "case_count": 22,
            "case_runs": report["planned_case_runs"],
            "domains": ["refunds", "marketing_roi"],
            "data_kind": "synthetic",
            "generalization_claim": False,
        },
        "arm_call_audit": arm_audit,
        "unavailable_field_reasons": {
            "git_sha": "The report and freeze do not bind the execution to a commit SHA.",
            "run_id": "No evaluation-level ID was retained; per-case IDs were omitted from case records.",
            "trace_id": "No trace ID was retained in the report or freeze.",
            "span_id": "No span ID was retained in the report or freeze.",
            "evaluation_id": "No evaluation ID was emitted by this runner.",
            "dataset_sha256": "The source provides domain-separated blind hashes, not one canonical dataset hash.",
            "model_id": "The report records configured model only; provider response model ID was not retained.",
            "prompt_sha256": "Per-question prompt payloads and prompt hash were not retained.",
            "scorer_sha256": "The report does not record a scorer source hash.",
            "runtime": "Python/platform/dependency lock facts were not retained in the run report.",
            "budget.spent_usd": "Provider invoice cost was not confirmed; price estimate is kept separately.",
            "gold_sha256": "Only domain-separated local gold hashes exist; no single combined hash was recorded.",
            "started_at": "Only report creation time was retained; run start time was not recorded.",
            "max_steps": "The report records request caps, not an evaluation step cap.",
            "retry_event": "Three calls exceed planned first analyses in retrieval_off; per-call causes were not retained, so they are not classified as retries.",
            "trace_events": "No trace event stream was included with the model report.",
        },
        "report_payload_boundary": {
            "sent_fields": report["request_payload_fields"],
            "excluded_fields_attested": report["request_excludes_case_id_gold_expected_status_database_rows_sql_and_query_results"],
            "raw_questions_or_gold_content_in_sidecar": False,
        },
        "source_artifacts": {
            "report_path": "reports/ip4-retrieval-ablation-model-2026-11-16-r2.json",
            "report_sha256": sha_file(REPORT_PATH),
            "freeze_path": "reports/ip4-retrieval-ablation-freeze-2026-11-16.json",
            "freeze_sha256": sha_file(FREEZE_PATH),
            "manifest_sha256": report["manifest_sha256"],
            "blind_sha256_by_domain": report["blind_sha256"],
            "gold_sha256_local_only_by_domain": report["gold_sha256_local_only"],
            "data_fingerprint": report["data_fingerprint"],
        },
        "normalizer_sha256": sha_file(Path(__file__)),
        "schema_sha256": sha_file(ROOT / "insightpilot/run_manifest.py"),
    }
    errors = validate_public_run_manifest(manifest)
    if errors:
        raise ValueError("invalid IP4 retrieval run manifest: " + "; ".join(errors))
    return manifest


def main() -> None:
    manifest = build_manifest()
    OUTPUT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "path": OUTPUT_PATH.relative_to(ROOT).as_posix(),
                      "schema_version": manifest["schema_version"],
                      "model_calls": manifest["model_calls"],
                      "run_id": manifest["run_id"], "trace_id": manifest["trace_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
