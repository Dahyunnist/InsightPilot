"""Audit frozen IP4 v13 artifacts and existing offline evidence without network calls."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_LOCK_PATHS = {
    "scorer_sha256": "insightpilot/enterprise_evaluate.py",
    "planner_sha256": "insightpilot/planner.py",
    "model_runner_sha256": "scripts/run_ip4_v13_model_blind.py",
    "rule_baseline_sha256": "reports/ip4-v13-rule-baseline.json",
    "rule_verifier_sha256": "scripts/verify_ip4_v13_rule.py",
    "holdout_builder_sha256": "data/build_ip4_v13_holdout.py",
    "dataset_builder_sha256": "data/multidomain_seed.py",
    "test_sha256": "tests/test_ip4_v13_holdout.py",
    "preregistration_script_sha256": "scripts/preregister_ip4_v13_model.py",
    "agent_runtime_sha256": "insightpilot/agent.py",
    "metric_runtime_sha256": "insightpilot/metrics.py",
    "query_runtime_sha256": "insightpilot/query_engine.py",
    "sql_runtime_sha256": "insightpilot/sql.py",
    "sql_policy_runtime_sha256": "insightpilot/sql_policy.py",
    "verification_runtime_sha256": "insightpilot/verification.py",
    "config_runtime_sha256": "insightpilot/config.py",
    "requirements_sha256": "requirements.txt",
}


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def _read_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def audit() -> dict:
    blind_path = "evals/ip4-v13-blind.jsonl"
    gold_path = "evals/ip4-v13-gold.jsonl"
    manifest_path = "evals/ip4-v13-manifest.json"
    seal_path = "reports/ip4-v13-gold-seal-20261114-r5.json"
    lock_path = "reports/ip4-v13-model-preregistration-20261114-r5.json"
    blind, gold = _read_jsonl(blind_path), _read_jsonl(gold_path)
    manifest, seal = _read_json(manifest_path), _read_json(seal_path)
    lock = _read_json(lock_path)
    offline = _read_json("reports/ip4-v13-offline-revision-20261113-r4.json")
    baseline = _read_json("reports/ip4-v13-rule-baseline.json")
    prior_model = _read_json("reports/ip4-v13-model-evaluation-r5.json")
    pg = _read_json("reports/ip4-postgres-verification.json")
    ledger = _read_json("reports/model-experiment-cost-ledger.json")
    code_mismatches = {
        key: {"path": path, "locked_sha256": lock.get("artifacts", {}).get(key),
              "current_sha256": _sha(path)}
        for key, path in CODE_LOCK_PATHS.items()
        if _sha(path) != lock.get("artifacts", {}).get(key)
    }

    blind_sha, gold_sha, manifest_sha = _sha(blind_path), _sha(gold_path), _sha(manifest_path)
    blind_ids = [row.get("case_id") for row in blind]
    gold_by_id = {row.get("case_id"): row for row in gold}
    domain_counts = dict(sorted(Counter(row.get("domain") for row in gold).items()))
    status_counts = dict(sorted(Counter(row.get("expected_status", "answered") for row in gold).items()))
    blind_schema_ok = all(set(row) == {"case_id", "question"} for row in blind)
    id_sets_match = len(blind) == len(set(blind_ids)) == len(gold_by_id) and set(blind_ids) == set(gold_by_id)
    hashes_match = (
        blind_sha == manifest.get("blind_sha256") == seal.get("blind_sha256") == lock.get("artifacts", {}).get("blind_sha256")
        and gold_sha == manifest.get("gold_sha256") == seal.get("gold_sha256") == lock.get("artifacts", {}).get("gold_sha256")
        and manifest_sha == seal.get("manifest_sha256") == lock.get("artifacts", {}).get("manifest_sha256")
    )
    strata_ok = (len(blind) == 22 and domain_counts == {"marketing_roi": 10, "refunds": 12}
                 and status_counts == {"answered": 14, "clarify": 5, "refused": 3})
    ambiguity_ids = manifest.get("edge_coverage", {}).get("ambiguity_guard_case_ids", [])
    refusal_reasons = Counter(row.get("expected_reason") for row in gold
                              if row.get("expected_status") == "refused")

    baseline_ok = (baseline.get("suite_id") == manifest.get("suite_id")
                   and baseline.get("total") == 22 and baseline.get("model_calls") == 0
                   and baseline.get("blind_sha256") == blind_sha
                   and baseline.get("gold_sha256") == gold_sha
                   and baseline.get("data_fingerprint") == manifest.get("data_fingerprint"))

    attempts = [attempt for case in prior_model.get("cases", []) for attempt in case.get("attempts", [])]
    transport_attempts = [attempt for attempt in attempts
                          if attempt.get("evaluation_disposition") == "not_scored_transport_error"]
    failure_classification_ok = (
        prior_model.get("status") == "partial_transport_blocked"
        and prior_model.get("provider_responses_received") == 0
        and len(transport_attempts) == prior_model.get("model_requests_attempted")
        and all(attempt.get("score", {}).get("passed") is None for attempt in transport_attempts)
    )

    reservation = ledger.get("ip4_v13_reservation", {})
    budget_hard_gate_ok = (
        lock.get("case_count") == 22
        and lock.get("planned_max_api_calls") == 22
        and lock.get("execution_attempts_per_case") == 1
        and lock.get("worst_case_reserve_usd", 1) <= 0.1873344
        and lock.get("global_cap_usd") == ledger.get("cap_usd") == 5.0
        and reservation.get("status") in {"reserved", "current_run_completed_prior_attempt_unresolved"}
        and offline.get("model_requests_sent", 1) == 0
        and offline.get("provider_balance_check_performed") is False
        and offline.get("model_evaluation_allowed") is False
    )

    # Existing PostgreSQL report covers an older 12-case suite and has no v13 hashes.
    postgres_v13_match = (pg.get("case_count_per_tenant") == 22
                          and pg.get("blind_sha256") == blind_sha
                          and pg.get("gold_sha256") == gold_sha)
    code_lock_match = not code_mismatches
    checks = {
        "frozen_v13_hashes_match_manifest_seal_and_lock": hashes_match,
        "blind_gold_case_ids_and_blind_schema_match": id_sets_match and blind_schema_ok,
        "22_case_domain_and_status_strata_match": strata_ok,
        "sqlite_rule_baseline_matches_frozen_hashes_and_dataset": baseline_ok,
        "prior_transport_failures_are_unscored": failure_classification_ok,
        "budget_caps_and_zero_call_preflight_hold": budget_hard_gate_ok,
        "execution_code_lock_matches": code_lock_match,
        "postgres_22_case_v13_gold_parity": postgres_v13_match,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return {
        "suite_id": manifest.get("suite_id"),
        "status": "passed" if not blockers else "blocked",
        "model_calls_this_audit": 0,
        "blind_questions_emitted": False,
        "blind_or_gold_modified": False,
        "case_count": len(blind),
        "domain_counts": domain_counts,
        "expected_status_counts": status_counts,
        "refusal_reason_counts": dict(sorted(refusal_reasons.items())),
        "ambiguity_guard_count": len(ambiguity_ids),
        "hashes": {"blind_sha256": blind_sha, "gold_sha256": gold_sha,
                   "manifest_sha256": manifest_sha},
        "sqlite_rule_baseline": {"passed": baseline.get("passed"), "total": baseline.get("total"),
                                 "gold_hash_matched": baseline_ok},
        "execution_code_lock": {"matches": code_lock_match, "mismatches": code_mismatches,
                                "relock_allowed_without_rebaseline": not code_lock_match and
                                set(code_mismatches) <= {"model_runner_sha256"}},
        "postgres_evidence": {"legacy_case_count_per_tenant": pg.get("case_count_per_tenant"),
                              "v13_same_hash_parity": postgres_v13_match,
                              "blocker": None if postgres_v13_match else
                              "没有本次 22 题 v13 的 PostgreSQL 同哈希验收证据；当前会话无权连接本机 Docker Engine。"},
        "failure_classification": {"prior_status": prior_model.get("status"),
                                   "attempted": prior_model.get("model_requests_attempted"),
                                   "provider_responses": prior_model.get("provider_responses_received"),
                                   "transport_attempts_not_scored": len(transport_attempts),
                                   "scored_failure_classes_supported_by_runner": [
                                       "wrong_clarifications", "wrong_refusals", "numeric_errors",
                                       "guard_wrong_responses", "unauthorized_accepts"],
                                   "observed_scored_model_failures": 0},
        "budget_gate": {"checks_passed": budget_hard_gate_ok,
                        "planned_calls": lock.get("planned_max_api_calls"),
                        "reserved_usd": lock.get("worst_case_reserve_usd"),
                        "global_cap_usd": ledger.get("cap_usd"),
                        "live_balance_preflight": "blocked_pending_owner_review"},
        "tenant_isolation": {"generic_api_regression_exists": True,
                             "v13_22_case_postgres_tenant_run": "blocked_with_postgres_parity"},
        "checks": checks,
        "blockers": blockers,
        "v14_model_run": "not_started_pending_owner_review_and_live_balance_preflight",
    }


def main(output_path: Path | None = None) -> None:
    report = audit()
    path = output_path or ROOT / "reports/ip4-v13-acceptance-audit-2026-11-15.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
