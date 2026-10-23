"""Real PostgreSQL enforcement checks for IP-1/IP-2 using synthetic fixtures only."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.planner import QueryPlan
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.runner import QueryExecutionError, QueryTimeoutError
from insightpilot.sql import CompiledQuery, compile_query


TENANT_TABLES = (
    "orders", "refunds", "products", "customers", "campaign_attributions",
    "campaigns", "campaign_spend_events", "refund_reversals",
)


class _PermissionWasUnexpectedlyGranted(Exception):
    pass


def _reader_denied(dsn: str, statement: str, tenant_id: str | None = None,
                   *, read_only: bool = False) -> bool:
    import psycopg

    try:
        with psycopg.connect(dsn) as connection:
            with connection.transaction():
                if read_only:
                    connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE insightpilot_reader")
                if tenant_id:
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                try:
                    with connection.transaction():
                        connection.execute(statement)
                except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction):
                    return True
                raise _PermissionWasUnexpectedlyGranted()
    except _PermissionWasUnexpectedlyGranted:
        return False


def _equal_rows(left: list[dict], right: list[dict]) -> bool:
    if len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right):
        if left_row.keys() != right_row.keys():
            return False
        for key in left_row:
            left_value, right_value = left_row[key], right_row[key]
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
                if not math.isclose(left_value, right_value, rel_tol=1e-12, abs_tol=1e-6):
                    return False
            elif left_value != right_value:
                return False
    return True


def verify(database: Path, report_path: Path) -> dict:
    import psycopg
    from psycopg_pool import ConnectionPool

    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    admin_dsn = os.environ.get("INSIGHTPILOT_IP4_ADMIN_DSN")
    app_role = os.environ.get("INSIGHTPILOT_IP4_APP_ROLE")
    if not dsn or not admin_dsn or not app_role:
        raise RuntimeError("Set isolated test app/admin DSNs and app role before PostgreSQL acceptance.")

    with psycopg.connect(admin_dsn) as admin:
        postgres_version = admin.execute("SHOW server_version").fetchone()[0]
        roles = {row[0]: {"superuser": row[1], "bypass_rls": row[2], "can_login": row[3],
                          "inherit": row[4]}
                 for row in admin.execute(
                     "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit FROM pg_roles "
                     "WHERE rolname IN ('insightpilot_reader', %s)", (app_role,))}
        membership = admin.execute(
            "SELECT pg_has_role(%s, 'insightpilot_reader', 'MEMBER')", (app_role,)).fetchone()[0]
        rls = {row[0]: {"enabled": row[1], "forced": row[2]} for row in admin.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(%s)", (list(TENANT_TABLES),))}
        column_privileges = {
            "orders.customer_id": admin.execute(
                "SELECT has_column_privilege('insightpilot_reader','public.orders','customer_id','SELECT')").fetchone()[0],
            "customers.email": admin.execute(
                "SELECT has_column_privilege('insightpilot_reader','public.customers','email','SELECT')").fetchone()[0],
            "customers.phone": admin.execute(
                "SELECT has_column_privilege('insightpilot_reader','public.customers','phone','SELECT')").fetchone()[0],
        }

    role_checks = (
        roles.get("insightpilot_reader") == {"superuser": False, "bypass_rls": False,
                                              "can_login": False, "inherit": False}
        and roles.get(app_role, {}).get("superuser") is False
        and roles.get(app_role, {}).get("bypass_rls") is False
        and roles.get(app_role, {}).get("can_login") is True
        and membership
    )
    rls_checks = len(rls) == len(TENANT_TABLES) and all(
        row["enabled"] and row["forced"] for row in rls.values())

    tenant_probes = {}
    for tenant, other in (("tenant-a", "tenant-b"), ("tenant-b", "tenant-a")):
        with psycopg.connect(dsn) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE insightpilot_reader")
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                own = connection.execute("SELECT count(*) FROM public.orders WHERE tenant_id=%s",
                                         (tenant,)).fetchone()[0]
                foreign = connection.execute("SELECT count(*) FROM public.orders WHERE tenant_id=%s",
                                             (other,)).fetchone()[0]
                foreign_join = connection.execute(
                    "SELECT count(*) FROM public.orders o JOIN public.refunds r USING(order_id) "
                    "WHERE r.tenant_id=%s", (other,)).fetchone()[0]
        tenant_probes[tenant] = {"own_rows": own, "foreign_rows_visible": foreign,
                                 "foreign_join_rows_visible": foreign_join}

    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            missing_context_rows = connection.execute("SELECT count(*) FROM public.orders").fetchone()[0]

    sensitive_sql = (
        "SELECT customer_id FROM public.orders LIMIT 1",
        "SELECT email FROM public.customers LIMIT 1",
        "SELECT phone FROM public.customers LIMIT 1",
    )
    sensitive_checks = {sql: _reader_denied(dsn, sql, "tenant-a") for sql in sensitive_sql}
    dml_checks = {
        "insert": _reader_denied(
            dsn, "INSERT INTO public.orders "
            "(tenant_id,order_id,product_id,region,channel,ordered_at,status,paid_amount_cents) "
            "VALUES ('tenant-a','ip12-dml-probe','p1','华东','web','2026-10-01T00:00:00Z','paid',1)",
            "tenant-a"),
        "update": _reader_denied(dsn, "UPDATE public.orders SET status='cancelled' WHERE false", "tenant-a"),
        "delete": _reader_denied(dsn, "DELETE FROM public.orders WHERE false", "tenant-a"),
    }
    dml_denied = all(dml_checks.values())
    ddl_denied = _reader_denied(dsn, "CREATE TABLE public.ip12_forbidden_ddl_probe(id integer)", "tenant-a")
    temp_ddl_denied = _reader_denied(
        dsn, "CREATE TEMP TABLE ip12_forbidden_temp_probe(id integer)", "tenant-a", read_only=True)

    valid_plan = QueryPlan("marketing_net_roi", "2026-10-01", "2026-11-01", None, None)
    valid_query = compile_query(valid_plan)
    sqlite_engine = SQLiteQueryEngine(database)
    sqlite_rows, _ = sqlite_engine.execute_readonly(valid_query)
    pg_engine = PostgresQueryEngine(dsn, statement_timeout_ms=10_000)
    pg_rows, _ = pg_engine.execute_readonly(valid_query, tenant_id="tenant-a")
    parity = _equal_rows(sqlite_rows, pg_rows)

    malicious_region = "华东' OR 1=1 --"
    injected_query = compile_query(QueryPlan(
        "approved_refund_order_rate", "2026-10-01", "2026-11-01", malicious_region, None))
    injection_rows, _ = pg_engine.execute_readonly(injected_query, tenant_id="tenant-a")
    sqlite_injection_rows, _ = sqlite_engine.execute_readonly(injected_query)
    injection_safe = (malicious_region not in injected_query.sql and
                      malicious_region in injected_query.params and
                      _equal_rows(injection_rows, sqlite_injection_rows) and
                      len(injection_rows) == 1 and injection_rows[0]["numerator"] == 0)

    boundary_checks = {}
    for end_date in ("2026-10-31", "2026-11-01"):
        plan = QueryPlan("marketing_net_roi", "2026-10-01", end_date, None, None)
        query = compile_query(plan)
        sqlite_result, _ = sqlite_engine.execute_readonly(query)
        pg_result, _ = pg_engine.execute_readonly(query, tenant_id="tenant-a")
        boundary_checks[end_date] = _equal_rows(sqlite_result, pg_result)
        boundary_checks[f"{end_date}_rows"] = pg_result
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
            oct31_spend_events = connection.execute(
                "SELECT count(*) FROM public.campaign_spend_events "
                "WHERE spent_at >= '2026-10-31T00:00:00Z' AND spent_at < '2026-11-01T00:00:00Z'").fetchone()[0]
            snapshot = connection.execute(
                "SELECT value FROM public.data_meta WHERE key='snapshot_at'").fetchone()[0]
            visible_reversals = connection.execute(
                "SELECT count(*) FROM public.refund_reversals WHERE recorded_at<=%s", (snapshot,)).fetchone()[0]
    boundary_checks["october_31_events_present"] = oct31_spend_events > 0
    boundary_checks["recorded_refund_reversals_present"] = visible_reversals > 0
    boundary_checks["utc_right_edge_changes_spend"] = (
        boundary_checks["2026-11-01_rows"][0]["spend_cents"] >
        boundary_checks["2026-10-31_rows"][0]["spend_cents"]
    )
    del boundary_checks["2026-10-31_rows"]
    del boundary_checks["2026-11-01_rows"]

    # Add a future-recorded reversal only to the ephemeral PostgreSQL fixture.
    # If the SQL loses its snapshot cutoff, SQLite parity fails. Always remove it.
    marker_reversal = "ip12-future-snapshot-probe"
    with psycopg.connect(admin_dsn) as admin:
        refund_id = admin.execute(
            "SELECT r.refund_id FROM public.refunds r JOIN public.orders o USING(order_id) "
            "WHERE r.tenant_id='tenant-a' AND o.tenant_id='tenant-a' AND r.status='approved' "
            "AND o.ordered_at >= '2026-10-01T00:00:00Z' AND o.ordered_at < '2026-11-01T00:00:00Z' "
            "ORDER BY r.refund_id LIMIT 1").fetchone()[0]
        admin.execute(
            "INSERT INTO public.refund_reversals "
            "(reversal_id, refund_id, reversed_at, recorded_at, amount_cents, tenant_id) "
            "VALUES (%s,%s,'2027-02-28T00:00:00Z','9999-01-01T00:00:00Z',1,'tenant-a')",
            (marker_reversal, refund_id))
    try:
        future_reversal_rows, _ = pg_engine.execute_readonly(valid_query, tenant_id="tenant-a")
        boundary_checks["future_recorded_refund_reversal_ignored"] = _equal_rows(
            future_reversal_rows, sqlite_rows)
    finally:
        with psycopg.connect(admin_dsn) as admin:
            admin.execute("DELETE FROM public.refund_reversals WHERE reversal_id=%s AND tenant_id='tenant-a'",
                          (marker_reversal,))

    # Reuse one physical pool connection to prove transaction-local tenant state clears.
    pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=1, timeout=0.2, open=True)
    pooled = PostgresQueryEngine(dsn, pool=pool, statement_timeout_ms=10_000)
    tenant_context_cleared = False
    pool_exhaustion_recovered = False
    timeout_recovered = False
    statement_timeout_observed = False
    try:
        refund_query = compile_query(QueryPlan(
            "approved_refund_order_rate", "2026-10-01", "2026-10-08", None, None))
        pooled.execute_readonly(refund_query, tenant_id="tenant-a")
        pooled.execute_readonly(refund_query, tenant_id="tenant-b")
        with pool.connection() as connection:
            with connection.transaction():
                current_tenant = connection.execute(
                    "SELECT current_setting('app.tenant_id', true)").fetchone()[0]
        tenant_context_cleared = current_tenant in (None, "")

        with pool.connection():
            try:
                pooled.execute_readonly(refund_query, tenant_id="tenant-a")
            except QueryExecutionError:
                exhausted = True
            else:
                exhausted = False
        pooled.execute_readonly(refund_query, tenant_id="tenant-a")
        pool_exhaustion_recovered = exhausted

        short_timeout = PostgresQueryEngine(dsn, pool=pool, statement_timeout_ms=50)
        try:
            try:
                short_timeout.execute_readonly(CompiledQuery(
                    "SELECT COUNT(*) AS numerator FROM orders o "
                    "CROSS JOIN orders a CROSS JOIN orders b LIMIT 1", ()),
                    tenant_id="tenant-a")
            except QueryTimeoutError:
                statement_timeout_observed = True
        finally:
            short_timeout.close()
        pooled.execute_readonly(refund_query, tenant_id="tenant-a")
        timeout_recovered = statement_timeout_observed
    finally:
        pooled.close()
        pool.close()
        pg_engine.close()

    checks = {
        "database_roles_and_membership": role_checks,
        "rls_enabled_and_forced_on_tenant_tables": rls_checks,
        "column_privileges_exclude_sensitive_fields": not any(column_privileges.values()),
        "cross_tenant_reads_and_joins_blocked": all(
            row["own_rows"] > 0 and row["foreign_rows_visible"] == 0 and
            row["foreign_join_rows_visible"] == 0 for row in tenant_probes.values()),
        "missing_tenant_fails_closed": missing_context_rows == 0,
        "database_denies_sensitive_column_reads": all(sensitive_checks.values()),
        "database_denies_dml_and_ddl": dml_denied and ddl_denied and temp_ddl_denied,
        "bound_parameter_injection_is_data": injection_safe,
        "sqlite_postgres_marketing_roi_parity": parity,
        "ip2_utc_refund_boundary_cases": all(boundary_checks.values()),
        "transaction_local_tenant_context_cleared": tenant_context_cleared,
        "pool_exhaustion_recovery": pool_exhaustion_recovered,
        "statement_timeout_and_recovery": statement_timeout_observed and timeout_recovered,
    }
    report = {
        "status": "passed" if all(checks.values()) else "failed",
        "model_calls": 0,
        "synthetic_data_only": True,
        "ip4_frozen_artifacts_modified": False,
        "tenant_table_count": len(TENANT_TABLES),
        "postgres_version": postgres_version,
        "role_properties": roles,
        "rls_tables": rls,
        "sensitive_column_privileges": column_privileges,
        "tenant_probes": tenant_probes,
        "missing_context_visible_rows": missing_context_rows,
        "sensitive_read_denials": list(sensitive_checks.values()),
        "dml_denied": dml_denied,
        "dml_denials": dml_checks,
        "ddl_denied": ddl_denied,
        "temp_ddl_denied_in_read_only_transaction": temp_ddl_denied,
        "bound_injection_payload_is_sql": malicious_region in injected_query.sql,
        "sqlite_postgres_roi_rows_equal": parity,
        "utc_and_refund_boundary_checks": boundary_checks,
        "tenant_context_cleared": tenant_context_cleared,
        "tenant_context_after_pool_release": current_tenant,
        "pool_exhaustion_recovered": pool_exhaustion_recovered,
        "statement_timeout_observed_and_recovered": statement_timeout_observed and timeout_recovered,
        "checks": checks,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("reports/ip1-ip2-postgres-acceptance-v1.json"))
    args = parser.parse_args()
    report = verify(args.database, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
