"""Audit v13 revision hashes and score a no-model verifier ablation."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.enterprise_evaluate import _compare, _compare_without_verification, _without_verification
from insightpilot.freeze import sha256_text
from insightpilot.rule_baseline import RulePlanner
from insightpilot.runner import read_data_identity


REVISION = "20261113-r4"
BLIND = ROOT / "evals/ip4-v13-blind.jsonl"
GOLD = ROOT / "evals/ip4-v13-gold.jsonl"
EVAL_MANIFEST = ROOT / "evals/ip4-v13-manifest.json"
DATABASE = ROOT / "data/insightpilot-ip4-v13.sqlite"
BASELINE = ROOT / "reports/ip4-v13-rule-baseline.json"
OLD_LOCK = ROOT / "reports/ip4-v13-model-preregistration.json"
LEDGER = ROOT / "reports/model-experiment-cost-ledger.json"

PINNED_PATHS = {
    "scorer_sha256": "insightpilot/enterprise_evaluate.py",
    "planner_sha256": "insightpilot/planner.py",
    "model_runner_sha256": "scripts/run_ip4_v13_model_blind.py",
    "rule_baseline_runner_sha256": "scripts/verify_ip4_v13_rule.py",
    "rule_verifier_sha256": "scripts/verify_ip4_v13_rule.py",
    "holdout_builder_sha256": "data/build_ip4_v13_holdout.py",
    "dataset_builder_sha256": "data/multidomain_seed.py",
    "holdout_test_sha256": "tests/test_ip4_v13_holdout.py",
    "preregistration_script_sha256": "scripts/preregister_ip4_v13_model.py",
    "offline_audit_script_sha256": "scripts/audit_ip4_v13_offline.py",
    "agent_runtime_sha256": "insightpilot/agent.py",
    "metric_runtime_sha256": "insightpilot/metrics.py",
    "query_runtime_sha256": "insightpilot/query_engine.py",
    "sql_runtime_sha256": "insightpilot/sql.py",
    "sql_policy_runtime_sha256": "insightpilot/sql_policy.py",
    "verification_runtime_sha256": "insightpilot/verification.py",
    "config_runtime_sha256": "insightpilot/config.py",
    "requirements_sha256": "requirements.txt",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_no_verifier() -> dict:
    manifest = json.loads(EVAL_MANIFEST.read_text(encoding="utf-8"))
    if sha256_text(BLIND) != manifest["blind_sha256"] or sha256_text(GOLD) != manifest["gold_sha256"]:
        raise ValueError("v13 question or gold hash differs from frozen source")
    version, fingerprint = read_data_identity(DATABASE)
    if version != manifest["data_version"] or fingerprint != manifest["data_fingerprint"]:
        raise ValueError("v13 data identity differs from frozen source")
    cases = jsonl(BLIND)
    gold = {row["case_id"]: row for row in jsonl(GOLD)}
    if len(cases) != manifest["case_count"] or {row["case_id"] for row in cases} != set(gold):
        raise ValueError("v13 questions and gold IDs/count differ")

    scores = []
    for case in cases:
        result = _without_verification(RulePlanner(), DATABASE, case["question"])
        comparison, adjustment = _compare_without_verification(case, gold[case["case_id"]], result)
        scores.append({
            **comparison,
            "independent_verifier_invoked": False,
            "model_calls": 0,
            **({"audit_adjustment": adjustment} if adjustment else {}),
        })
    answerable = {case_id for case_id, row in gold.items()
                  if row.get("expected_status", "answered") == "answered"}
    report = {
        "revision": REVISION,
        "suite_id": manifest["suite_id"],
        "evaluation_role": "development_only",
        "is_blind": False,
        "arm": "RulePlanner_without_independent_verification",
        "model_calls": 0,
        "model_requests_sent": 0,
        "estimated_model_cost_usd": 0.0,
        "data_fingerprint": fingerprint,
        "questions_sha256": sha(BLIND),
        "gold_sha256": sha(GOLD),
        "scorer_sha256": sha(ROOT / PINNED_PATHS["scorer_sha256"]),
        "passed": sum(row["passed"] for row in scores),
        "total": len(scores),
        "answerable_passed": sum(row["passed"] for row in scores if row["case_id"] in answerable),
        "answerable_total": len(answerable),
        "guard_passed": sum(row["passed"] for row in scores if row["case_id"] not in answerable),
        "guard_total": len(scores) - len(answerable),
        "verifier_invocations": 0,
        "cases": scores,
        "interpretation": "本臂仅比较离线 gold 数值与计划；SQL 只读执行仍受查询策略保护。没有独立 verifier 的通过数不代表具备独立复核安全性。",
    }
    return report


def build_preflight(ablation: dict, ablation_path: Path | None = None) -> dict:
    manifest = json.loads(EVAL_MANIFEST.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    old_lock = json.loads(OLD_LOCK.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    reservation = ledger["ip4_v13_reservation"]
    cases = jsonl(BLIND)
    gold = jsonl(GOLD)

    input_tokens = 3_000
    output_tokens = 1_024
    per_call = (input_tokens * 0.30 + output_tokens * 1.20) / 1_000_000
    one_pass_reserve = round(len(cases) * per_call, 8)
    uncertain_history = float(reservation["historical_uncertain_reserve_usd"])
    required_total = round(one_pass_reserve + uncertain_history, 8)
    reserve_cny = round(required_total * 7.5, 6)
    actual_version, actual_fingerprint = read_data_identity(DATABASE)
    actual_pins = {key: sha(ROOT / path) for key, path in PINNED_PATHS.items()}
    stale_old_artifacts = sorted(
        key for key, digest in actual_pins.items()
        if old_lock.get("artifacts", {}).get(key) != digest
    )
    # Older lock used a shorter key name for this artifact.
    if old_lock.get("artifacts", {}).get("test_sha256") != sha(ROOT / "tests/test_ip4_v13_holdout.py"):
        stale_old_artifacts.append("test_sha256")

    checks = {
        "questions_match_frozen_manifest": sha256_text(BLIND) == manifest["blind_sha256"],
        "gold_matches_frozen_manifest": sha256_text(GOLD) == manifest["gold_sha256"],
        "question_gold_ids_and_count_match": len(cases) == manifest["case_count"] and
        {row["case_id"] for row in cases} == {row["case_id"] for row in gold},
        "database_identity_matches": (actual_version, actual_fingerprint) ==
        (manifest["data_version"], manifest["data_fingerprint"]),
        "rule_baseline_matches_data_and_has_zero_calls": baseline.get("data_fingerprint") == actual_fingerprint and
        baseline.get("model_calls") == 0,
        "rule_ablation_matches_data_and_has_zero_calls": ablation.get("data_fingerprint") == actual_fingerprint and
        ablation.get("model_calls") == 0 and ablation.get("verifier_invocations") == 0,
        "ledger_global_cap_is_five_usd": ledger.get("cap_usd") == 5.0,
        "existing_reservation_covers_current_and_unknown_history":
        reservation.get("status") == "reserved" and
        reservation.get("blind_sha256") == sha(BLIND) and
        reservation.get("gold_sha256") == sha(GOLD) and
        reservation.get("worst_case_reserve_usd", 0) + 1e-12 >= required_total,
    }
    if not all(checks.values()):
        raise ValueError("offline v13 gate failed: " + ", ".join(k for k, value in checks.items() if not value))

    artifacts = {
        "questions_sha256": sha(BLIND),
        "gold_sha256": sha(GOLD),
        "eval_manifest_sha256": sha(EVAL_MANIFEST),
        "database_file_sha256": sha(DATABASE),
        "rule_baseline_sha256": sha(BASELINE),
        "rule_ablation_sha256": sha(ablation_path or
                                     ROOT / f"reports/ip4-v13-rule-no-verifier-{REVISION}.json"),
        "old_preregistration_sha256": sha(OLD_LOCK),
        "cost_ledger_sha256": sha(LEDGER),
        **actual_pins,
    }
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    return {
        "revision": REVISION,
        "suite_id": manifest["suite_id"],
        "status": "blocked_pre_model_balance_unverified",
        "evaluation_role": "development_only",
        "is_blind": False,
        "model_evaluation_allowed": False,
        "model_calls": 0,
        "model_requests_sent": 0,
        "provider_received_this_revision": False,
        "provider_balance_current": None,
        "provider_balance_check_performed": False,
        "provider_balance_gate": {
            "status": "blocked_live_balance_required",
            "endpoint": "https://api.deepseek.com/user/balance",
            "prior_snapshot_usable": False,
            "minimum_cny_for_current_plus_unknown_history": reserve_cny,
            "reason": "宿主实时余额接口不可达；离线审计未发余额 GET，也未发模型请求。",
        },
        "offline_zero_call_gate": {"status": "passed", "checks": checks,
                                   "model_calls": 0, "external_requests": 0},
        "source_head_commit": head,
        "old_lock_status": old_lock["status"],
        "old_lock_freeze_commit": old_lock["freeze_commit_sha"],
        "old_lock_stale_artifact_keys": sorted(set(stale_old_artifacts)),
        "questions_and_gold_reused_only_as_development_data": True,
        "data_version": manifest["data_version"],
        "data_fingerprint": manifest["data_fingerprint"],
        "case_count": len(cases),
        "input_tokens_per_request": input_tokens,
        "output_tokens_per_request": output_tokens,
        "max_attempts_per_case": 1,
        "planned_max_api_calls": len(cases),
        "per_call_peak_usd": round(per_call, 8),
        "current_run_reserve_usd": one_pass_reserve,
        "historical_uncertain_reserve_usd": uncertain_history,
        "required_total_reserve_usd": required_total,
        "required_total_reserve_cny": reserve_cny,
        "global_cap_usd": ledger["cap_usd"],
        "global_remaining_after_existing_reservation_usd": ledger["remaining_global_cap_after_ip4_v13_reserve_usd"],
        "existing_reservation_bound_to_old_revision": True,
        "checks": checks,
        "artifacts": artifacts,
        "rule_baseline": {key: baseline[key] for key in
                          ("passed", "total", "answerable_passed", "answerable_total",
                           "guard_passed", "guard_total", "false_refusals", "false_accepts")},
        "rule_verifier_ablation": {key: ablation[key] for key in
                                   ("passed", "total", "answerable_passed", "answerable_total",
                                    "guard_passed", "guard_total", "verifier_invocations")},
    }


def run(output_dir: Path = ROOT / "reports") -> tuple[dict, dict]:
    ablation = evaluate_no_verifier()
    output_dir.mkdir(parents=True, exist_ok=True)
    ablation_path = output_dir / f"ip4-v13-rule-no-verifier-{REVISION}.json"
    ablation_path.write_text(json.dumps(ablation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preflight = build_preflight(ablation)
    preflight_path = output_dir / f"ip4-v13-offline-revision-{REVISION}.json"
    preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ablation, preflight


if __name__ == "__main__":
    ablation, preflight = run()
    print(json.dumps({
        "status": preflight["status"],
        "offline_zero_call_gate": preflight["offline_zero_call_gate"]["status"],
        "provider_balance_gate": preflight["provider_balance_gate"]["status"],
        "rule_ablation": {key: ablation[key] for key in
                           ("passed", "total", "answerable_passed", "guard_passed", "verifier_invocations")},
        "model_calls": preflight["model_calls"],
    }, ensure_ascii=False, indent=2))
