from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from data.inventory_fixture import create_inventory_fixture
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.runner import QueryExecutionError
from insightpilot.sql import compile_query
from scripts.prepare_ip4_postgres import prepare


@pytest.mark.skipif(os.environ.get("INSIGHTPILOT_RUN_IP2_INVENTORY_POSTGRES_INTEGRATION") != "1",
                    reason="requires explicit opt-in and a dedicated random inventory test database")
def test_inventory_turnover_sqlite_postgres_parity_and_tenant_scope():
    required = ("INSIGHTPILOT_IP2_INVENTORY_ADMIN_DSN", "INSIGHTPILOT_IP2_INVENTORY_APP_DSN",
                "INSIGHTPILOT_IP2_INVENTORY_APP_ROLE", "INSIGHTPILOT_IP2_INVENTORY_APP_PASSWORD",
                "INSIGHTPILOT_IP2_INVENTORY_DATABASE")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        pytest.skip("dedicated PostgreSQL integration settings are not configured")

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ip2-inventory-postgres-") as temporary_root:
        database = create_inventory_fixture(Path(temporary_root) / "inventory.sqlite")
        database_name = os.environ["INSIGHTPILOT_IP2_INVENTORY_DATABASE"]
        assert re.fullmatch(r"insightpilot_ip2_inventory_[a-f0-9]{10}", database_name)
        prepare(database, os.environ["INSIGHTPILOT_IP2_INVENTORY_ADMIN_DSN"],
                os.environ["INSIGHTPILOT_IP2_INVENTORY_APP_ROLE"],
                os.environ["INSIGHTPILOT_IP2_INVENTORY_APP_PASSWORD"],
                root / "docs/postgresql-ip2-inventory-bootstrap.sql", expected_database=database_name,
                included_tables=("data_meta", "inventory_daily_snapshots", "inventory_cogs_events"))
        query = compile_query(QueryPlan(
            "inventory_turnover", "2026-11-01", "2026-11-04", None, "inventory_category"))
        sqlite_rows, _ = SQLiteQueryEngine(database).execute_readonly(query)

        app_dsn = os.environ["INSIGHTPILOT_IP2_INVENTORY_APP_DSN"]
        missing_scope = PostgresQueryEngine(app_dsn)
        with pytest.raises(QueryExecutionError, match="tenant_id is required"):
            missing_scope.execute_readonly(query)

        for tenant in ("tenant-a", "tenant-b"):
            engine = PostgresQueryEngine(app_dsn, tenant_id=tenant)
            try:
                postgres_rows, _ = engine.execute_readonly(query)
                assert [row["group_value"] for row in postgres_rows] == [row["group_value"] for row in sqlite_rows]
                for postgres_row, sqlite_row in zip(postgres_rows, sqlite_rows):
                    for key in ("numerator_cents", "denominator_cents", "value"):
                        if sqlite_row[key] is None:
                            assert postgres_row[key] is None
                        else:
                            assert float(postgres_row[key]) == pytest.approx(float(sqlite_row[key]))
            finally:
                engine.close()

        import psycopg

        with psycopg.connect(app_dsn) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE insightpilot_reader")
                connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
                visible = connection.execute(
                    "SELECT DISTINCT tenant_id FROM inventory_daily_snapshots").fetchall()
                assert visible == [("tenant-a",)]
                connection.execute("SELECT set_config('app.tenant_id', '', true)")
                assert connection.execute("SELECT count(*) FROM inventory_daily_snapshots").fetchone()[0] == 0
            with psycopg.connect(app_dsn) as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL ROLE insightpilot_reader")
                    connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        connection.execute(
                            "INSERT INTO inventory_daily_snapshots "
                            "(tenant_id, snapshot_id, category, snapshot_at, local_date, inventory_value_cents) "
                            "VALUES ('tenant-a', 'permission-probe', 'test', "
                            "'2026-11-01T00:00:00Z', '2026-11-01', 1)")

            # A distinct inventory table confirms the same tenant policy applies across both source tables.
            with psycopg.connect(app_dsn) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL ROLE insightpilot_reader")
                    connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
                    assert connection.execute(
                        "SELECT DISTINCT tenant_id FROM inventory_cogs_events ORDER BY tenant_id"
                    ).fetchall() == [("tenant-a",)]
