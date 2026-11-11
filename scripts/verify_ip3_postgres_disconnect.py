"""Force-drop one PostgreSQL pool session and verify pool reconnection."""

from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.query_engine import PostgresQueryEngine
from insightpilot.rule_baseline import RulePlanner
from insightpilot.sql import compile_query


def verify(tenant_id: str = "tenant-a") -> dict:
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("Install requirements.txt before PostgreSQL disconnect verification") from None
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    admin_dsn = os.environ.get("INSIGHTPILOT_IP4_ADMIN_DSN")
    if not dsn or not admin_dsn:
        raise RuntimeError("Set test-only INSIGHTPILOT_IP4_APP_DSN and INSIGHTPILOT_IP4_ADMIN_DSN")

    engine = PostgresQueryEngine(dsn, tenant_id=tenant_id, statement_timeout_ms=10_000)
    query = compile_query(RulePlanner().plan(
        "2026-10-01 到 2026-10-31 华东批准退款金额率是多少？"))
    killed = False
    forced_disconnect = "not_checked"
    try:
        pool = engine._connection_pool()
        with pool.connection() as connection:
            backend_pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                killed = admin.execute("SELECT pg_terminate_backend(%s)", (backend_pid,)).fetchone()[0]
            try:
                connection.execute("SELECT 1")
            except Exception as exc:
                forced_disconnect = type(exc).__name__
            else:
                forced_disconnect = "connection_survived"
        if not killed or forced_disconnect == "connection_survived":
            raise RuntimeError("PostgreSQL backend termination did not disconnect the pooled session")

        rows, backend = engine.execute_readonly(query)
        report = {"status": "passed", "backend": backend, "tenant": tenant_id,
                  "forced_backend_termination": True, "disconnect_error_class": forced_disconnect,
                  "pool_reconnected": True, "recovered_query_row_count": len(rows),
                  "automatic_query_retry": False}
        return report
    finally:
        engine.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/ip3-postgres-disconnect.json"))
    args = parser.parse_args()
    try:
        report = verify()
    except Exception as exc:
        report = {"status": "failed", "error_class": type(exc).__name__,
                  "error": "details omitted to avoid connection-string disclosure"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)
