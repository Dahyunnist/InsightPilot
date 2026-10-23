"""Verify IP4 queries, RLS isolation, statement timeout, and pool recovery on PostgreSQL."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.ip4_evaluate import compare
from insightpilot.rule_baseline import RulePlanner
from insightpilot.query_engine import PostgresQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError, QueryTimeoutError
from insightpilot.sql import CompiledQuery, compile_query


def verify(database: Path, blind_path: Path, gold_path: Path, tenant_ids: tuple[str, str],
           expected_data_version: str, expected_data_fingerprint: str) -> dict:
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
    answerable = [case for case in cases if gold[case["case_id"]]["expected_status"] == "answered"]
    tenant_results = {}
    engine = PostgresQueryEngine(dsn)
    core = InsightPilot(RulePlanner(), database, query_engine=engine)
    with tempfile.TemporaryDirectory(prefix="insightpilot-api-shared-") as temp:
        token_map = {f"local-{tenant}-only": tenant for tenant in tenant_ids}
        app = create_app(core, run_store=RunStore(Path(temp) / "runs.sqlite"),
                         api_tenant_tokens=token_map, api_scopes={
                             "refund:read", "refund:cancel", "marketing:read"})
        with TestClient(app) as client:
            for tenant_id in tenant_ids:
                token = f"local-{tenant_id}-only"
                checks = []
                for case in cases:
                    response = client.post("/analysis", json={"question": case["question"]},
                                           headers={"Authorization": f"Bearer {token}"})
                    actual = response.json()
                    scored = compare(case, gold[case["case_id"]], actual)
                    expected_http = {"answered": 200, "clarify": 422, "refused": 403}[
                        gold[case["case_id"]]["expected_status"]]
                    checked = {**scored, "http_status": response.status_code,
                               "http_status_ok": response.status_code == expected_http,
                               "actual_stage": actual.get("stage"),
                               "actual_message": actual.get("message")}
                    if gold[case["case_id"]]["expected_status"] == "answered":
                        checked["postgres_backend"] = actual.get("query_backend") == "postgresql"
                        checked["dataset_identity_matches"] = (
                            actual.get("data_version") == expected_data_version and
                            actual.get("data_fingerprint") == expected_data_fingerprint
                        )
                        checked["sqlite_independent_verifier"] = actual.get("verification", {}).get("passed") is True
                    checked["passed"] = checked["passed"] and checked["http_status_ok"] and all(
                        checked.get(key, True) for key in ("postgres_backend", "dataset_identity_matches",
                                                            "sqlite_independent_verifier"))
                    checks.append(checked)
                refusal_cases = [row for row in checks if gold[row["case_id"]]["expected_status"] == "refused"]
                tenant_results[tenant_id] = {
                    "passed": sum(row["passed"] for row in checks), "total": len(checks), "case_results": checks,
                    "failures": [row for row in checks if not row["passed"]],
                    "refusal_metrics": {
                        "correct_refusals": sum(row["actual_status"] == "refused" and row["reason_ok"]
                                                 for row in refusal_cases),
                        "expected_refusals": len(refusal_cases),
                        "false_refusals_on_answerable": [row["case_id"] for row in checks
                            if gold[row["case_id"]]["expected_status"] == "answered" and row["actual_status"] == "refused"],
                        "false_accepts_on_refusal_cases": [row["case_id"] for row in refusal_cases
                            if row["actual_status"] == "answered"],
                        "unsupported_marketing_region_accepted": any(row["case_id"] == "IP4-M04" and
                            row["actual_status"] == "answered" for row in checks),
                    },
                    "cross_tenant_join_case_ids": [row["case_id"] for row in checks
                        if row["case_id"] in ("IP4-R03", "IP4-M01") and row["passed"]],
                }
    engine.close()

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

    dml_denied = False
    with psycopg.connect(dsn) as connection:
        try:
            connection.execute("BEGIN")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_ids[0],))
            connection.execute("DELETE FROM orders WHERE false")
        except psycopg.errors.InsufficientPrivilege:
            dml_denied = True
        finally:
            connection.rollback()

    # Hold the only pooled connection, confirm checkout fails, release it, then prove recovery.
    pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=1, timeout=0.2, open=True)
    pool_result = "not_checked"
    query = compile_query(RulePlanner().plan(answerable[0]["question"]))
    # Keep statement execution budget separate from the deliberately short pool checkout.
    pooled_engine = PostgresQueryEngine(dsn, tenant_id=tenant_ids[0], pool=pool,
                                        statement_timeout_ms=10_000)
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

    passed = (all(value["passed"] == value["total"] for value in tenant_results.values())
              and no_tenant_result == "rejected" and dml_denied and pool_result == "recovered"
              and timeout_result == "timed_out")
    return {"status": "passed" if passed else "failed", "backend": "PostgreSQL via FastAPI/InsightPilot/PostgresQueryEngine",
            "case_count_per_tenant": len(cases), "answerable_case_count": len(answerable), "tenants": tenant_results,
            "missing_tenant_context": no_tenant_result,
            "postgres_dml_denied": dml_denied,
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
    parser.add_argument("--manifest", type=Path, default=Path("evals/ip4-manifest-v1.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/ip4-postgres-verification.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_data_version = manifest["data_version"]
    result = verify(args.database, args.blind, args.gold, (args.tenant_a, args.tenant_b),
                    manifest["data_version"], manifest["data_fingerprint"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
