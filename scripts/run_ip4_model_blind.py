"""Run the frozen IP4 holdout once through the locked model on both query backends."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.agent import InsightPilot
from insightpilot.config import load_settings
from insightpilot.freeze import sha256_text
from insightpilot.ip4_evaluate import compare
from insightpilot.planner import OpenAICompatiblePlanner
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals/ip4-manifest-v1.json"
BLIND = ROOT / "evals/ip4-blind-v1.jsonl"
GOLD = ROOT / "evals/ip4-gold-v1.jsonl"
LOCK = ROOT / "reports/ip4-model-preregistration.json"
OUTPUT = ROOT / "reports/ip4-model-blind-verification.json"
PEAK_INPUT_USD_PER_MILLION = 0.30
PEAK_OUTPUT_USD_PER_MILLION = 1.20
MAX_PROMPT_BYTES = 51_000
MAX_RESERVED_INPUT_TOKENS = 52_000
MAX_OUTPUT_TOKENS = 1_024
MAX_ATTEMPTS_PER_CASE = 2
BACKEND_RUNS = 3  # SQLite once, PostgreSQL once per seeded tenant.


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _max_reserve_usd(case_count: int) -> float:
    per_request = (MAX_RESERVED_INPUT_TOKENS * PEAK_INPUT_USD_PER_MILLION +
                   MAX_OUTPUT_TOKENS * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000
    return round(case_count * BACKEND_RUNS * MAX_ATTEMPTS_PER_CASE * per_request, 8)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate frozen hashes, model, and budget without sending requests")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if _file_hash(BLIND) != manifest["blind_sha256"] or _file_hash(GOLD) != manifest["gold_sha256"]:
        raise SystemExit("Frozen IP4 blind/gold hash mismatch; no model request sent.")
    if manifest["data_version"] != lock["data_version"] or manifest["data_fingerprint"] != lock["data_fingerprint"]:
        raise SystemExit("Frozen IP4 dataset identity differs from preregistration; no request sent.")

    settings = load_settings(ROOT)
    expected_settings = lock["model_settings"]
    if (settings.model != expected_settings["model"] or
            f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}" !=
            expected_settings["base_url_origin"] or
            settings.max_completion_tokens != expected_settings["max_completion_tokens"]):
        raise SystemExit("Configured model settings differ from preregistration; no request sent.")
    if settings.max_completion_tokens > MAX_OUTPUT_TOKENS:
        raise SystemExit("Configured output cap exceeds preregistration; no request sent.")

    ledger = json.loads((ROOT / "reports/model-experiment-cost-ledger.json").read_text(encoding="utf-8"))
    remaining = float(ledger["v7_independent_holdout"]["remaining_global_cap_after_run_usd"])
    cases = [json.loads(line) for line in BLIND.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in
            (json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip())}
    reserve = _max_reserve_usd(len(cases))
    if len(cases) != manifest["case_count"] or remaining + 1e-12 < reserve:
        raise SystemExit("Case count or global budget check failed; no model request sent.")

    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise SystemExit("Set INSIGHTPILOT_IP4_APP_DSN to the non-superuser PG test role; no model request sent.")
    if args.preflight_only:
        print(json.dumps({"preflight": "passed", "model": settings.model,
                          "case_runs": len(cases) * BACKEND_RUNS,
                          "maximum_api_calls": len(cases) * BACKEND_RUNS * MAX_ATTEMPTS_PER_CASE,
                          "worst_case_reserve_usd": reserve,
                          "remaining_after_reserve_usd": round(remaining - reserve, 8)}, indent=2))
        return

    runs = []
    call_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    usage_missing_calls = 0

    def evaluate_run(backend: str, tenant_id: str | None, engine) -> None:
        nonlocal call_count, prompt_tokens, completion_tokens, usage_missing_calls
        details = []
        for case in cases:
            planner = OpenAICompatiblePlanner(settings, max_prompt_bytes_per_call=MAX_PROMPT_BYTES)
            core = InsightPilot(planner, ROOT / "data/insightpilot-multidomain.sqlite",
                                query_engine=engine, tenant_id=tenant_id)
            result = core.analyze(case["question"], tenant_id=tenant_id)
            score = compare(case, gold[case["case_id"]], result)
            calls = result.get("model_calls", [])
            if len(calls) > MAX_ATTEMPTS_PER_CASE:
                raise RuntimeError("Planner exceeded preregistered per-case request cap.")
            call_count += len(calls)
            for call in calls:
                usage = call.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
                    prompt_tokens += usage["prompt_tokens"]
                    completion_tokens += usage["completion_tokens"]
                else:
                    usage_missing_calls += 1
            details.append({**score, "model_calls": len(calls), "call_usage": calls,
                            "terminal_stage": result.get("stage"),
                            "terminal_reason": result.get("reason")})
        close_engine = getattr(engine, "close", None)
        if close_engine is not None:
            close_engine()
        runs.append({"backend": backend, "tenant_id": tenant_id,
                     "passed": sum(row["passed"] for row in details), "total": len(details),
                     "answerable_passed": sum(row["passed"] for row in details
                         if gold[row["case_id"]]["expected_status"] == "answered"),
                     "answerable_total": sum(row["expected_status"] == "answered" for row in gold.values()),
                     "guard_passed": sum(row["passed"] for row in details
                         if gold[row["case_id"]]["expected_status"] != "answered"),
                     "guard_total": sum(row["expected_status"] != "answered" for row in gold.values()),
                     "failures": [row for row in details if not row["passed"]],
                     "cases": details})

    evaluate_run("sqlite", None, SQLiteQueryEngine(ROOT / "data/insightpilot-multidomain.sqlite"))
    for tenant in ("tenant-a", "tenant-b"):
        evaluate_run("postgresql", tenant, PostgresQueryEngine(dsn, tenant_id=tenant))

    actual_peak_cost = ((prompt_tokens * PEAK_INPUT_USD_PER_MILLION +
                         completion_tokens * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000)
    missing_usage_reserve = usage_missing_calls * ((
        MAX_RESERVED_INPUT_TOKENS * PEAK_INPUT_USD_PER_MILLION +
        MAX_OUTPUT_TOKENS * PEAK_OUTPUT_USD_PER_MILLION) / 1_000_000)
    result = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "model": settings.model,
        "data_version": manifest["data_version"],
        "data_fingerprint": manifest["data_fingerprint"],
        "manifest_sha256": _file_hash(MANIFEST),
        "blind_sha256": _file_hash(BLIND),
        "gold_sha256": _file_hash(GOLD),
        "scorer": lock["scorer"],
        "max_attempts_per_case": MAX_ATTEMPTS_PER_CASE,
        "prompt_byte_cap": MAX_PROMPT_BYTES,
        "output_token_cap": settings.max_completion_tokens,
        "global_remaining_before_usd": remaining,
        "worst_case_reserve_usd": reserve,
        "global_remaining_after_reserve_usd": round(remaining - reserve, 8),
        "observed_calls": call_count,
        "observed_prompt_tokens": prompt_tokens,
        "observed_completion_tokens": completion_tokens,
        "usage_missing_calls": usage_missing_calls,
        "peak_list_price_estimate_usd": round(actual_peak_cost, 8),
        "missing_usage_worst_case_reserve_usd": round(missing_usage_reserve, 8),
        "estimated_max_charge_for_run_usd": round(actual_peak_cost + missing_usage_reserve, 8),
        "runs": runs,
        "model_response_alias_note": "DeepSeek documents legacy deepseek-v4-flash as accepted and billed at Flash rates.",
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "runs"}, ensure_ascii=False, indent=2))
    for run in runs:
        print(f"{run['backend']} {run['tenant_id'] or '-'}: {run['passed']}/{run['total']} "
              f"(answerable {run['answerable_passed']}/{run['answerable_total']}, "
              f"guards {run['guard_passed']}/{run['guard_total']})")


if __name__ == "__main__":
    main()
