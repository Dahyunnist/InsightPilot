"""Create backward-compatible public run-manifest views for IP4 v13 artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.run_manifest import IP_TRACE_EVENT_TYPES, PUBLIC_EVENT_TYPES, validate_public_run_manifest


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def event_coverage(vocabulary: tuple[str, ...], events: list[dict]) -> dict:
    counts = {name: sum(isinstance(event, dict) and event.get("event", event.get("event_type")) == name
                        for event in events)
              for name in vocabulary}
    return {name: {"count": count, "coverage": "covered" if count else "not_covered"}
            for name, count in counts.items()}


def field_states(source_kind: str, runtime_facts: dict | None = None) -> dict[str, str]:
    baseline = source_kind == "offline_rule_baseline"
    facts = runtime_facts or {}
    state = {
        "git_sha": "recorded", "run_id": "recorded" if baseline else "blocked",
        "trace_id": "recorded" if baseline else "blocked",
        "span_id": "not_applicable" if baseline else "blocked",
        "evaluation_id": "recorded" if baseline else "blocked",
        "environment": "recorded" if baseline and facts.get("platform") else "blocked",
        "dataset_id": "recorded", "dataset_sha256": "recorded", "data_fingerprint": "recorded", "split": "recorded",
        "model_id": "not_applicable" if baseline else "not_applicable",
        "prompt_sha256": "not_applicable", "tool_schema_sha256": "not_applicable",
        "scorer_sha256": "recorded" if baseline else "blocked",
        "runtime.digest_sha256": "recorded" if baseline and facts.get("runtime_digest") else "blocked",
        "runtime.python": "recorded" if baseline and facts.get("python") else "blocked",
        "runtime.platform": "recorded" if baseline and facts.get("platform") else "blocked",
        "runtime.lock_sha256": "blocked",
        "budget.cap_usd": "recorded", "budget.spent_usd": "recorded",
        "gold_sha256": "recorded", "gold_source": "recorded",
        "started_at": "recorded", "completed_at": "recorded" if baseline else "blocked",
        "max_steps": "recorded" if baseline else "blocked",
        "max_tokens": "not_applicable",
    }
    return state


def _seal(value: dict) -> dict:
    value["blocked_fields"] = sorted(key for key, state in value["field_status"].items() if state == "blocked")
    errors = validate_public_run_manifest(value)
    if errors:
        raise ValueError("invalid IP4 public manifest: " + "; ".join(errors))
    return value


def normalize_baseline(events: list[dict] | None = None) -> dict:
    reports = ROOT / "reports"
    source_path = reports / "ip4-v13-rule-baseline-manifest.json"
    report_path = reports / "ip4-v13-rule-baseline.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    blind_path, gold_path = ROOT / "evals/ip4-v13-blind.jsonl", ROOT / "evals/ip4-v13-gold.jsonl"
    canonical_report = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if (source.get("dataset_sha256") != sha_file(blind_path) or
            source.get("truth_sha256") != sha_file(gold_path) or
            source.get("result_fingerprint_sha256") != sha_bytes(canonical_report) or
            report.get("data_fingerprint") != json.loads((ROOT / "evals/ip4-v13-manifest.json").read_text(encoding="utf-8")).get("data_fingerprint")):
        raise ValueError("legacy baseline source hashes do not match frozen artifacts")
    facts = source.get("runtime_facts") or {}
    runtime_facts = {key: facts[key] for key in (
        "python", "python_implementation", "platform", "requirements_sha256", "runner_sha256", "working_tree_clean")}
    if source.get("runtime_digest") != sha_bytes(json.dumps(
            runtime_facts, sort_keys=True, separators=(",", ":")).encode("utf-8")):
        raise ValueError("legacy baseline runtime digest mismatch")
    status = "completed"
    outcome = "passed" if report["passed"] == report["total"] else "failed"
    value = {
        "schema_version": 2, "project": source.get("project"), "run_id": source.get("run_id"),
        "trace_id": source.get("trace_id"), "span_id": source.get("span_id"),
        "evaluation_id": source.get("evaluation_id"), "environment": facts.get("platform"),
        "git_sha": source.get("git_sha"), "source_tree_clean": facts.get("working_tree_clean"),
        "dataset_id": source.get("dataset_id"), "dataset_sha256": source.get("dataset_sha256"),
        "data_fingerprint": report.get("data_fingerprint"),
        "split": source.get("split"), "model_id": source.get("model_id"), "planned_model_id": None,
        "prompt_sha256": source.get("prompt_sha"), "tool_schema_sha256": source.get("tool_schema_sha"),
        "scorer_sha256": source.get("scorer_sha"),
        "runtime": {"digest_sha256": source.get("runtime_digest"), "python": facts.get("python"),
                    "python_implementation": facts.get("python_implementation"),
                    "platform": facts.get("platform"), "lock_sha256": None,
                    "dependency_manifest_sha256": facts.get("requirements_sha256"),
                    "runner_sha256": facts.get("runner_sha256")},
        "budget": {"cap_usd": 5.0, "spent_usd": source.get("cost_usd_estimate"),
                   "reserved_usd": 0.0, "status": "completed_no_model_cost"},
        "gold_source": source.get("gold_source"), "gold_sha256": source.get("truth_sha256"),
        "cost_usd": source.get("cost_usd_estimate"), "model_calls": report.get("model_calls"),
        "model_input_received": report.get("model_calls", 0) > 0,
        "max_steps": source.get("max_steps"), "max_tokens": source.get("max_tokens"),
        "status": status, "source_status": source.get("status"), "evaluation_outcome": outcome,
        "started_at": source.get("started_at"), "completed_at": source.get("ended_at"),
        "field_status": field_states("offline_rule_baseline", {
            "runtime_digest": source.get("runtime_digest"), "python": facts.get("python"),
            "platform": facts.get("platform")}),
        "trace_event_coverage": event_coverage(PUBLIC_EVENT_TYPES, events or []),
        "native_event_coverage": event_coverage(IP_TRACE_EVENT_TYPES, events or []),
        "source_artifacts": {
            "manifest_sha256": sha_file(source_path), "report_sha256": sha_file(report_path),
            "blind_sha256": source.get("dataset_sha256"), "gold_sha256": source.get("truth_sha256"),
            "manifest_path": "reports/ip4-v13-rule-baseline-manifest.json",
            "report_path": "reports/ip4-v13-rule-baseline.json",
        },
        "normalizer_sha256": sha_file(Path(__file__)),
        "schema_sha256": sha_file(ROOT / "insightpilot/run_manifest.py"),
    }
    value["field_status"]["normalizer_sha256"] = "recorded"
    value["field_status"]["schema_sha256"] = "recorded"
    value["field_status"]["source_tree_clean"] = "recorded"
    value["field_status"]["model_id"] = "not_applicable"
    return _seal(value)


def normalize_blocked_attempt(events: list[dict] | None = None) -> dict:
    reports = ROOT / "reports"
    attempt_path = reports / "ip4-v13-attempt-manifest-2026-11-13-r3.json"
    lock_path = reports / "ip4-v13-model-preregistration-20261113-r2.json"
    ledger_path = reports / "model-experiment-cost-ledger.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    reservation = ledger["ip4_v13_reservation"]
    if (sha_file(lock_path) != attempt.get("prior_preregistration_sha256") or
            lock["artifacts"]["blind_sha256"] != attempt.get("question_set_sha256") or
            lock["artifacts"]["gold_sha256"] != attempt.get("gold_sha256") or
            sha_file(ROOT / "evals/ip4-v13-blind.jsonl") != attempt.get("question_set_sha256") or
            sha_file(ROOT / "evals/ip4-v13-gold.jsonl") != attempt.get("gold_sha256") or
            reservation.get("preregistration_sha256") != attempt.get("prior_preregistration_sha256")):
        raise ValueError("blocked attempt does not match its lock, frozen questions, gold, or reservation")
    value = {
        "schema_version": 2, "project": "InsightPilot", "run_id": None, "trace_id": None,
        "span_id": None, "evaluation_id": None, "environment": None,
        "git_sha": attempt.get("current_commit"), "source_tree_clean": None,
        "dataset_id": attempt.get("suite_id"), "dataset_sha256": attempt.get("question_set_sha256"),
        "data_fingerprint": lock.get("data_fingerprint"),
        "split": "frozen_synthetic", "model_id": "none", "planned_model_id": attempt.get("model_id"),
        "prompt_sha256": None, "planned_prompt_source_sha256": lock["artifacts"]["planner_sha256"],
        "tool_schema_sha256": None, "scorer_sha256": None,
        "planned_scorer_sha256": lock["artifacts"]["scorer_sha256"],
        "runtime": {"digest_sha256": None, "python": None, "python_implementation": None,
                    "platform": None, "lock_sha256": None},
        "budget": {"cap_usd": ledger["cap_usd"], "spent_usd": attempt["cost_usd"],
                   "reserved_usd": reservation["worst_case_reserve_usd"],
                   "remaining_after_reserve_usd": attempt["ledger_remaining_after_prior_reservation_usd"],
                   "status": "blocked_stale_lock_and_balance_unverified",
                   "reservation_bound_to_current_code": attempt["prior_reservation_bound_to_current_code"]},
        "gold_source": "independent_synthetic", "gold_sha256": attempt.get("gold_sha256"),
        "cost_usd": attempt.get("cost_usd"), "model_calls": attempt.get("model_calls"),
        "model_input_received": attempt.get("provider_received"),
        "max_steps": None, "planned_max_calls": lock.get("planned_max_api_calls"),
        "max_tokens": None, "planned_max_tokens": lock.get("output_tokens_per_request"),
        "status": "blocked", "source_status": attempt.get("status"),
        "evaluation_outcome": "not_run_preflight_blocked",
        "started_at": attempt.get("attempted_at_utc"), "completed_at": None,
        "field_status": field_states("blocked_model_attempt"),
        "trace_event_coverage": event_coverage(PUBLIC_EVENT_TYPES, events or []),
        "native_event_coverage": event_coverage(IP_TRACE_EVENT_TYPES, events or []),
        "source_artifacts": {
            "attempt_manifest_sha256": sha_file(attempt_path), "preregistration_sha256": sha_file(lock_path),
            "question_sha256": attempt.get("question_set_sha256"), "gold_sha256": attempt.get("gold_sha256"),
            "ledger_sha256": sha_file(ledger_path), "attempt_path": "reports/ip4-v13-attempt-manifest-2026-11-13-r3.json",
        },
        "normalizer_sha256": sha_file(Path(__file__)),
        "schema_sha256": sha_file(ROOT / "insightpilot/run_manifest.py"),
    }
    value["field_status"].update({
        "runtime.digest_sha256": "blocked", "runtime.python": "blocked", "runtime.platform": "blocked",
        "runtime.lock_sha256": "blocked", "budget.cap_usd": "recorded", "budget.spent_usd": "recorded",
        "gold_sha256": "recorded", "gold_source": "recorded", "started_at": "recorded",
        "normalizer_sha256": "recorded", "source_tree_clean": "blocked",
        "schema_sha256": "recorded",
    })
    return _seal(value)


def build_public_views() -> tuple[dict, dict, dict]:
    baseline = normalize_baseline()
    attempt = normalize_blocked_attempt()
    coverage = {
        "report_version": 1,
        "normalizer_sha256": sha_file(Path(__file__)),
        "schema_sha256": sha_file(ROOT / "insightpilot/run_manifest.py"),
        "source_logs_available": False,
        "observation_note": "No trace JSONL was emitted for the RulePlanner baseline or blocked preflight; zero means not observed, not proof of absence.",
        "vocabularies": {
            "shared_v2": {"events": list(PUBLIC_EVENT_TYPES), "observed": baseline["trace_event_coverage"]},
            "insightpilot_native_jsonl": {"events": list(IP_TRACE_EVENT_TYPES), "observed": baseline["native_event_coverage"]},
        },
        "runs": [
            {"kind": "offline_rule_baseline", "run_id": baseline["run_id"],
             "shared_v2": baseline["trace_event_coverage"], "native": baseline["native_event_coverage"]},
            {"kind": "blocked_model_preflight", "run_id": None,
             "shared_v2": attempt["trace_event_coverage"], "native": attempt["native_event_coverage"]},
        ],
    }
    return baseline, attempt, coverage


def main() -> None:
    baseline, attempt, coverage = build_public_views()
    outputs = {
        ROOT / "reports/ip4-v13-rule-baseline-public-manifest.json": baseline,
        ROOT / "reports/ip4-v13-attempt-2026-11-13-r3-public-manifest.json": attempt,
        ROOT / "reports/ip4-v13-event-vocabulary-coverage.json": coverage,
    }
    for path, value in outputs.items():
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "files": [str(path.relative_to(ROOT)) for path in outputs]}, indent=2))


if __name__ == "__main__":
    main()
