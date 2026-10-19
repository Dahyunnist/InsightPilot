"""Run the versioned golden set and write auditable evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .agent import InsightPilot
from .config import load_settings
from .planner import OpenAICompatiblePlanner


def _close(actual, expected, tolerance):
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0)
    return actual == expected


def _numeric_map(rows, key, value):
    return {row[key]: row[value] for row in rows}


def _normal_pass(case, result):
    if result.get("status") != "answered" or result.get("metric", {}).get("id") != case["metric"]:
        return False
    rows, expected, tol = result["rows"], case["expected"], case["tolerance"]
    metric = case["metric"]
    if metric in ("approved_refund_amount_rate", "approved_refund_order_rate") and case["group_by"]:
        field_map = {"approved_refund_amount_rate": ("numerator_cents", "denominator_cents", "numerator_cents", "denominator_cents"),
                     "approved_refund_order_rate": ("numerator_orders", "denominator_orders", "numerator", "denominator")}
        nkey, dkey, ncol, dcol = field_map[metric]
        actual = {row["group_value"]: {nkey: row[ncol], dkey: row[dcol], "value": row["value"]} for row in rows}
        return set(actual) == set(expected) and all(
            all(_close(actual[k][f], val, tol) for f, val in exp.items()) for k, exp in expected.items()
        )
    if metric == "approved_refund_order_rate" and len(case["period_utc"]) == 2 and isinstance(case["period_utc"][0], list):
        actual = {row["group_value"]: {"numerator_orders": row["numerator"], "denominator_orders": row["denominator"], "value": row["value"]} for row in rows}
        return set(actual) == set(expected) and all(all(_close(actual[k][f], val, tol) for f, val in exp.items()) for k, exp in expected.items())
    if metric == "approved_refund_order_rate" and case["group_by"]:
        actual = {row["group_value"]: {"numerator_orders": row["numerator"], "denominator_orders": row["denominator"], "value": row["value"]} for row in rows}
        return set(actual) == set(expected) and all(all(_close(actual[k][f], val, tol) for f, val in exp.items()) for k, exp in expected.items())
    if metric == "approved_refund_amount_contribution":
        actual = _numeric_map(rows, "group_value", "value")
    elif metric in ("approved_refund_event_amount", "refund_amount_by_status", "approved_refund_amount"):
        if not rows:
            return False
        actual = _numeric_map(rows, "group_value", "amount_cents") if "group_value" in rows[0] else {"全部": rows[0]["amount_cents"]}
    elif metric == "approved_refund_order_counts":
        actual = {k: rows[0][k] for k in ("paid_orders", "refunded_orders")}
    elif metric == "approved_refund_cohort_summary":
        actual = {k: rows[0][k] for k in ("numerator_cents", "refunded_orders", "paid_orders")}
    elif metric in ("approved_refund_amount_rate", "approved_refund_order_rate"):
        row = rows[0]
        actual = {"numerator_cents": row.get("numerator_cents"), "denominator_cents": row.get("denominator_cents"), "value": row.get("value")} if metric.endswith("amount_rate") else {"numerator_orders": row.get("numerator"), "denominator_orders": row.get("denominator"), "value": row.get("value")}
    else:
        return False
    return set(actual) == set(expected) and all(_close(actual[k], value, tol) for k, value in expected.items())


def _evaluate(case, result):
    expected_status = {"normal": "answered", "ambiguity": "clarify", "refusal": "refused"}[case["kind"]]
    passed = result.get("status") == expected_status
    if passed and case["kind"] == "normal":
        passed = _normal_pass(case, result)
    if passed and case["kind"] == "refusal":
        passed = result.get("reason") == case["expected"]["reason"]
    return passed


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def run(output: Path, root: Path, *, ablation: bool = True) -> dict:
    settings = load_settings(root)
    cases = [json.loads(line) for line in (root / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    db = settings.database_path
    raw_path = output / "evaluation-raw.jsonl"
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        app = InsightPilot(OpenAICompatiblePlanner(settings), db)
        result = app.analyze(case["question"])
        rows.append({"case_id": case["id"], "kind": case["kind"], "expected": case["expected"], "passed": _evaluate(case, result), "result": result})
        raw_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    normal = [row for row in rows if row["kind"] == "normal"]
    answered = [row["result"] for row in normal if row["result"].get("status") == "answered"]
    latencies = [r["elapsed_ms"] for r in answered]
    tokens = [r["model_usage"]["total_tokens"] for r in answered if isinstance(r.get("model_usage"), dict) and "total_tokens" in r["model_usage"]]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "total": len(rows),
        "passed": sum(row["passed"] for row in rows), "normal_total": len(normal),
        "normal_numeric_pass": sum(row["passed"] for row in normal),
        "clarify_pass": sum(row["passed"] for row in rows if row["kind"] == "ambiguity"),
        "refusal_pass": sum(row["passed"] for row in rows if row["kind"] == "refusal"),
        "sql_executable": sum(r.get("status") == "answered" and bool(r.get("verification", {}).get("passed")) for r in (row["result"] for row in normal)),
        "p50_elapsed_ms": _percentile(latencies, .5), "p95_elapsed_ms": _percentile(latencies, .95),
        "mean_reported_total_tokens": round(statistics.mean(tokens), 2) if tokens else None,
        "token_usage_cases": len(tokens), "failures": [row["case_id"] for row in rows if not row["passed"]],
    }
    (output / "evaluation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if ablation:
        selected = [case for case in cases if case["id"] in {"N01", "N02", "N03", "N05", "N07", "N12"}]
        ablated = []
        for case in selected:
            result = InsightPilot(OpenAICompatiblePlanner(settings, use_metric_retrieval=False), db).analyze(case["question"])
            ablated.append({"case_id": case["id"], "passed": _evaluate(case, result), "result": result})
        (output / "retrieval-ablation-raw.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in ablated) + "\n", encoding="utf-8")
        summary["retrieval_ablation"] = {"cases": len(ablated), "numeric_pass": sum(row["passed"] for row in ablated), "failures": [row["case_id"] for row in ablated if not row["passed"]]}
        (output / "evaluation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Run InsightPilot golden-set evaluation")
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--no-ablation", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output, Path.cwd(), ablation=not args.no_ablation), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
