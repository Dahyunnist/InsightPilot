"""Evaluate the frozen refunds and marketing sets once on SQLite and PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.agent import InsightPilot
from insightpilot.config import load_settings
from insightpilot.ip4_evaluate import compare
from insightpilot.planner import OpenAICompatiblePlanner
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.rule_baseline import RulePlanner


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("refunds", "marketing_roi")
TENANTS = ("tenant-a", "tenant-b")
SET_ROOT = ROOT / "evals/domain-validation-v1"
LOCK_PATH = ROOT / "reports/domain-validation-model-preregistration.json"
OUTPUT_PATH = ROOT / "reports/domain-validation-model-blind-report.json"
LEDGER_PATH = ROOT / "reports/model-experiment-cost-ledger.json"
PEAK_INPUT_USD_PER_MILLION = 0.30
PEAK_OUTPUT_USD_PER_MILLION = 1.20
MAX_PROMPT_BYTES = 51_000
RESERVED_INPUT_TOKENS = 52_000
MAX_OUTPUT_TOKENS = 1_024
MAX_ATTEMPTS_PER_CASE = 2
BACKEND_RUNS = 3
SCORER_PATH = ROOT / "insightpilot/ip4_evaluate.py"
PLANNER_PATH = ROOT / "insightpilot/planner.py"
HARNESS_PATH = ROOT / "scripts/run_domain_validation_model_blind.py"
DATA_PATH = ROOT / "data/insightpilot-multidomain.sqlite"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reserve_usd(case_runs: int) -> float:
    per_request = (RESERVED_INPUT_TOKENS * PEAK_INPUT_USD_PER_MILLION +
                   MAX_OUTPUT_TOKENS * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000
    return round(case_runs * MAX_ATTEMPTS_PER_CASE * per_request, 8)


def _load_frozen_domains(lock: dict) -> tuple[list[dict], dict[str, dict]]:
    cases: list[dict] = []
    gold: dict[str, dict] = {}
    for domain in DOMAINS:
        prefix = SET_ROOT / domain
        blind_path = prefix.with_name(f"{domain}-blind-v1.jsonl")
        gold_path = prefix.with_name(f"{domain}-gold-v1.jsonl")
        manifest_path = prefix.with_name(f"{domain}-manifest-v1.json")
        files = lock["sets"][domain]
        for path, key in ((blind_path, "blind_sha256"), (gold_path, "gold_sha256"),
                          (manifest_path, "manifest_sha256")):
            if _sha(path) != files[key]:
                raise SystemExit(f"{domain}: locked hash mismatch at {path}; no request sent")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["gold_method"].find("Python") < 0:
            raise SystemExit(f"{domain}: gold is not independent Python oracle; no request sent")
        cases.extend(json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip())
        gold.update({row["case_id"]: row for row in
                     (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()
                      if line.strip())})
    if len(cases) != lock["total_cases"] or set(row["case_id"] for row in cases) != set(gold):
        raise SystemExit("Locked case count or gold IDs differ; no request sent")
    return cases, gold


def _preflight() -> tuple[dict, object, list[dict], dict[str, dict], str, float]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    settings = load_settings(ROOT)
    origin = f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}"
    configured = lock["model_settings"]
    if (settings.model != configured["model"] or origin != configured["base_url_origin"] or
            settings.max_completion_tokens != configured["max_completion_tokens"] or
            configured["temperature"] != 0):
        raise SystemExit("Model configuration differs from preregistration; no request sent")
    if (_sha(SCORER_PATH) != lock["scorer"]["sha256"] or
            _sha(PLANNER_PATH) != lock["planner_prompt"]["file_sha256"] or
            _sha(HARNESS_PATH) != lock["harness_sha256"]):
        raise SystemExit("Locked scorer/prompt implementation changed; no request sent")
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    remaining = float(ledger["remaining_global_cap_after_ip4_model_usd"])
    cases, gold = _load_frozen_domains(lock)
    reserve = _reserve_usd(lock["total_cases"] * BACKEND_RUNS)
    if reserve != lock["worst_case_reserve_usd"] or reserve > remaining:
        raise SystemExit("Worst-case reserve exceeds remaining cap; no request sent")
    sealed_ip4 = lock["sealed_old_ip4"]
    if (_sha(ROOT / "evals/ip4-blind-v1.jsonl") != sealed_ip4["blind_sha256"] or
            _sha(ROOT / sealed_ip4["seal_file"]) != sealed_ip4["sha256"]):
        raise SystemExit("Old IP4 freeze mismatch; stop before request")
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise SystemExit("Set INSIGHTPILOT_IP4_APP_DSN to the PG app-role DSN; no request sent")
    return lock, settings, cases, gold, dsn, remaining


def _evaluate_arm(backend: str, tenant_id: str | None, cases: list[dict],
                  gold: dict[str, dict], settings, dsn: str | None) -> tuple[dict, int, int, int, int]:
    engine = (SQLiteQueryEngine(DATA_PATH) if backend == "sqlite" else
              PostgresQueryEngine(dsn or "", tenant_id=tenant_id))
    core = InsightPilot(RulePlanner(), DATA_PATH, query_engine=engine, tenant_id=tenant_id)
    details = []
    call_count = prompt_tokens = completion_tokens = missing_usage = 0
    try:
        for case in cases:
            planner = OpenAICompatiblePlanner(settings, max_prompt_bytes_per_call=MAX_PROMPT_BYTES)
            core.planner = planner
            result = core.analyze(case["question"], tenant_id=tenant_id)
            score = compare(case, gold[case["case_id"]], result)
            calls = result.get("model_calls", [])
            if len(calls) > MAX_ATTEMPTS_PER_CASE:
                raise RuntimeError("Planner exceeded preregistered per-case request cap")
            call_count += len(calls)
            for call in calls:
                usage = call.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
                    prompt_tokens += usage["prompt_tokens"]
                    completion_tokens += usage["completion_tokens"]
                else:
                    missing_usage += 1
            details.append({
                **score,
                "question": case["question"],
                "oracle_gold": gold[case["case_id"]],
                "actual_stage": result.get("stage"),
                "actual_reason": result.get("reason"),
                "actual_plan": result.get("plan"),
                "actual_metric_id": result.get("metric", {}).get("id"),
                "compiled_sql": result.get("sql"),
                "parameter_count": len(result.get("parameters", [])) if result.get("sql") else None,
                "sql_sha256": hashlib.sha256(result["sql"].encode("utf-8")).hexdigest()
                    if result.get("sql") else None,
                "verification_passed": result.get("verification", {}).get("passed"),
                "failure_detail": result.get("failure_detail"),
                "model_calls": len(calls),
                "call_usage": calls,
            })
    finally:
        close_engine = getattr(engine, "close", None)
        if close_engine is not None:
            close_engine()

    answerable = {case["case_id"] for case in cases if gold[case["case_id"]]["expected_status"] == "answered"}
    guard_ids = {case["case_id"] for case in cases} - answerable
    result = {
        "backend": backend, "tenant_id": tenant_id,
        "passed": sum(row["passed"] for row in details), "total": len(details),
        "answerable_passed": sum(row["passed"] for row in details if row["case_id"] in answerable),
        "answerable_total": len(answerable),
        "guard_passed": sum(row["passed"] for row in details if row["case_id"] in guard_ids),
        "guard_total": len(guard_ids),
        "correct_refusals": sum(row["actual_status"] == "refused" and row["reason_ok"]
                                 for row in details if row["case_id"] in guard_ids),
        "false_refusals": [row["case_id"] for row in details
                            if row["case_id"] in answerable and row["actual_status"] == "refused"],
        "false_accepts": [row["case_id"] for row in details
                           if row["case_id"] in guard_ids and row["actual_status"] == "answered"],
        "failures": [row for row in details if not row["passed"]],
        "cases": details,
    }
    return result, call_count, prompt_tokens, completion_tokens, missing_usage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    lock, settings, cases, gold, dsn, remaining = _preflight()
    reserve = lock["worst_case_reserve_usd"]
    if args.preflight_only:
        print(json.dumps({"preflight": "passed", "model": settings.model,
                          "case_runs": len(cases) * BACKEND_RUNS,
                          "maximum_api_calls": len(cases) * BACKEND_RUNS * MAX_ATTEMPTS_PER_CASE,
                          "worst_case_reserve_usd": reserve,
                          "remaining_before_usd": remaining,
                          "remaining_after_reserve_usd": round(remaining - reserve, 8)}, indent=2))
        return

    arms = [("sqlite", None), ("postgresql", "tenant-a"), ("postgresql", "tenant-b")]
    runs = []
    call_count = prompt_tokens = completion_tokens = missing_usage = 0
    for backend, tenant_id in arms:
        arm, calls, prompt, completion, missing = _evaluate_arm(
            backend, tenant_id, cases, gold, settings, dsn if backend == "postgresql" else None)
        runs.append(arm)
        call_count += calls
        prompt_tokens += prompt
        completion_tokens += completion
        missing_usage += missing

    peak_cost = (prompt_tokens * PEAK_INPUT_USD_PER_MILLION +
                 completion_tokens * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000
    missing_reserve = missing_usage * (
        RESERVED_INPUT_TOKENS * PEAK_INPUT_USD_PER_MILLION +
        MAX_OUTPUT_TOKENS * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000
    output = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "model": settings.model,
        "model_response_aliases": sorted({call.get("response_model") for run in runs
            for case in run["cases"] for call in case["call_usage"] if call.get("response_model")}),
        "prompt_sha256": lock["planner_prompt"]["file_sha256"],
        "scorer_sha256": lock["scorer"]["sha256"],
        "set_manifest_sha256": {domain: lock["sets"][domain]["manifest_sha256"] for domain in DOMAINS},
        "max_attempts_per_case": MAX_ATTEMPTS_PER_CASE,
        "prompt_byte_cap": MAX_PROMPT_BYTES,
        "output_token_cap": settings.max_completion_tokens,
        "global_remaining_before_usd": remaining,
        "worst_case_reserve_usd": reserve,
        "global_remaining_after_reserve_usd": round(remaining - reserve, 8),
        "observed_calls": call_count,
        "observed_prompt_tokens": prompt_tokens,
        "observed_completion_tokens": completion_tokens,
        "usage_missing_calls": missing_usage,
        "peak_list_price_estimate_usd": round(peak_cost, 8),
        "missing_usage_worst_case_reserve_usd": round(missing_reserve, 8),
        "estimated_max_charge_for_run_usd": round(peak_cost + missing_reserve, 8),
        "runs": runs,
        "old_ip4_touched": False,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "runs"},
                     ensure_ascii=False, indent=2))
    for arm in runs:
        print(f"{arm['backend']} {arm['tenant_id'] or '-'}: {arm['passed']}/{arm['total']}; "
              f"false refusals={len(arm['false_refusals'])}, false accepts={len(arm['false_accepts'])}")


if __name__ == "__main__":
    main()
