"""Run only the preregistered v10 model arm; never sends gold to the planner."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.config import load_settings
from insightpilot.enterprise_evaluate import BudgetedPlanner, EvaluationBudget, _compare
from insightpilot.runner import read_data_identity
from scripts.verify_ip4_freeze_seal import verify as verify_ip4_seal


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    lock_path = ROOT / "reports/enterprise-v10-model-preregistration.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    blind_path, gold_path = ROOT / "evals/enterprise-blind-v10.jsonl", ROOT / "evals/enterprise-gold-v10.jsonl"
    manifest_path = ROOT / "evals/enterprise-holdout-manifest-v10.json"
    if lock["status"] != "frozen_pre_model":
        raise SystemExit("v10 preregistration is not in pre-model state")
    for path, key in ((blind_path, "blind_sha256"), (gold_path, "gold_sha256"),
                      (manifest_path, "manifest_sha256")):
        if sha(path) != lock[key]:
            raise SystemExit(f"frozen artifact hash mismatch: {path.name}; no request sent")
    seal = verify_ip4_seal()
    if seal["status"] != "passed":
        raise SystemExit("old IP4 seal check failed; no request sent")
    settings = load_settings(ROOT)
    settings = replace(settings, max_completion_tokens=lock["max_output_tokens"])
    from urllib.parse import urlparse
    origin = f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}"
    if settings.model != lock["model"] or origin != lock["base_url_origin"]:
        raise SystemExit("provider settings differ from lock; no request sent")
    if sha(ROOT / "insightpilot/enterprise_evaluate.py") != lock["scorer_sha256"] or sha(ROOT / "insightpilot/planner.py") != lock["planner_sha256"]:
        raise SystemExit("locked scorer or planner changed; no request sent")
    if sha(ROOT / "scripts/run_enterprise_v10_model.py") != lock["model_runner_sha256"]:
        raise SystemExit("locked model harness changed; no request sent")
    if sha(ROOT / "reports/enterprise-v10-rule-baseline.json") != lock["rule_baseline_sha256"]:
        raise SystemExit("paired rule baseline changed; no request sent")
    database = ROOT / "data/insightpilot-enterprise-v10.sqlite"
    identity = read_data_identity(database)
    if identity[1] != lock["data_fingerprint"]:
        raise SystemExit("v10 database fingerprint mismatch; no request sent")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    if len(cases) != lock["case_count"] or set(gold) != {row["case_id"] for row in cases}:
        raise SystemExit("case/gold IDs differ from lock; no request sent")

    budget = EvaluationBudget(cap_usd=lock["worst_case_reserve_usd"],
                              max_calls=lock["max_api_calls"],
                              prompt_tokens=lock["prompt_token_ceiling"],
                              completion_tokens=lock["max_output_tokens"])
    output = []
    for case in cases:
        attempt_rows = []
        for attempt in range(1, lock["max_attempts_per_case"] + 1):
            planner = BudgetedPlanner(settings, budget)
            result = InsightPilot(planner, database).analyze(case["question"])
            checked = _compare(case, gold[case["case_id"]], result)
            calls = list(planner.last_calls)
            attempt_rows.append({"attempt": attempt, "score": {**checked, "actual_status": result.get("status")}, "result": result,
                                 "provider_calls": calls})
            if checked["passed"] or not calls or result.get("stage") != "planning":
                break
        output.append({"case_id": case["case_id"], "question": case["question"],
                       "attempts": attempt_rows, "final_score": attempt_rows[-1]["score"]})

    answerable = {case_id for case_id, row in gold.items() if row.get("expected_status", "answered") == "answered"}
    guards = set(gold) - answerable
    final_rows = [row["final_score"] for row in output]
    observed_calls = budget.calls
    estimate = round(budget.estimated_usd, 8)
    ledger_path = ROOT / "reports/model-experiment-cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    previous_remaining = lock["global_remaining_before_usd"]
    actual_remaining = round(previous_remaining - estimate, 8)
    prompt_tokens = sum(call["usage"].get("prompt_tokens", 0) for row in output for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    completion_tokens = sum(call["usage"].get("completion_tokens", 0) for row in output for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "completed", "suite": lock["suite_id"], "model": settings.model,
        "data_fingerprint": lock["data_fingerprint"], "blind_sha256": lock["blind_sha256"],
        "gold_sha256": lock["gold_sha256"], "preregistration_sha256": sha(lock_path),
        "scorer_sha256": lock["scorer_sha256"], "old_ip4_touched": False,
        "model_calls": observed_calls, "model_calls_are_question_only": True,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "passed": sum(row["passed"] for row in final_rows), "total": len(final_rows),
        "answerable_passed": sum(row["passed"] for row in final_rows if row["case_id"] in answerable),
        "answerable_total": len(answerable),
        "guard_passed": sum(row["passed"] for row in final_rows if row["case_id"] in guards),
        "guard_total": len(guards),
        "false_refusals": [row["case_id"] for row in final_rows if row["case_id"] in answerable and row.get("actual_status") != "answered"],
        "false_accepts": [row["case_id"] for row in final_rows if row["case_id"] in guards and row.get("actual_status") == "answered"],
        "usage_missing_calls": sum(call["usage"] is None for row in output for attempt in row["attempts"] for call in attempt["provider_calls"]),
        "peak_list_price_estimate_usd": estimate,
        "reserved_worst_case_usd": lock["worst_case_reserve_usd"],
        "global_remaining_before_usd": previous_remaining,
        "global_remaining_after_actual_usage_usd": actual_remaining,
        "global_cap_usd": lock["global_cap_usd"],
        "rule_baseline": {"passed": 8, "total": 20, "answerable_passed": 0, "false_refusals": 11, "false_accepts": 0},
        "cases": output,
    }
    report_path = ROOT / "reports/enterprise-v10-model-blind-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger["remaining_global_cap_after_v10_model_usd"] = actual_remaining
    ledger["v10_independent_holdout"] = {
        "status": "completed", "holdout": "enterprise-blind-v10", "seed": lock["seed"],
        "blind_sha256": lock["blind_sha256"], "gold_sha256": lock["gold_sha256"],
        "model_calls": observed_calls, "strict_passed": f"{report['passed']}/{report['total']}",
        "peak_list_price_estimate_usd": estimate,
        "worst_case_reserve_usd": lock["worst_case_reserve_usd"],
        "global_remaining_before_usd": previous_remaining,
        "global_remaining_after_actual_usage_usd": actual_remaining,
        "no_old_ip4_rerun": True,
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


