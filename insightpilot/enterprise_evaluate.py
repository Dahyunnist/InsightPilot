"""Evaluate prompt-blind enterprise holdout questions against separate Python gold."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import random
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import replace

from .agent import InsightPilot, _preflight
from .config import load_settings
from .freeze import sha256_text
from .metrics import REGION_VALUES
from .planner import OpenAICompatiblePlanner
from .runner import execute_readonly, read_data_identity
from .sql import compile_query


def _matches(actual, expected, tolerance: float = 1e-6) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance)
    return actual == expected


def _compare(case: dict, gold: dict, result: dict) -> dict:
    expected_status = gold.get("expected_status", "answered")
    if expected_status != "answered":
        status_ok = result.get("status") == expected_status
        reason_ok = "expected_reason" not in gold or result.get("reason") == gold["expected_reason"]
        return {"case_id": case["case_id"], "status_ok": status_ok, "reason_ok": reason_ok,
                "expected_status": expected_status,
                "metric_ok": None, "dimension_ok": None, "region_ok": None, "numeric_ok": None,
                "date_window_ok": None, "sql_verified": False,
                "reward": 1.0 if status_ok and reason_ok else 0.0,
                "passed": status_ok and reason_ok}
    expected_metric = gold["metric"]
    metric_ok = result.get("metric", {}).get("id") == expected_metric
    expected = gold["expected"]
    result_rows = result.get("rows", [])
    rows = {row.get("group_value"): row for row in result_rows}
    if set(expected) == {"全部"} and len(result_rows) == 1 and result_rows[0].get("group_value") is None:
        rows = {"全部": result_rows[0]}
    numeric_ok = set(rows) == set(expected) and all(
        all(_matches(rows[group].get(field), value) for field, value in row.items())
        for group, row in expected.items()
    )
    verification = result.get("verification", {}).get("passed") is True
    status_ok = result.get("status") == "answered"
    plan = result.get("plan", {})
    dimension_ok = plan.get("dimension") == gold.get("dimension")
    region_ok = plan.get("region") == gold.get("region")
    date_window_ok = ("analysis_start" not in gold or
                      (plan.get("start_date_inclusive") == gold["analysis_start"] and
                       plan.get("end_date_exclusive") == gold["analysis_end_exclusive"]))
    reward = (0.5 if metric_ok else 0) + (0.5 if result.get("plan", {}).get("dimension") == gold["dimension"] else 0)
    reward += 1.0 if numeric_ok else 0
    reward += 1.0 if verification else 0
    if not status_ok and result.get("status") in ("refused", "clarify"):
        reward += 0.25
    if result.get("stage") in ("query", "verification") and result.get("status") == "failed":
        reward -= 1.0
    reward += 0.5 if region_ok else 0
    return {"case_id": case["case_id"], "status_ok": status_ok,
            "metric_ok": metric_ok, "dimension_ok": dimension_ok,
            "region_ok": region_ok, "numeric_ok": numeric_ok, "sql_verified": verification,
            "date_window_ok": date_window_ok,
            "reward": round(reward, 2),
            "passed": status_ok and metric_ok and dimension_ok and numeric_ok and verification and region_ok and date_window_ok}


def _without_verification(planner, database_path, question: str) -> dict:
    started = time.perf_counter()
    denied = _preflight(question)
    if denied:
        return {**denied, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "model_usage": None}
    try:
        plan = planner.plan(question)
        compiled = compile_query(plan)
        rows, data_version = execute_readonly(database_path, compiled)
        return {"status": "answered", "metric": {"id": plan.metric_id},
                "plan": {"dimension": plan.dimension, "region": plan.region},
                "rows": rows, "data_version": data_version, "verification": {"passed": None, "disabled": True},
                "parameters": list(compiled.params),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "model_usage": planner.last_call_usage}
    except Exception as exc:
        from .planner import NeedsClarification
        if isinstance(exc, NeedsClarification):
            return {"status": "clarify", "stage": "planning", "reason": type(exc).__name__,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "model_usage": planner.last_call_usage}
        return {"status": "failed", "stage": "ablation", "reason": type(exc).__name__,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "model_usage": planner.last_call_usage}


def _compare_without_verification(case: dict, gold: dict, result: dict) -> tuple[dict, dict | None]:
    audited_result = result
    adjustment = None
    if gold.get("region") and result.get("plan", {}).get("region") is None:
        bound_regions = [value for value in result.get("parameters", []) if value in REGION_VALUES]
        if len(bound_regions) == 1:
            audited_result = {**result, "plan": {**result.get("plan", {}), "region": bound_regions[0]}}
            adjustment = {"field": "plan.region", "value": bound_regions[0],
                          "source": "region literal found in parameterized SQL bindings"}
    comparison = _compare(case, gold, {**audited_result, "verification": {"passed": True}})
    comparison["sql_verified"] = False
    if gold.get("expected_status", "answered") == "answered":
        comparison["reward"] = round(comparison["reward"] - 1.0, 2)
        comparison["passed"] = (result.get("status") == "answered" and comparison["metric_ok"] and
                                 comparison["dimension_ok"] and comparison["numeric_ok"] and comparison["region_ok"] and
                                 comparison["date_window_ok"])
    return comparison, adjustment


def _distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(values), "min": round(ordered[0], 3),
            "median": round(statistics.median(ordered), 3),
            "p95": round(ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 3),
            "max": round(ordered[-1], 3), "mean": round(statistics.mean(values), 3)}


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total <= 0:
        return None
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def _cluster_bootstrap_ci(results: list[dict], seed: int = 91427, iterations: int = 5000) -> list[float] | None:
    by_case: dict[str, list[int]] = {}
    for row in results:
        by_case.setdefault(row["case_id"], []).append(int(row["passed"]))
    if len(by_case) < 2:
        return None
    case_rates = [statistics.mean(scores) for scores in by_case.values()]
    rng = random.Random(seed)
    means = [statistics.mean(rng.choices(case_rates, k=len(case_rates))) for _ in range(iterations)]
    means.sort()
    return [round(means[int(.025 * iterations)], 4), round(means[min(iterations - 1, int(.975 * iterations))], 4)]


def run(output: Path, root: Path, database: Path | None = None, repeats: int = 1,
        blind_path: Path | None = None, gold_path: Path | None = None, reuse_existing: bool = False) -> dict:
    if repeats < 1 or repeats > 10:
        raise ValueError("repeats must be between 1 and 10")
    settings = load_settings(root)
    if database is not None:
        settings = replace(settings, database_path=database.resolve())
    if not settings.database_path.is_file():
        raise ValueError("企业评测数据库不存在；先运行 python -m data.enterprise_seed。")
    dataset_version, data_fingerprint = read_data_identity(settings.database_path)
    if dataset_version != "insightpilot-enterprise-v1":
        raise ValueError("企业 holdout 只适用于 insightpilot-enterprise-v1。")
    blind_path = blind_path or (root / "evals/enterprise-blind-v1.jsonl")
    gold_path = gold_path or blind_path.with_name(blind_path.name.replace("blind", "gold"))
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    output.mkdir(parents=True, exist_ok=True)
    if set(gold_by_id) != {case["case_id"] for case in cases}:
        raise ValueError("holdout and oracle case IDs do not match")
    manifest_path = blind_path.with_name(blind_path.stem.replace("blind", "holdout-manifest") + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["data_fingerprint"] != data_fingerprint or manifest["case_count"] != len(cases):
        raise ValueError("holdout manifest does not match dataset or case count")
    if (manifest["blind_questions_sha256"] != sha256_text(blind_path) or
            manifest["gold_sha256"] != sha256_text(gold_path)):
        raise ValueError("holdout files differ from frozen manifest hashes")
    main_results, latencies, model_latencies, tokens = [], [], [], []
    main_path = output / "enterprise-evaluation-raw.jsonl"
    if reuse_existing and main_path.exists():
        existing = [json.loads(line) for line in main_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expected_keys = {(case["case_id"], repeat) for repeat in range(1, repeats + 1) for case in cases}
        if ({(row["case_id"], row["repeat_index"]) for row in existing} != expected_keys or any(
                row.get("question") != next(c["question"] for c in cases if c["case_id"] == row["case_id"])
                for row in existing)):
            raise ValueError("existing main-arm records do not match this frozen holdout")
        for row in existing:
            case = next(c for c in cases if c["case_id"] == row["case_id"])
            checked = _compare(case, gold_by_id[row["case_id"]], row["result"])
            if checked["passed"] != row["passed"]:
                raise ValueError("existing main-arm score differs from frozen oracle")
        main_results = existing
    else:
        for repeat_index in range(1, repeats + 1):
            for case in cases:
                result = InsightPilot(OpenAICompatiblePlanner(settings), settings.database_path).analyze(case["question"])
                comparison = _compare(case, gold_by_id[case["case_id"]], result)
                main_results.append({**comparison, "repeat_index": repeat_index,
                                     "question": case["question"], "result": result})
        main_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in main_results) + "\n", encoding="utf-8")
    for row in main_results:
        result = row["result"]
        if isinstance(result.get("elapsed_ms"), (int, float)):
            latencies.append(result["elapsed_ms"])
        if isinstance(result.get("model_latency_ms"), (int, float)):
            model_latencies.append(result["model_latency_ms"])
        usage = result.get("model_usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            tokens.append(usage["total_tokens"])

    gold_by_id = {row["case_id"]: row for row in gold_rows}
    no_retrieval, no_verifier = [], []
    no_retrieval_path, no_verifier_path = output / "enterprise-ablation-no-retrieval.jsonl", output / "enterprise-ablation-no-verifier.jsonl"
    reuse_ablation = reuse_existing and no_retrieval_path.exists() and no_verifier_path.exists()
    if reuse_ablation:
        no_retrieval = [json.loads(line) for line in no_retrieval_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        no_verifier = [json.loads(line) for line in no_verifier_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if {row["case_id"] for row in no_retrieval} != set(gold_by_id) or {row["case_id"] for row in no_verifier} != set(gold_by_id):
            raise ValueError("existing ablation records do not match this frozen holdout")
        for row in no_verifier:
            case = next(item for item in cases if item["case_id"] == row["case_id"])
            comparison, adjustment = _compare_without_verification(case, gold_by_id[row["case_id"]], row["result"])
            row.update(comparison)
            if adjustment:
                row["audit_adjustment"] = adjustment
        no_verifier_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in no_verifier) + "\n", encoding="utf-8")
    else:
        for case in cases:
            baseline_result = InsightPilot(OpenAICompatiblePlanner(settings, use_metric_retrieval=False), settings.database_path).analyze(case["question"])
            no_retrieval.append({**_compare(case, gold_by_id[case["case_id"]], baseline_result), "result": baseline_result})
            planner = OpenAICompatiblePlanner(settings)
            result = _without_verification(planner, settings.database_path, case["question"])
            comparison, adjustment = _compare_without_verification(case, gold_by_id[case["case_id"]], result)
            entry = {**comparison, "result": result}
            if adjustment:
                entry["audit_adjustment"] = adjustment
            no_verifier.append(entry)
        no_retrieval_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in no_retrieval) + "\n", encoding="utf-8")
        no_verifier_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in no_verifier) + "\n", encoding="utf-8")

    ordered = sorted(latencies)
    per_case = {}
    for case in cases:
        attempts = [row for row in main_results if row["case_id"] == case["case_id"]]
        per_case[case["case_id"]] = {"passed": sum(row["passed"] for row in attempts), "attempts": len(attempts),
                                      "pass_rate": sum(row["passed"] for row in attempts) / len(attempts),
                                      "statuses": {status: sum(row["result"].get("status") == status for row in attempts)
                                                   for status in sorted({row["result"].get("status", "missing") for row in attempts})},
                                      "outcomes_by_repeat": [{"repeat_index": row["repeat_index"], "passed": row["passed"],
                                                              "status": row["result"].get("status"),
                                                              "metric_id": row["result"].get("metric", {}).get("id"),
                                                              "dimension": row["result"].get("plan", {}).get("dimension"),
                                                              "region": row["result"].get("plan", {}).get("region"),
                                                              "numeric_ok": row["numeric_ok"],
                                                              "sql_verified": row["sql_verified"]} for row in attempts]}
    per_repeat = [{"repeat_index": repeat,
                   "passed": sum(row["passed"] for row in main_results if row["repeat_index"] == repeat),
                   "attempts": len(cases),
                   "failed_case_ids": [row["case_id"] for row in main_results
                                       if row["repeat_index"] == repeat and not row["passed"]]}
                  for repeat in range(1, repeats + 1)]
    case_successes = sum(item["pass_rate"] == 1.0 for item in per_case.values())
    answerable_ids = [case_id for case_id, gold in gold_by_id.items()
                      if gold.get("expected_status", "answered") == "answered"]
    answerable_successes = sum(per_case[case_id]["pass_rate"] == 1.0 for case_id in answerable_ids)

    def arm_measurements(rows):
        call_records = [call for row in rows for call in row["result"].get("model_calls", [])]
        usage = [call.get("usage") or {} for call in call_records]
        if not call_records:
            usage = [row["result"].get("model_usage") or {} for row in rows]
        calls = [call.get("latency_ms") for call in call_records
                 if isinstance(call.get("latency_ms"), (int, float))]
        if not calls:
            calls = [row["result"].get("model_latency_ms") for row in rows
                     if isinstance(row["result"].get("model_latency_ms"), (int, float))]
        elapsed = [row["result"].get("elapsed_ms") for row in rows
                   if isinstance(row["result"].get("elapsed_ms"), (int, float))]
        token_counts = [item["total_tokens"] for item in usage if isinstance(item.get("total_tokens"), int)]
        model_call_count = sum(row["result"].get("model_call_count", 0) for row in rows)
        if not model_call_count:
            model_call_count = max(len(calls), len(token_counts))
        return {"model_call_count": model_call_count, "total_tokens": sum(token_counts),
                "model_latency_ms_distribution": _distribution(calls),
                "elapsed_ms_distribution": _distribution(elapsed),
                "total_tokens_distribution": _distribution(token_counts)}

    def failed_paths(rows):
        return [{"case_id": row["case_id"], "status": row["result"].get("status"),
                 "stage": row["result"].get("stage"), "reason": row["result"].get("reason"),
                 "trajectory": row["result"].get("trajectory")}
                for row in rows if not row["passed"]]

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "holdout_id": manifest["holdout_id"],
        "data_version": dataset_version,
        "data_fingerprint": data_fingerprint,
        "model": settings.model,
        "case_count": len(cases), "repeat_count": repeats, "attempt_count": len(main_results),
        "passed": sum(row["passed"] for row in main_results),
        "task_completion_rate": sum(row["passed"] for row in main_results) / len(main_results) if main_results else None,
        "unique_case_passed": case_successes, "unique_case_total": len(cases),
        "unique_case_wilson_95_ci": _wilson_interval(case_successes, len(cases)),
        "answerable_case_passed": answerable_successes, "answerable_case_total": len(answerable_ids),
        "answerable_case_wilson_95_ci": _wilson_interval(answerable_successes, len(answerable_ids)),
        "deterministic_guard_case_count": len(cases) - len(answerable_ids),
        "metric_accuracy": sum(row["metric_ok"] is True for row in main_results),
        "dimension_accuracy": sum(row["dimension_ok"] is True for row in main_results),
        "numeric_accuracy": sum(row["numeric_ok"] is True for row in main_results),
        "sql_verified": sum(row["sql_verified"] for row in main_results),
        "mean_reward": round(statistics.mean(row["reward"] for row in main_results), 3) if main_results else None,
        "sql_safe_execution": sum(row["status_ok"] and row["sql_verified"] for row in main_results),
        "per_case": per_case, "per_repeat": per_repeat,
        "case_cluster_bootstrap_95_ci": _cluster_bootstrap_ci(main_results),
        "confidence_interval_note": "按题目聚类 bootstrap；刻意分层的合成 holdout 非随机总体样本，区间仅描述本集不确定性。",
        "elapsed_ms_distribution": _distribution(latencies),
        "model_backed_elapsed_ms_distribution": _distribution([
            row["result"]["elapsed_ms"] for row in main_results
            if row["result"].get("model_usage") and isinstance(row["result"].get("elapsed_ms"), (int, float))]),
        "model_latency_ms_distribution": _distribution(model_latencies),
        "total_tokens_distribution": _distribution(tokens),
        "model_call_count": len(model_latencies), "token_cases": len(tokens),
        "cost_estimate": None, "cost_note": "供应商未提供可核验单价；不估算费用。",
        "failures": [row["case_id"] for row in main_results if not row["passed"]],
        "ablations": {
            "without_semantic_retrieval_passed": sum(row["passed"] for row in no_retrieval),
            "without_semantic_retrieval_total": len(no_retrieval),
            "without_semantic_retrieval_failures": [row["case_id"] for row in no_retrieval if not row["passed"]],
            "without_independent_verification_passed": sum(row["passed"] for row in no_verifier),
            "without_independent_verification_total": len(no_verifier),
            "without_independent_verification_failures": [row["case_id"] for row in no_verifier if not row["passed"]],
            "without_verifier_sql_verified": 0,
            "without_semantic_retrieval_measurements": arm_measurements(no_retrieval),
            "without_semantic_retrieval_failure_paths": failed_paths(no_retrieval),
            "without_independent_verification_measurements": arm_measurements(no_verifier),
            "without_independent_verification_failure_paths": failed_paths(no_verifier),
        },
    }
    (output / "enterprise-evaluation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def refresh_no_verifier_arm(output: Path, root: Path, database: Path | None,
                            blind_path: Path, gold_path: Path) -> dict:
    settings = load_settings(root)
    if database is not None:
        settings = replace(settings, database_path=database.resolve())
    dataset_version, fingerprint = read_data_identity(settings.database_path)
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    manifest_path = blind_path.with_name(blind_path.stem.replace("blind", "holdout-manifest") + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (dataset_version != "insightpilot-enterprise-v1" or fingerprint != manifest["data_fingerprint"] or
            manifest["case_count"] != len(cases) or
            manifest["blind_questions_sha256"] != sha256_text(blind_path) or
            manifest["gold_sha256"] != sha256_text(gold_path)):
        raise ValueError("frozen holdout, oracle, or database fingerprint mismatch")
    rows = []
    for case in cases:
        planner = OpenAICompatiblePlanner(settings)
        result = _without_verification(planner, settings.database_path, case["question"])
        comparison, adjustment = _compare_without_verification(case, gold_by_id[case["case_id"]], result)
        row = {**comparison, "result": result}
        if adjustment:
            row["audit_adjustment"] = adjustment
        rows.append(row)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "enterprise-ablation-no-verifier.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return {"holdout_id": manifest["holdout_id"], "cases": len(rows),
            "passed": sum(row["passed"] for row in rows), "failures": [row["case_id"] for row in rows if not row["passed"]],
            "output": str(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the prompt-blind enterprise evaluation set")
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--refresh-no-verifier", action="store_true")
    args = parser.parse_args()
    if args.refresh_no_verifier:
        blind_path = args.blind or Path("evals/enterprise-blind-v1.jsonl")
        gold_path = args.gold or blind_path.with_name(blind_path.name.replace("blind", "gold"))
        print(json.dumps(refresh_no_verifier_arm(args.output, Path.cwd(), args.database, blind_path, gold_path),
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps(run(args.output, Path.cwd(), args.database, args.repeats, args.blind, args.gold,
                         args.reuse_existing), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
