"""Run only the preregistered v13 model arm; never sends gold to the planner."""

from __future__ import annotations

import hashlib
import argparse
import json
import platform
import subprocess
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
from insightpilot.planner import PlanError
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


class CapturingBudgetedPlanner(BudgetedPlanner):
    """Retain each parsed structured response for the frozen evaluation report."""

    def __init__(self, settings, budget):
        super().__init__(settings, budget)
        self.raw_structured_outputs = []

    def _complete(self, question: str, candidates: list[str], allowed_metric_ids: list[str]) -> dict:
        if self.last_call_count >= 1:
            raise PlanError("v13 limit: one model request per question")
        result = super()._complete(question, candidates, allowed_metric_ids)
        self.raw_structured_outputs.append(result)
        return result


def git_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def is_ancestor(commit: str, ref: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, ref], cwd=ROOT,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="verify all gates and send no model requests")
    parser.add_argument("--preregistration", type=Path,
                        default=Path("reports/ip4-v13-model-preregistration.json"))
    parser.add_argument("--report", type=Path,
                        default=Path("reports/ip4-v13-model-evaluation-report.json"))
    args = parser.parse_args()
    lock_path = (ROOT / args.preregistration).resolve()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    blind_path, gold_path = ROOT / "evals/ip4-v13-blind.jsonl", ROOT / "evals/ip4-v13-gold.jsonl"
    manifest_path = ROOT / "evals/ip4-v13-manifest.json"
    seal_path = ROOT / lock.get("gold_seal_file", "reports/ip4-v13-gold-seal.json")
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
        "agent_runtime_sha256": ROOT / "insightpilot/agent.py",
        "metric_runtime_sha256": ROOT / "insightpilot/metrics.py",
        "query_runtime_sha256": ROOT / "insightpilot/query_engine.py",
        "sql_runtime_sha256": ROOT / "insightpilot/sql.py",
        "sql_policy_runtime_sha256": ROOT / "insightpilot/sql_policy.py",
        "verification_runtime_sha256": ROOT / "insightpilot/verification.py",
        "config_runtime_sha256": ROOT / "insightpilot/config.py",
        "requirements_sha256": ROOT / "requirements.txt",
    }
    if any(sha(path) != lock["artifacts"][key] for key, path in expected_hashes.items()):
        raise SystemExit("registered scorer, planner, runner, builder, or baseline changed; no request sent")
    if (platform.python_version() != lock["python_version"] or
            platform.python_implementation() != lock["python_implementation"]):
        raise SystemExit("Python runtime differs from preregistration; no request sent")
    current_commit = git_text("rev-parse", "HEAD")
    freeze_commit = lock["freeze_commit_sha"]
    if not is_ancestor(freeze_commit, current_commit):
        raise SystemExit("registered freeze is not an ancestor of current commit; no request sent")
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
    if (lock.get("execution_attempts_per_case") != 1 or
            lock.get("planned_max_api_calls") != len(cases) or
            lock["planned_max_api_calls"] > lock["max_api_calls"]):
        raise SystemExit("v13 execution count differs from one-call-per-case plan; no request sent")
    report_path = (ROOT / args.report).resolve()
    if report_path.exists():
        raise SystemExit("v13 model report already exists; refusing to overwrite or rerun the frozen set")

    ledger_path = ROOT / "reports/model-experiment-cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger["cap_usd"] != lock["global_cap_usd"]:
        raise SystemExit("Global model cost cap changed; no request sent")
    reservation = ledger.get("ip4_v13_reservation", {})
    if (reservation.get("status") != "reserved" or
            reservation.get("preregistration_sha256") != sha(lock_path) or
            reservation.get("worst_case_reserve_usd") != lock["worst_case_reserve_usd"]):
        raise SystemExit("v13 cost reservation is missing or differs from lock; no request sent")

    budget = EvaluationBudget(cap_usd=lock["worst_case_reserve_usd"],
                              max_calls=lock["planned_max_api_calls"],
                              prompt_tokens=lock["input_tokens_per_request"],
                              completion_tokens=lock["output_tokens_per_request"])
    if args.dry_run:
        print(json.dumps({
            "status": "dry_preflight_passed", "model_requests_sent": 0,
            "freeze_commit_sha": freeze_commit, "current_commit_sha": current_commit,
            "model": settings.model,
            "case_count": len(cases), "answerable_case_count": lock["answerable_case_count"],
            "guard_case_count": lock["guard_case_count"],
            "max_actual_model_calls": lock["planned_max_api_calls"],
            "reserved_worst_case_usd": lock["worst_case_reserve_usd"],
            "current_run_reserve_usd": lock["current_run_reserve_usd"],
            "historical_uncertain_reserve_usd": lock["historical_uncertain_reserve_usd"],
            "reserved_worst_case_cny": lock["worst_case_reserve_cny"],
            "provider_balance_preflight": balance,
            "blind_sha256": lock["artifacts"]["blind_sha256"],
            "gold_sha256": lock["artifacts"]["gold_sha256"],
            "scorer_sha256": lock["artifacts"]["scorer_sha256"],
            "runner_sha256": lock["artifacts"]["model_runner_sha256"],
        }, ensure_ascii=False, indent=2))
        return

    outputs = []
    for case in cases:
        attempts = []
        for attempt_no in range(1, lock["execution_attempts_per_case"] + 1):
            planner = CapturingBudgetedPlanner(settings, budget)
            planner.max_prompt_bytes_per_call = lock["max_prompt_bytes_per_call"]
            result = InsightPilot(planner, database, tracer=None).analyze(case["question"])
            score = _compare(case, gold[case["case_id"]], result)
            calls = list(planner.last_calls)
            attempts.append({"attempt": attempt_no, "score": {**score, "actual_status": result.get("status")},
                             "result": result, "raw_structured_outputs": planner.raw_structured_outputs,
                             "provider_calls": calls})
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
    current_reserve = lock["current_run_reserve_usd"]
    historical_reserve = lock["historical_uncertain_reserve_usd"]
    if estimate > current_reserve + 1e-12 or historical_reserve + estimate > before:
        raise SystemExit("Observed v13 usage exceeded its reserved cap")
    after = round(before - historical_reserve - estimate, 8)
    prompt_tokens = sum(call["usage"].get("prompt_tokens", 0) for row in outputs
                        for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    completion_tokens = sum(call["usage"].get("completion_tokens", 0) for row in outputs
                            for attempt in row["attempts"] for call in attempt["provider_calls"] if call["usage"])
    per_call_reserve = budget.per_call_usd
    for output in outputs:
        for attempt in output["attempts"]:
            calls = attempt["provider_calls"]
            attempt["peak_list_price_estimate_usd"] = round(sum(
                (call["usage"].get("prompt_tokens", lock["input_tokens_per_request"]) *
                 lock["pricing"]["input_peak_usd_per_million"] / 1_000_000 +
                 call["usage"].get("completion_tokens", lock["output_tokens_per_request"]) *
                 lock["pricing"]["output_peak_usd_per_million"] / 1_000_000)
                if isinstance(call.get("usage"), dict) else per_call_reserve
                for call in calls), 8)
            attempt["model_call_count"] = len(calls)
    wrong_clarifications = [score["case_id"] for score in final_scores
                            if score["case_id"] in answerable and score.get("actual_status") == "clarify"]
    wrong_refusals = [score["case_id"] for score in final_scores
                      if score["case_id"] in answerable and score.get("actual_status") == "refused"]
    numeric_errors = [score["case_id"] for score in final_scores
                      if score["case_id"] in answerable and score.get("actual_status") == "answered"
                      and score.get("numeric_ok") is False]
    guard_wrong_responses = [score["case_id"] for score in final_scores
                             if score["case_id"] not in answerable and not score.get("passed")]
    unauthorized_accepts = [score["case_id"] for score in final_scores
                            if gold[score["case_id"]].get("expected_status") == "refused"
                            and score.get("actual_status") == "answered"]
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "completed",
        "evaluation_design": "single pass on frozen synthetic questions; historical exposure unknown",
        "suite_id": lock["suite_id"], "model": settings.model,
        "data_fingerprint": lock["data_fingerprint"], "questions_sha256": lock["artifacts"]["blind_sha256"],
        "gold_sha256": lock["artifacts"]["gold_sha256"], "preregistration_sha256": sha(lock_path),
        "scorer_sha256": lock["artifacts"]["scorer_sha256"], "old_ip4_touched": False,
        "model_calls": len([call for row in outputs for attempt in row["attempts"]
                             for call in attempt["provider_calls"]]),
        "agent_cases_run": len(outputs), "planned_max_api_calls": lock["planned_max_api_calls"],
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
        "wrong_clarifications": wrong_clarifications, "wrong_refusals": wrong_refusals,
        "numeric_errors": numeric_errors, "guard_wrong_responses": guard_wrong_responses,
        "unauthorized_accepts": unauthorized_accepts,
        "usage_missing_calls": sum(call["usage"] is None for row in outputs
                                    for attempt in row["attempts"] for call in attempt["provider_calls"]),
        "peak_list_price_estimate_usd": estimate,
        "reserved_worst_case_usd": current_reserve,
        "historical_uncertain_reserve_usd": historical_reserve,
        "global_remaining_before_usd": before, "global_remaining_after_actual_usage_usd": after,
        "global_cap_usd": lock["global_cap_usd"], "provider_balance_preflight": balance,
        "rule_baseline": {key: baseline[key] for key in
                          ("passed", "total", "answerable_passed", "answerable_total",
                           "guard_passed", "guard_total", "false_refusals", "false_accepts")},
        "cases": outputs,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger["remaining_global_cap_after_ip4_v13_model_usd"] = after
    ledger["remaining_global_cap_after_ip4_v13_reserve_usd"] = round(
        before - historical_reserve - current_reserve, 8)
    ledger["ip4_v13_reservation"].update({
        "status": "current_run_completed_prior_attempt_unresolved",
        "current_run_actual_usage_usd": estimate,
        "current_run_unused_reserve_released_usd": round(current_reserve - estimate, 8),
        "historical_uncertain_reserve_usd": historical_reserve,
        "global_remaining_after_actual_usage_usd": after,
        "model_calls": report["model_calls"],
    })
    ledger["ip4_v13_model_evaluation"] = {
        "status": "completed_single_pass", "suite_id": lock["suite_id"],
        "evaluation_design": report["evaluation_design"],
        "data_fingerprint": lock["data_fingerprint"], "questions_sha256": lock["artifacts"]["blind_sha256"],
        "gold_sha256": lock["artifacts"]["gold_sha256"], "model_calls": report["model_calls"],
        "strict_passed": f"{report['passed']}/{report['total']}",
        "peak_list_price_estimate_usd": estimate,
        "worst_case_reserve_usd": current_reserve,
        "historical_uncertain_reserve_usd": historical_reserve,
        "global_remaining_before_usd": before, "global_remaining_after_actual_usage_usd": after,
        "old_ip4_touched": False,
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
