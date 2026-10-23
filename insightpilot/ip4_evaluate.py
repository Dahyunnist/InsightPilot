"""Offline evaluation for the frozen cross-domain IP4 holdout."""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from .agent import InsightPilot
from .freeze import sha256_text
from .rule_baseline import RulePlanner
from .runner import read_data_identity


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    rate = successes / total
    den = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / den
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / den
    return [round(max(0, center - margin), 4), round(min(1, center + margin), 4)]


def _bootstrap(rows: list[dict], seed: int = 24017, iterations: int = 5000) -> list[float] | None:
    if len(rows) < 2:
        return None
    scores = [int(row["passed"]) for row in rows]
    rng = random.Random(seed)
    samples = sorted(sum(rng.choices(scores, k=len(scores))) / len(scores) for _ in range(iterations))
    return [round(samples[int(iterations * .025)], 4),
            round(samples[min(iterations - 1, int(iterations * .975))], 4)]


def _value_matches(actual, expected) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6)
    return actual == expected


def compare(case: dict, gold: dict, result: dict) -> dict:
    expected_status = gold["expected_status"]
    status_ok = result.get("status") == expected_status
    reason_ok = ("expected_reason" not in gold or result.get("reason") == gold["expected_reason"])
    checks = {"status_ok": status_ok, "reason_ok": reason_ok}
    if expected_status == "answered":
        metric_ok = result.get("metric", {}).get("id") == gold["metric"]
        plan = result.get("plan", {})
        dimension_ok = plan.get("dimension") == gold["dimension"]
        region_ok = plan.get("region") == gold["region"]
        dates_ok = (plan.get("start_date_inclusive") == gold["analysis_start"] and
                    plan.get("end_date_exclusive") == gold["analysis_end_exclusive"])
        actual_rows = {row.get("group_value"): row for row in result.get("rows", [])}
        expected_rows = {row.get("group_value"): row for row in gold["expected_rows"]}
        numeric_ok = set(actual_rows) == set(expected_rows) and all(
            all(_value_matches(actual_rows[group].get(key), value)
                for key, value in expected.items() if key != "group_value")
            for group, expected in expected_rows.items()
        )
        verification_ok = result.get("verification", {}).get("passed") is True
        checks.update({"metric_ok": metric_ok, "dimension_ok": dimension_ok, "region_ok": region_ok,
                       "date_window_ok": dates_ok, "numeric_ok": numeric_ok,
                       "independent_verifier_ok": verification_ok})
        passed = all((status_ok, metric_ok, dimension_ok, region_ok, dates_ok, numeric_ok, verification_ok))
    else:
        passed = status_ok and reason_ok
        checks.update({"metric_ok": None, "dimension_ok": None, "region_ok": None,
                       "date_window_ok": None, "numeric_ok": None,
                       "independent_verifier_ok": None})
    return {"case_id": case["case_id"], "domain": gold["domain"], "passed": passed,
            "actual_status": result.get("status"), "actual_reason": result.get("reason"), **checks}


def evaluate(database: Path, blind_path: Path, gold_path: Path, manifest_path: Path,
             output_path: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (sha256_text(blind_path) != manifest["blind_sha256"] or
            sha256_text(gold_path) != manifest["gold_sha256"]):
        raise ValueError("IP4 blind/gold file hash does not match the frozen manifest")
    version, fingerprint = read_data_identity(database)
    if version != manifest["data_version"] or fingerprint != manifest["data_fingerprint"]:
        raise ValueError("IP4 dataset version/fingerprint does not match the frozen manifest")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    if len(cases) != manifest["case_count"] or {row["case_id"] for row in cases} != set(gold_by_id):
        raise ValueError("IP4 blind/gold case IDs or count do not match the manifest")

    core = InsightPilot(RulePlanner(), database)
    details = []
    for case in cases:
        result = core.analyze(case["question"])
        details.append(compare(case, gold_by_id[case["case_id"]], result))

    expected_refusals = [row for row in details
                         if gold_by_id[row["case_id"]]["expected_status"] == "refused"]
    false_refusals = [row["case_id"] for row in details
                      if gold_by_id[row["case_id"]]["expected_status"] == "answered"
                      and row["actual_status"] == "refused"]
    false_accepts = [row["case_id"] for row in expected_refusals if row["actual_status"] != "refused"]
    by_domain = {}
    for domain in sorted({row["domain"] for row in details}):
        subset = [row for row in details if row["domain"] == domain]
        passed = sum(row["passed"] for row in subset)
        by_domain[domain] = {"passed": passed, "total": len(subset),
                             "strict_accuracy": round(passed / len(subset), 4),
                             "wilson_95_ci": _wilson(passed, len(subset)),
                             "failures": [row["case_id"] for row in subset if not row["passed"]]}
    successes, total = sum(row["passed"] for row in details), len(details)
    refusal_successes = sum(row["passed"] for row in expected_refusals)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite_id": manifest["suite_id"], "backend": "SQLite", "planner": "RulePlanner",
        "model_calls": 0, "estimated_model_cost_usd": 0.0,
        "data_version": version, "data_fingerprint": fingerprint,
        "case_count": total, "passed": successes, "strict_accuracy": round(successes / total, 4),
        "wilson_95_ci": _wilson(successes, total), "case_bootstrap_95_ci": _bootstrap(details),
        "by_domain": by_domain,
        "answerable": {"passed": sum(row["passed"] for row in details
                                      if gold_by_id[row["case_id"]]["expected_status"] == "answered"),
                       "total": sum(gold["expected_status"] == "answered" for gold in gold_rows)},
        "refusal_metrics": {"expected_refusal_count": len(expected_refusals),
                            "correct_refusals": refusal_successes,
                            "correct_refusal_recall": round(refusal_successes / len(expected_refusals), 4)
                            if expected_refusals else None,
                            "false_refusals_on_answerable": false_refusals,
                            "false_accepts_on_refusal_cases": false_accepts,
                            "unsupported_region_accepted": [row["case_id"] for row in details
                                if row["case_id"] == "IP4-M04" and row["actual_status"] == "answered"]},
        "failures": [row for row in details if not row["passed"]],
        "confidence_interval_note": "此为按业务域分层的小型合成固定集；Wilson 与题目 bootstrap 区间仅描述本冻结集，不代表生产总体准确率。",
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--blind", type=Path, default=Path("evals/ip4-blind-v1.jsonl"))
    parser.add_argument("--gold", type=Path, default=Path("evals/ip4-gold-v1.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("evals/ip4-manifest-v1.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/ip4-rule-baseline.json"))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.database, args.blind, args.gold, args.manifest, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
