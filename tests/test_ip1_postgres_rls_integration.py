"""Opt-in, synthetic-only PostgreSQL RLS and read-only integration checks."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("INSIGHTPILOT_RUN_IP1_POSTGRES_RLS_INTEGRATION") != "1",
    reason="requires explicit opt-in and the dedicated synthetic insightpilot_ip4 database",
)


def _connection_settings() -> tuple[str, str]:
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    app_role = os.environ.get("INSIGHTPILOT_IP4_APP_ROLE")
    if not dsn or not app_role:
        pytest.skip("dedicated synthetic PostgreSQL app DSN and role are required")
    return dsn, app_role


def _scoped_read(connection, tenant_id: str, sql: str, params: tuple = ()):
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        session_user, database = connection.execute(
            "SELECT session_user, current_database()"
        ).fetchone()
        assert session_user == os.environ["INSIGHTPILOT_IP4_APP_ROLE"]
        assert database == "insightpilot_ip4"
        connection.execute("SET LOCAL ROLE insightpilot_reader")
        assert connection.execute("SELECT current_user").fetchone()[0] == "insightpilot_reader"
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        return connection.execute(sql, params).fetchall()


@pytest.mark.parametrize("tenant_id", ("tenant-a", "tenant-b"))
def test_rls_allows_same_tenant_and_filters_cross_tenant_reads_and_joins(tenant_id):
    import psycopg

    dsn, _app_role = _connection_settings()
    other_tenant = "tenant-b" if tenant_id == "tenant-a" else "tenant-a"
    with psycopg.connect(dsn) as connection:
        own_rows = _scoped_read(
            connection,
            tenant_id,
            "SELECT COUNT(*) FROM public.orders WHERE tenant_id = %s",
            (tenant_id,),
        )[0][0]
        assert own_rows > 0
        visible_tenants = _scoped_read(
            connection,
            tenant_id,
            "SELECT DISTINCT tenant_id FROM public.orders ORDER BY tenant_id",
        )
        assert visible_tenants == [(tenant_id,)]
        cross_join_rows = _scoped_read(
            connection,
            tenant_id,
            """SELECT COUNT(*) FROM public.orders own
               JOIN public.orders other ON own.order_id = other.order_id
               WHERE own.tenant_id = %s AND other.tenant_id = %s""",
            (tenant_id, other_tenant),
        )[0][0]
        assert cross_join_rows == 0


@pytest.mark.parametrize(
    ("statement", "expected_sqlstates"),
    (
        ("UPDATE public.orders SET paid_amount_cents = 0 WHERE false", {"25006", "42501"}),
        ("CREATE TABLE public.ip1_rls_integration_must_not_exist (id integer)", {"25006", "42501"}),
    ),
)
def test_reader_cannot_write_or_run_ddl(statement, expected_sqlstates):
    import psycopg

    dsn, _app_role = _connection_settings()
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
            with pytest.raises(psycopg.Error) as error:
                connection.execute(statement)
            assert error.value.sqlstate in expected_sqlstates


def test_read_only_transaction_enforces_statement_timeout():
    import psycopg

    dsn, _app_role = _connection_settings()
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
            connection.execute("SELECT set_config('statement_timeout', %s, true)", ("50ms",))
            with pytest.raises(psycopg.errors.QueryCanceled) as error:
                connection.execute("SELECT pg_sleep(0.25)")
            assert error.value.sqlstate == "57014"
