"""Run offline RulePlanner baseline on frozen v9; gold stays outside agent input."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.enterprise_evaluate import _compare
from insightpilot.rule_baseline import RulePlanner


def main() -> None:
    cases = [json.loads(line) for line in (ROOT / "evals/enterprise-blind-v9.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in (json.loads(line) for line in (ROOT / "evals/enterprise-gold-v9.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    database = ROOT / "data/insightpilot-enterprise-v9.sqlite"
    core = InsightPilot(RulePlanner(), database)
    evaluated = [(case, core.analyze(case["question"])) for case in cases]
    rows = [_compare(case, gold[case["case_id"]], result) for case, result in evaluated]
    answerable = {case_id for case_id, item in gold.items() if item.get("expected_status", "answered") == "answered"}
    result = {"suite": "enterprise-blind-v9", "planner": "RulePlanner", "model_calls": 0,
              "passed": sum(row["passed"] for row in rows), "total": len(rows),
              "answerable_passed": sum(row["passed"] for row in rows if row["case_id"] in answerable),
              "answerable_total": len(answerable),
              "guard_passed": sum(row["passed"] for row in rows if row["case_id"] not in answerable),
              "guard_total": len(rows) - len(answerable),
              "false_refusals": [case["case_id"] for case, value in evaluated if case["case_id"] in answerable and value.get("status") != "answered"],
              "false_accepts": [case["case_id"] for case, value in evaluated if case["case_id"] not in answerable and value.get("status") == "answered"],
              "cases": rows}
    (ROOT / "reports/enterprise-v9-rule-baseline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
