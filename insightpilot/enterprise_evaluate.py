"""Evaluate prompt-blind enterprise holdout questions against separate Python gold."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import replace

from .agent import InsightPilot
from .config import load_settings
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
    expected_metric = gold["metric"]
    metric_ok = result.get("metric", {}).get("id") == expected_metric
    rows = {row.get("group_value"): row for row in result.get("rows", [])}
    expected = gold["expected"]
    numeric_ok = set(rows) == set(expected) and all(
        all(_matches(rows[group].get(field), value) for field, value in row.items())
        for group, row in expected.items()
    )
    verification = result.get("verification", {}).get("passed") is True
    status_ok = result.get("status") == "answered"
    reward = (0.5 if metric_ok else 0) + (0.5 if result.get("plan", {}).get("dimension") == gold["dimension"] else 0)
    reward += 1.0 if numeric_ok else 0
    reward += 1.0 if verification else 0
    if not status_ok and result.get("status") in ("refused", "clarify"):
        reward += 0.25
    if result.get("stage") in ("query", "verification") and result.get("status") == "failed":
        reward -= 1.0
    return {"case_id": case["case_id"], "status_ok": status_ok,
            "metric_ok": metric_ok, "dimension_ok": result.get("plan", {}).get("dimension") == gold["dimension"],
            "numeric_ok": numeric_ok, "sql_verified": verification,
            "reward": round(reward, 2),
            "passed": result.get("status") == "answered" and metric_ok and numeric_ok and verification}


def _without_verification(planner, database_path, question: str) -> dict:
    started = time.perf_counter()
    try:
        plan = planner.plan(question)
        compiled = compile_query(plan)
        rows, data_version = execute_readonly(database_path, compiled)
        return {"status": "answered", "metric": {"id": plan.metric_id}, "plan": {"dimension": plan.dimension},
                "rows": rows, "data_version": data_version, "verification": {"passed": None, "disabled": True},
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "model_usage": planner.last_call_usage}
    except Exception as exc:
        return {"status": "failed", "stage": "ablation", "reason": type(exc).__name__,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "model_usage": planner.last_call_usage}


def run(output: Path, root: Path, database: Path | None = None) -> dict:
    settings = load_settings(root)
    if database is not None:
        settings = replace(settings, database_path=database.resolve())
    if not settings.database_path.is_file():
        raise ValueError("企业评测数据库不存在；先运行 python -m data.enterprise_seed。")
    dataset_version, data_fingerprint = read_data_identity(settings.database_path)
    if dataset_version != "insightpilot-enterprise-v1":
        raise ValueError("企业 holdout 只适用于 insightpilot-enterprise-v1。")
    cases = [json.loads(line) for line in (root / "evals/enterprise-blind-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in (root / "evals/enterprise-gold-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    output.mkdir(parents=True, exist_ok=True)
    main_results, latencies, tokens = [], [], []
    for case in cases:
        result = InsightPilot(OpenAICompatiblePlanner(settings), settings.database_path).analyze(case["question"])
        comparison = _compare(case, gold_by_id[case["case_id"]], result)
        main_results.append({**comparison, "question": case["question"], "result": result})
        if isinstance(result.get("elapsed_ms"), (int, float)):
            latencies.append(result["elapsed_ms"])
        usage = result.get("model_usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            tokens.append(usage["total_tokens"])
    (output / "enterprise-evaluation-raw.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in main_results) + "\n", encoding="utf-8")

    gold_by_id = {row["case_id"]: row for row in gold_rows}
    no_retrieval, no_verifier = [], []
    for case in cases:
        baseline_result = InsightPilot(OpenAICompatiblePlanner(settings, use_metric_retrieval=False), settings.database_path).analyze(case["question"])
        no_retrieval.append({**_compare(case, gold_by_id[case["case_id"]], baseline_result), "result": baseline_result})
        planner = OpenAICompatiblePlanner(settings)
        result = _without_verification(planner, settings.database_path, case["question"])
        comparison = _compare(case, gold_by_id[case["case_id"]], {**result, "verification": {"passed": True}})
        comparison["sql_verified"] = False
        comparison["passed"] = result.get("status") == "answered" and comparison["metric_ok"] and comparison["numeric_ok"]
        no_verifier.append({**comparison, "result": result})
    (output / "enterprise-ablation-no-retrieval.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in no_retrieval) + "\n", encoding="utf-8")
    (output / "enterprise-ablation-no-verifier.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in no_verifier) + "\n", encoding="utf-8")

    ordered = sorted(latencies)
    model_latencies = [row["result"].get("model_latency_ms") for row in main_results
                       if isinstance(row["result"].get("model_latency_ms"), (int, float))]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "data_version": dataset_version,
        "data_fingerprint": data_fingerprint,
        "case_count": len(cases), "passed": sum(row["passed"] for row in main_results),
        "task_completion_rate": sum(row["passed"] for row in main_results) / len(cases) if cases else None,
        "metric_accuracy": sum(row["metric_ok"] for row in main_results),
        "dimension_accuracy": sum(row["dimension_ok"] for row in main_results),
        "numeric_accuracy": sum(row["numeric_ok"] for row in main_results),
        "sql_verified": sum(row["sql_verified"] for row in main_results),
        "mean_reward": round(statistics.mean(row["reward"] for row in main_results), 3) if main_results else None,
        "sql_safe_execution": sum(row["status_ok"] and row["sql_verified"] for row in main_results),
        "p50_elapsed_ms": ordered[(len(ordered)-1)//2] if ordered else None,
        "p95_elapsed_ms": ordered[max(0, math.ceil(.95*len(ordered))-1)] if ordered else None,
        "mean_total_tokens": round(statistics.mean(tokens), 2) if tokens else None,
        "mean_model_latency_ms": round(statistics.mean(model_latencies), 3) if model_latencies else None,
        "token_cases": len(tokens), "cost_estimate": None, "cost_note": "当前未配置供应商单价，只报告 provider token 用量。",
        "failures": [row["case_id"] for row in main_results if not row["passed"]],
        "ablations": {
            "without_semantic_retrieval_passed": sum(row["passed"] for row in no_retrieval),
            "without_semantic_retrieval_total": len(no_retrieval),
            "without_semantic_retrieval_failures": [row["case_id"] for row in no_retrieval if not row["passed"]],
            "without_independent_verification_numerically_correct": sum(row["passed"] for row in no_verifier),
            "without_independent_verification_total": len(no_verifier),
            "without_independent_verification_failures": [row["case_id"] for row in no_verifier if not row["passed"]],
            "without_verifier_sql_verified": 0,
        },
    }
    (output / "enterprise-evaluation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the prompt-blind enterprise evaluation set")
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(run(args.output, Path.cwd(), args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
