"""Run the frozen v4 benchmark with the LLM planner or fixed-rule baseline."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .agent import InsightPilot
from .config import load_settings
from .enterprise_evaluate import _compare
from .freeze import sha256_text
from .planner import OpenAICompatiblePlanner
from .rule_baseline import RulePlanner
from .runner import read_data_identity


PRICING = {
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "model_alias": "deepseek-v4-flash (legacy alias; provider routes to V4.1 Flash)",
    "rates_usd_per_million": {
        "peak": {"cache_hit_input": 0.006, "cache_miss_input": 0.30, "output": 1.20},
        "off_peak": {"cache_hit_input": 0.003, "cache_miss_input": 0.15, "output": 0.60},
    },
}


def _percentile(values: list[float], p: float):
    if not values:
        return None
    values = sorted(values)
    return round(values[max(0, math.ceil(p * len(values)) - 1)], 3)


def _is_peak(created_at: int | None) -> bool | None:
    if created_at is None:
        return None
    moment = datetime.fromtimestamp(created_at, timezone.utc)
    return moment.weekday() < 5 and (1 <= moment.hour < 4 or 6 <= moment.hour < 10)


def _usage_and_cost(rows: list[dict]):
    calls = [call for row in rows for call in row["result"].get("model_calls", [])]
    latencies = [call["latency_ms"] for call in calls if isinstance(call.get("latency_ms"), (int, float))]
    prompt = completion = total = hit = miss = usage_count = 0
    cost = 0.0
    all_priced = bool(calls)
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, dict):
            all_priced = False
            continue
        p, c, t = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")
        if not all(isinstance(value, int) for value in (p, c, t)):
            all_priced = False
            continue
        prompt += p
        completion += c
        total += t
        usage_count += 1
        cached, uncached = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
        peak = _is_peak(call.get("created_at"))
        if isinstance(cached, int) and isinstance(uncached, int) and cached + uncached == p and peak is not None:
            hit += cached
            miss += uncached
            rates = PRICING["rates_usd_per_million"]["peak" if peak else "off_peak"]
            cost += (cached * rates["cache_hit_input"] + uncached * rates["cache_miss_input"] +
                     c * rates["output"]) / 1_000_000
        else:
            all_priced = False
    return {
        "api_request_attempts": len(calls), "responses_with_token_usage": usage_count,
        "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
        "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": miss,
        "model_request_latency_ms_p50": _percentile(latencies, .5),
        "model_request_latency_ms_p95": _percentile(latencies, .95),
        "estimated_list_price_usd": round(cost, 8) if all_priced else None,
        "price_estimate_complete": all_priced, "pricing_basis": PRICING,
    }


def _reserve_experiment_budget(root: Path, settings, output: Path, case_count: int,
                               run_key: str = "current_v4_run") -> tuple[Path, dict, float]:
    ledger_path = root / "reports/model-experiment-cost-ledger.json"
    if not ledger_path.is_file():
        raise ValueError("model cost ledger missing; refuse unbudgeted API calls")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger.get(run_key, {}).get("status") in ("reserved", "completed", "stopped_budget_guard"):
        raise ValueError(f"{run_key} already reserved or completed; refusing duplicate calls")
    raw_path = output / ("diagnostic-raw.jsonl" if run_key == "post_fix_smoke" else "model-raw.jsonl")
    if raw_path.exists():
        raise ValueError("model raw output already exists; refusing to repeat billable calls")
    cap = float(ledger["cap_usd"])
    previous = float(ledger["prior_experiment_reserve_usd"])
    for key in ("current_v4_run", "post_fix_smoke"):
        if key == run_key:
            continue
        prior_run = ledger.get(key, {})
        if prior_run.get("status") == "completed":
            previous += float(prior_run.get("used_worst_case_reserve_usd", 0))
        elif prior_run.get("status") in ("reserved", "stopped_budget_guard"):
            previous += float(prior_run.get("reserved_usd", 0))
    prompt_ceiling = int(ledger["max_prompt_tokens_per_request"])
    max_attempts_per_case = int(ledger["max_attempts_per_case"])
    unit_reserve = (prompt_ceiling * .30 + settings.max_completion_tokens * 1.20) / 1_000_000
    reserve = case_count * max_attempts_per_case * unit_reserve
    if previous + reserve > cap:
        raise ValueError("projected maximum API cost exceeds remaining experiment budget")
    ledger[run_key] = {
        "status": "reserved", "case_count": case_count, "model": settings.model,
        "max_completion_tokens": settings.max_completion_tokens,
        "per_request_prompt_ceiling": prompt_ceiling, "max_attempts_per_case": max_attempts_per_case,
        "reserved_usd": round(reserve, 8), "reserved_remaining_after_history_usd": round(cap - previous, 8),
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ledger_path, ledger, unit_reserve


def run(mode: str, root: Path = Path("."), output: Path = Path("reports/current-benchmark"),
        case_ids: list[str] | None = None) -> dict:
    settings = load_settings(root)
    database = (root / "data/insightpilot-enterprise.sqlite").resolve()
    if not database.is_file():
        raise ValueError("企业评测数据库不存在；先运行 python -m data.enterprise_seed。")
    data_version, fingerprint = read_data_identity(database)
    blind_path = root / "evals/enterprise-blind-v4.jsonl"
    gold_path = root / "evals/enterprise-gold-v4.jsonl"
    manifest_path = root / "evals/enterprise-holdout-manifest-v4.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if (data_version != manifest["data_version"] or fingerprint != manifest["data_fingerprint"] or
            manifest["case_count"] != len(cases) or len(gold_rows) != len(cases) or
            manifest["blind_questions_sha256"] != sha256_text(blind_path) or
            manifest["gold_sha256"] != sha256_text(gold_path)):
        raise ValueError("frozen questions, gold, manifest, or dataset fingerprint do not match")
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    if {case["case_id"] for case in cases} != set(gold_by_id):
        raise ValueError("question and gold case IDs do not match")
    if mode == "diagnostic":
        if not case_ids or len(set(case_ids)) != len(case_ids) or not set(case_ids).issubset(gold_by_id):
            raise ValueError("diagnostic mode requires unique case IDs from the frozen holdout")
        cases = [case for case in cases if case["case_id"] in set(case_ids)]
    elif case_ids:
        raise ValueError("case IDs are only valid in diagnostic mode")

    output.mkdir(parents=True, exist_ok=True)
    run_key = "post_fix_smoke" if mode == "diagnostic" else "current_v4_run"
    budget = _reserve_experiment_budget(root, settings, output, len(cases), run_key) if mode in ("model", "diagnostic") else None
    rows = []
    for case in cases:
        planner = RulePlanner() if mode == "baseline" else OpenAICompatiblePlanner(settings)
        result = InsightPilot(planner, database).analyze(case["question"])
        comparison = _compare(case, gold_by_id[case["case_id"]], result)
        rows.append({**comparison, "question": case["question"], "result": result})
        raw_name = "model-raw.jsonl" if mode == "model" else f"{mode}-raw.jsonl"
        (output / raw_name).write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        if mode in ("model", "diagnostic"):
            calls = result.get("model_calls", [])
            prompt_ceiling = budget[1]["max_prompt_tokens_per_request"]
            completion_ceiling = settings.max_completion_tokens
            invalid_usage = any((call.get("usage") or {}).get("prompt_tokens", 0) > prompt_ceiling or
                                (call.get("usage") or {}).get("completion_tokens", 0) > completion_ceiling
                                for call in calls)
            if len(calls) > budget[1]["max_attempts_per_case"] or invalid_usage:
                ledger = json.loads(budget[0].read_text(encoding="utf-8"))
                ledger[run_key].update({"status": "stopped_budget_guard", "calls_seen": len(calls)})
                budget[0].write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                raise ValueError("observed model request exceeded the reserved token or retry ceiling")
            actual_attempts = sum(item["result"].get("model_call_count", 0) for item in rows)
            used_reserve = actual_attempts * budget[2]
            ledger = json.loads(budget[0].read_text(encoding="utf-8"))
            ledger[run_key].update({"calls_observed": actual_attempts,
                                    "used_worst_case_reserve_usd": round(used_reserve, 8)})
            budget[0].write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{mode}: {len(rows)}/{len(cases)} {case['case_id']} {result.get('status')} "
              f"calls={result.get('model_call_count', 0)}", flush=True)

    status_counts = Counter(row["result"].get("status", "missing") for row in rows)
    called = [row for row in rows if row["result"].get("model_call_count", 0) > 0]
    guard_ids = set(manifest["deterministic_guard_case_ids"])
    expected_guard = [row for row in rows if row["case_id"] in guard_ids]
    guard = [row for row in expected_guard if row["result"].get("model_call_count", 0) == 0]
    expected_answerable = [row for row in rows if gold_by_id[row["case_id"]].get("expected_status", "answered") == "answered"]
    model_answered = [row for row in called if row["result"].get("status") == "answered"]
    expected_clarify = [row for row in rows if gold_by_id[row["case_id"]].get("expected_status") == "clarify"]
    expected_refused = [row for row in rows if gold_by_id[row["case_id"]].get("expected_status") == "refused"]
    model_clarified = [row for row in called if row["result"].get("status") == "clarify"]
    model_refused = [row for row in called if row["result"].get("status") == "refused"]
    no_model_answered = [row for row in rows if row["result"].get("status") == "answered" and
                         row["result"].get("model_call_count", 0) == 0]
    elapsed = [row["result"]["elapsed_ms"] for row in rows if isinstance(row["result"].get("elapsed_ms"), (int, float))]
    failure_groups = Counter((row["result"].get("status", "missing"), row["result"].get("stage", "guard"),
                              row["result"].get("reason", "unspecified")) for row in rows if not row["passed"])
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "model_requested": settings.model if mode != "baseline" else None,
        "holdout_id": manifest["holdout_id"], "question_sha256": manifest["blind_questions_sha256"],
        "gold_sha256": manifest["gold_sha256"], "data_version": data_version, "data_fingerprint": fingerprint,
        "case_count": len(rows), "attempt_count": len(rows), "passed": sum(row["passed"] for row in rows),
        "case_accuracy": round(sum(row["passed"] for row in rows) / len(rows), 4),
        "expected_answerable_count": len(expected_answerable),
        "expected_answerable_passed": sum(row["passed"] for row in expected_answerable),
        "expected_guard_count": len(expected_guard), "actual_guard_handled_count": len(guard),
        "guard_passed": sum(row["passed"] for row in expected_guard),
        "actual_rule_answer_cases": len(no_model_answered),
        "actual_model_call_cases": len(called),
        "actual_model_answer_cases": len(model_answered),
        "actual_model_answer_passed": sum(row["passed"] for row in model_answered),
        "actual_model_clarifications": len(model_clarified), "actual_model_refusals": len(model_refused),
        "status_counts": dict(status_counts),
        "clarify_count": status_counts["clarify"], "refused_count": status_counts["refused"],
        "expected_clarify_count": len(expected_clarify), "clarify_passed": sum(row["passed"] for row in expected_clarify),
        "expected_refusal_count": len(expected_refused), "refusal_passed": sum(row["passed"] for row in expected_refused),
        "sql_verified_answers": sum(row["result"].get("verification", {}).get("passed") is True for row in rows),
        "elapsed_ms_p50": _percentile(elapsed, .5), "elapsed_ms_p95": _percentile(elapsed, .95),
        "failures_by_status_stage_reason": [
            {"status": key[0], "stage": key[1], "reason": key[2], "count": count}
            for key, count in sorted(failure_groups.items())],
        "cases": [{key: row.get(key) for key in ("case_id", "passed", "status_ok", "reason_ok", "metric_ok",
                                                   "dimension_ok", "region_ok", "date_window_ok", "numeric_ok", "sql_verified")}
                  | {"status": row["result"].get("status"), "stage": row["result"].get("stage"),
                     "reason": row["result"].get("reason"), "model_call_count": row["result"].get("model_call_count", 0)}
                  for row in rows],
    }
    if mode in ("model", "diagnostic"):
        costs = _usage_and_cost(rows)
        summary["model_usage_and_cost"] = costs
        ledger = json.loads(budget[0].read_text(encoding="utf-8"))
        ledger[run_key].update({"status": "completed", "actual_attempts": costs["api_request_attempts"],
                                "actual_estimated_list_price_usd": costs["estimated_list_price_usd"],
                                "reserved_worst_case_usd": round(costs["api_request_attempts"] * budget[2], 8)})
        used = float(ledger["prior_experiment_reserve_usd"])
        for key in ("current_v4_run", "post_fix_smoke"):
            if ledger.get(key, {}).get("status") == "completed":
                used += float(ledger[key].get("used_worst_case_reserve_usd", 0))
        ledger[run_key]["remaining_unreserved_budget_usd"] = round(float(ledger["cap_usd"]) - used, 8)
        budget[0].write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / f"{mode}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def replay(root: Path = Path("."), output: Path = Path("reports/current-benchmark")) -> dict:
    """Re-score saved model outputs against frozen gold without making API requests."""
    database = (root / "data/insightpilot-enterprise.sqlite").resolve()
    data_version, fingerprint = read_data_identity(database)
    blind_path = root / "evals/enterprise-blind-v4.jsonl"
    gold_path = root / "evals/enterprise-gold-v4.jsonl"
    manifest_path = root / "evals/enterprise-holdout-manifest-v4.json"
    raw_path, summary_path = output / "model-raw.jsonl", output / "model-summary.json"
    ledger_path = root / "reports/model-experiment-cost-ledger.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in
            (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if (data_version != manifest["data_version"] or fingerprint != manifest["data_fingerprint"] or
            manifest["blind_questions_sha256"] != sha256_text(blind_path) or
            manifest["gold_sha256"] != sha256_text(gold_path) or len(cases) != manifest["case_count"] or
            len(rows) != len(cases) or len(gold) != len(cases) or
            ledger.get("current_v4_run", {}).get("status") != "completed"):
        raise ValueError("saved evaluation does not match its frozen inputs or completed cost ledger")
    case_by_id = {case["case_id"]: case for case in cases}
    seen = set()
    failures = []
    for row in rows:
        case_id = row.get("case_id")
        if case_id not in case_by_id or case_id in seen or row.get("question") != case_by_id[case_id]["question"]:
            raise ValueError("saved evaluation has duplicate, unknown, or changed questions")
        seen.add(case_id)
        checked = _compare(case_by_id[case_id], gold[case_id], row["result"])
        if checked["passed"] != row.get("passed"):
            raise ValueError(f"saved score differs from strict comparator for {case_id}")
        if not checked["passed"]:
            failures.append({"case_id": case_id, "status": row["result"].get("status"),
                             "dimension_ok": checked.get("dimension_ok"), "numeric_ok": checked.get("numeric_ok"),
                             "reason_ok": checked.get("reason_ok")})
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["passed"] != sum(row["passed"] for row in rows) or summary["attempt_count"] != len(rows):
        raise ValueError("saved summary does not match raw model results")
    response_calls = sum(row["result"].get("model_call_count", 0) for row in rows)
    if response_calls != ledger["current_v4_run"].get("actual_attempts"):
        raise ValueError("model request count differs from the budget ledger")
    result = {"replayed": True, "api_calls_made": 0, "case_count": len(rows),
              "passed": sum(row["passed"] for row in rows), "failures": failures,
              "api_request_attempts": response_calls,
              "estimated_list_price_usd": summary["model_usage_and_cost"]["estimated_list_price_usd"],
              "question_sha256": manifest["blind_questions_sha256"], "gold_sha256": manifest["gold_sha256"],
              "data_fingerprint": fingerprint}
    (output / "replay-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen v4 enterprise benchmark")
    parser.add_argument("--mode", choices=("model", "baseline", "diagnostic", "replay"), required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/current-benchmark"))
    parser.add_argument("--case-ids", nargs="+", default=None)
    args = parser.parse_args()
    if args.mode == "replay":
        if args.case_ids:
            parser.error("--case-ids cannot be used with replay mode")
        result = replay(Path.cwd(), args.output)
    else:
        result = run(args.mode, Path.cwd(), args.output, args.case_ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
