"""Verify IP4 queries, RLS isolation, statement timeout, and pool recovery on PostgreSQL."""

from __future__ import annotations

import json
import os
from pathlib import Path

from insightpilot.planner import RulePlanner
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.runner import QueryExecutionError, QueryTimeoutError
from insightpilot.sql import CompiledQuery, compile_query


def _compare_rows(actual: list[dict], expected: list[dict]) -> bool:
    actual_by_group = {row.get("group_value"): row for row in actual}
    expected_by_group = {row.get("group_value"): row for row in expected}
    if actual_by_group.keys() != expected_by_group.keys():
        return False
    for group, expected_row in expected_by_group.items():
        for field, expected_value in expected_row.items():
            actual_value = actual_by_group[group].get(field)
            if expected_value is None:
                if actual_value is not None:
                    return False
            elif isinstance(expected_value, (int, float)):
                if not isinstance(actual_value, (int, float)) or abs(actual_value - expected_value) > 1e-6:
                    return False
            elif actual_value != expected_value:
                return False
    return True


def verify(database: Path, blind_path: Path, gold_path: Path, tenant_ids: tuple[str, str]) -> dict:
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError:
        raise RuntimeError("Install requirements.txt before running PostgreSQL verification") from None
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise RuntimeError("Set INSIGHTPILOT_IP4_APP_DSN to the non-superuser test role DSN")

    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in gold_rows}
    sqlite_engine = SQLiteQueryEngine(database)
    answerable = [case for case in cases if gold[case["case_id"]]["expected_status"] == "answered"]
    tenant_results = {}
    for tenant_id in tenant_ids:
        rows_checked, mismatches = 0, []
        engine = PostgresQueryEngine(dsn, tenant_id=tenant_id)
        try:
            for case in answerable:
                plan = RulePlanner().plan(case["question"])
                query = compile_query(plan)
                pg_rows, _ = engine.execute_readonly(query)
                sqlite_rows, _ = sqlite_engine.execute_readonly(query)
                expected = gold[case["case_id"]]["expected_rows"]
                if not _compare_rows(pg_rows, expected) or not _compare_rows(pg_rows, sqlite_rows):
                    mismatches.append(case["case_id"])
                rows_checked += 1
        finally:
            engine.close()
        tenant_results[tenant_id] = {"matched_cases": rows_checked - len(mismatches),
                                     "total_cases": rows_checked, "mismatches": mismatches}

    no_tenant_result = "not_checked"
    engine = PostgresQueryEngine(dsn)
    try:
        plan = RulePlanner().plan(answerable[0]["question"])
        try:
            engine.execute_readonly(compile_query(plan))
        except QueryExecutionError:
            no_tenant_result = "rejected"
        else:
            no_tenant_result = "unexpectedly_accepted"
    finally:
        engine.close()

    # Hold the only pooled connection, confirm checkout fails, release it, then prove recovery.
    pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=1, timeout=0.2, open=True)
    pool_result = "not_checked"
    query = compile_query(RulePlanner().plan(answerable[0]["question"]))
    pooled_engine = PostgresQueryEngine(dsn, tenant_id=tenant_ids[0], pool=pool)
    try:
        with pool.connection():
            try:
                pooled_engine.execute_readonly(query)
            except QueryExecutionError:
                pool_result = "exhausted"
            else:
                pool_result = "unexpectedly_available"
        pooled_engine.execute_readonly(query)
        if pool_result == "exhausted":
            pool_result = "recovered"
    finally:
        pooled_engine.close()

    slow_engine = PostgresQueryEngine(dsn, tenant_id=tenant_ids[0], statement_timeout_ms=50)
    try:
        try:
            slow_engine.execute_readonly(CompiledQuery(
                "SELECT COUNT(*) AS numerator FROM orders o CROSS JOIN orders a CROSS JOIN orders b LIMIT 1", ()))
            timeout_result = "unexpectedly_completed"
        except QueryTimeoutError:
            timeout_result = "timed_out"
    finally:
        slow_engine.close()

    passed = (all(value["matched_cases"] == value["total_cases"] for value in tenant_results.values())
              and no_tenant_result == "rejected" and pool_result == "recovered"
              and timeout_result == "timed_out")
    return {"status": "passed" if passed else "failed", "backend": "PostgreSQL",
            "answerable_case_count": len(answerable), "tenants": tenant_results,
            "missing_tenant_context": no_tenant_result,
            "pool_exhaustion_and_recovery": pool_result,
            "statement_timeout": timeout_result,
            "independent_gold": "IP4 Python gold; PostgreSQL output also compared to SQLite output"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--blind", type=Path, default=Path("evals/ip4-blind-v1.jsonl"))
    parser.add_argument("--gold", type=Path, default=Path("evals/ip4-gold-v1.jsonl"))
    parser.add_argument("--tenant-a", default="tenant-a")
    parser.add_argument("--tenant-b", default="tenant-b")
    parser.add_argument("--output", type=Path, default=Path("reports/ip4-postgres-verification.json"))
    args = parser.parse_args()
    result = verify(args.database, args.blind, args.gold, (args.tenant_a, args.tenant_b))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
