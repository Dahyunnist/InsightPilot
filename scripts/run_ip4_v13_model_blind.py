"""Run only the preregistered v13 model arm; never sends gold to the planner."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.config import load_settings
from insightpilot.enterprise_evaluate import BudgetedPlanner, EvaluationBudget, _compare
from insightpilot.runner import read_data_identity


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_provider_balance(api_key: str, origin: str, requirement: dict) -> dict:
    request = Request(origin + "/user/balance", headers={"Authorization": "Bearer " + api_key,
                                                           "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit("DeepSeek balance preflight failed: " + type(exc).__name__ + "; no request sent") from None
    if not isinstance(payload, dict) or payload.get("is_available") is not True:
        raise SystemExit("DeepSeek account unavailable; no model request sent")
    currency = requirement["currency"]
    rows = payload.get("balance_infos")
    row = next((item for item in rows if isinstance(item, dict) and item.get("currency") == currency), None) \
        if isinstance(rows, list) else None
    available = float(row.get("total_balance", "0")) if row else 0.0
    if available + 1e-9 < float(requirement["required"]):
        raise SystemExit("DeepSeek balance below frozen reserve; no model request sent")
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "is_available": True,
            "currency": currency, "available": available, "required": requirement["required"]}


def main() -> None:
    lock_path = ROOT / "reports/ip4-v13-model-preregistration.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    blind_path, gold_path = ROOT / "evals/ip4-v13-blind.jsonl", ROOT / "evals/ip4-v13-gold.jsonl"
    manifest_path = ROOT / "evals/ip4-v13-manifest.json"
    seal_path = ROOT / "reports/ip4-v13-gold-seal.json"
    baseline_path = ROOT / "reports/ip4-v13-rule-baseline.json"
    if lock["status"] != "frozen_pre_model":
        raise SystemExit("v13 preregistration is not pre-model; no request sent")
    if (sha(blind_path) != lock["artifacts"]["blind_sha256"] or
            sha(gold_path) != lock["artifacts"]["gold_sha256"] or
            sha(manifest_path) != lock["artifacts"]["manifest_sha256"] or
            sha(seal_path) != lock["artifacts"]["gold_seal_sha256"]):
        raise SystemExit("frozen v13 artifact hash mismatch; no request sent")
    expected_hashes = {
        "scorer_sha256": ROOT / "insightpilot/enterprise_evaluate.py",
        "planner_sha256": ROOT / "insightpilot/planner.py",
        "model_runner_sha256": ROOT / "scripts/run_ip4_v13_model_blind.py",
        "rule_baseline_sha256": baseline_path,
        "rule_verifier_sha256": ROOT / "scripts/verify_ip4_v13_rule.py",
        "holdout_builder_sha256": ROOT / "data/build_ip4_v13_holdout.py",
        "dataset_builder_sha256": ROOT / "data/multidomain_seed.py",
        "test_sha256": ROOT / "tests/test_ip4_v13_holdout.py",
        "preregistration_script_sha256": ROOT / "scripts/preregister_ip4_v13_model.py",
    }
    if any(sha(path) != lock["artifacts"][key] for key, path in expected_hashes.items()):
        raise SystemExit("registered scorer, planner, runner, builder, or baseline changed; no request sent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline.get("model_calls") != 0 or baseline.get("data_fingerprint") != lock["data_fingerprint"]:
        raise SystemExit("offline baseline no longer matches v13 freeze; no request sent")

    settings = load_settings(ROOT)
    parsed = urlparse(settings.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if settings.model != lock["model"] or origin != lock["base_url_origin"]:
        raise SystemExit("provider settings differ from preregistration; no request sent")
    settings = replace(settings, max_completion_tokens=lock["output_tokens_per_request"])
    balance = check_provider_balance(settings.api_key, origin, lock["provider_balance_reserve_check"])

    database = ROOT / "data/insightpilot-ip4-v13.sqlite"
    identity = read_data_identity(database)
    if identity != (lock["data_version"], lock["data_fingerprint"]):
        raise SystemExit("v13 database identity mismatch; no request sent")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in (
        json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    if len(cases) != lock["case_count"] or set(gold) != {case["case_id"] for case in cases}:
        raise SystemExit("v13 case/gold IDs differ from lock; no request sent")

    budget = EvaluationBudget(cap_usd=lock["worst_case_reserve_usd"],
                              max_calls=lock["max_api_calls"],
                              prompt_tokens=lock["input_tokens_per_request"],
                              completion_tokens=lock["output_tokens_per_request"])
    outputs = []
    for case in cases:
        attempts = []
        for attempt_no in range(1, lock["max_attempts_per_case"] + 1):
            planner = BudgetedPlanner(settings, budget)
            planner.max_prompt_bytes_per_call = lock["max_prompt_bytes_per_call"]
            result = InsightPilot(planner, database, tracer=None).analyze(case["question"])
            score = _compare(case, gold[case["case_id"]], result)
            calls = list(planner.last_calls)
            attempts.append({"attempt": attempt_no, "score": {**score, "actual_status": result.get("status")},
                             "result": result, "provider_calls": calls})
            if score["passed"] or not calls or result.get("stage") != "planning":
                break
        outputs.append({"case_id": case["case_id"], "question": case["question"],
                        "attempts": attempts, "final_score": attempts[-1]["score"]})

    answerable = {case_id for case_id, row in gold.items()
                  if row.get("expected_status", "answered") == "answered"}
    final_scores = [row["final_score"] for row in outputs]
    ledger_path = ROOT / "reports/model-experiment-cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger["cap_usd"] != lock["global_cap_usd"]:
        raise SystemExit("Global model cost cap changed during v13 run")
    reservation = ledger.get("ip4_v13_reservation", {})
    if (reservation.get("status") != "reserved" or
            reservation.get("preregistration_sha256") != sha(lock_path) or
            reservation.get("worst_case_reserve_usd") != lock["worst_case_reserve_usd"]):
        raise SystemExit("v13 cost reservation is missing or differs from lock; no request sent")
    before = lock["global_remaining_before_usd"]
    estimate = round(budget.estimated_usd, 8)
    if estimate > lock["worst_case_reserve_usd"] + 1e-12 or estimate > before:
        raise SystemExit("Observed v13 usage exceeded its reserved cap")
    after = round(before - estimate, 8)
    prompt_tokens = sum(call["usage"].get("prompt_tokens", 0) for row in outputs
                        for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    completion_tokens = sum(call["usage"].get("completion_tokens", 0) for row in outputs
                            for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "completed",
        "suite_id": lock["suite_id"], "model": settings.model,
        "data_fingerprint": lock["data_fingerprint"], "blind_sha256": lock["artifacts"]["blind_sha256"],
        "gold_sha256": lock["artifacts"]["gold_sha256"], "preregistration_sha256": sha(lock_path),
        "scorer_sha256": lock["artifacts"]["scorer_sha256"], "old_ip4_touched": False,
        "model_calls": len([call for row in outputs for attempt in row["attempts"]
                             for call in attempt["provider_calls"]]),
        "reserved_calls_used": budget.calls, "model_calls_are_question_only": True,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "passed": sum(score["passed"] for score in final_scores), "total": len(final_scores),
        "answerable_passed": sum(score["passed"] for score in final_scores
                                  if score["case_id"] in answerable),
        "answerable_total": len(answerable),
        "guard_passed": sum(score["passed"] for score in final_scores
                             if score["case_id"] not in answerable),
        "guard_total": len(final_scores) - len(answerable),
        "false_refusals": [score["case_id"] for score in final_scores
                            if score["case_id"] in answerable and score.get("actual_status") != "answered"],
        "false_accepts": [score["case_id"] for score in final_scores
                           if score["case_id"] not in answerable and score.get("actual_status") == "answered"],
        "usage_missing_calls": sum(call["usage"] is None for row in outputs
                                    for attempt in row["attempts"] for call in attempt["provider_calls"]),
        "peak_list_price_estimate_usd": estimate,
        "reserved_worst_case_usd": lock["worst_case_reserve_usd"],
        "global_remaining_before_usd": before, "global_remaining_after_actual_usage_usd": after,
        "global_cap_usd": lock["global_cap_usd"], "provider_balance_preflight": balance,
        "rule_baseline": {key: baseline[key] for key in
                          ("passed", "total", "answerable_passed", "answerable_total",
                           "guard_passed", "guard_total", "false_refusals", "false_accepts")},
        "cases": outputs,
    }
    report_path = ROOT / "reports/ip4-v13-model-blind-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger["remaining_global_cap_after_ip4_v13_model_usd"] = after
    ledger["remaining_global_cap_after_ip4_v13_reserve_usd"] = round(
        before - lock["worst_case_reserve_usd"], 8)
    ledger["ip4_v13_reservation"].update({
        "status": "consumed", "actual_usage_usd": estimate,
        "unused_reserve_released_usd": round(lock["worst_case_reserve_usd"] - estimate, 8),
        "global_remaining_after_actual_usage_usd": after,
        "model_calls": report["model_calls"],
    })
    ledger["ip4_v13_independent_holdout"] = {
        "status": "completed", "holdout": lock["suite_id"],
        "data_fingerprint": lock["data_fingerprint"], "blind_sha256": lock["artifacts"]["blind_sha256"],
        "gold_sha256": lock["artifacts"]["gold_sha256"], "model_calls": report["model_calls"],
        "strict_passed": f"{report['passed']}/{report['total']}",
        "peak_list_price_estimate_usd": estimate,
        "worst_case_reserve_usd": lock["worst_case_reserve_usd"],
        "global_remaining_before_usd": before, "global_remaining_after_actual_usage_usd": after,
        "no_old_ip4_rerun": True,
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
