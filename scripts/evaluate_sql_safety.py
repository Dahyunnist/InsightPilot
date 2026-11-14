"""Run frozen offline SQL policy cases against InsightPilot's production guard."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from data.seed import create_database
from insightpilot.runner import execute_readonly
from insightpilot.sql import CompiledQuery
from insightpilot.sql_policy import SQLPolicyError, validate_sql

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "sql-safety-v1.jsonl"
REPORT = ROOT / "reports" / "sql-safety-v1.json"


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(cases: list[dict[str, Any]], database_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        observed = "allowed"
        reason = None
        try:
            validate_sql(case["sql"])
            if case["expected"] == "allowed":
                execute_readonly(database_path, CompiledQuery(case["sql"], ()))
            else:
                observed = "unexpectedly_allowed_by_ast"
                reason = "dangerous case passed the SQL AST policy; query was not executed"
        except SQLPolicyError as exc:
            observed = "blocked"
            reason = str(exc)
        except Exception as exc:  # an approved query failing at runtime is not a policy block
            observed = "execution_error"
            reason = type(exc).__name__
        rows.append({
            "case_id": case["case_id"],
            "category": case["category"],
            "expected": case["expected"],
            "observed": observed,
            "passed": observed == case["expected"],
            "reason": reason,
        })

    blocked = [row for row in rows if row["expected"] == "blocked"]
    allowed = [row for row in rows if row["expected"] == "allowed"]
    return {
        "suite_id": "insightpilot-sql-safety-v1",
        "status": "completed" if all(row["passed"] for row in rows) else "failed",
        "case_count": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "dangerous_blocked": sum(row["observed"] == "blocked" for row in blocked),
        "dangerous_total": len(blocked),
        "approved_allowed": sum(row["observed"] == "allowed" for row in allowed),
        "approved_total": len(allowed),
        "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
        "model_calls": 0,
        "database": "fixed synthetic S0 seed; approved query executed read-only",
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    cases = load_cases()
    if len({case["case_id"] for case in cases}) != len(cases):
        raise SystemExit("duplicate case_id")
    with tempfile.TemporaryDirectory(prefix="insightpilot-sql-safety-") as temp:
        database_path = create_database(Path(temp) / "fixture.sqlite")
        report = evaluate(cases, database_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "status", "case_count", "passed", "dangerous_blocked", "dangerous_total",
        "approved_allowed", "approved_total", "model_calls", "cases_sha256",
    )}, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
