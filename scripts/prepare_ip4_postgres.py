"""Load the IP4 synthetic fixture into a dedicated two-tenant PostgreSQL test DB."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


_TYPE_MAP = {"INTEGER": "BIGINT", "INT": "BIGINT", "REAL": "DOUBLE PRECISION",
             "FLOAT": "DOUBLE PRECISION", "TEXT": "TEXT", "BLOB": "BYTEA"}


def prepare(sqlite_path: Path, dsn: str, app_role: str, app_password: str, bootstrap_path: Path) -> dict:
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise RuntimeError("PostgreSQL fixture loader requires the project's psycopg[binary,pool] dependency") from None

    source = sqlite3.connect(sqlite_path)
    try:
        tables = [row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        table_info = {name: source.execute(f'PRAGMA table_info("{name}")').fetchall() for name in tables}
        source_rows = {name: source.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables}
    finally:
        source.close()

    with psycopg.connect(dsn, autocommit=True) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if database_name != "insightpilot_ip4":
            raise RuntimeError("Refusing to reset any database except the dedicated insightpilot_ip4 test database")
        # This loader is intentionally destructive only inside the dedicated ephemeral IP4 test database.
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        role_exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (app_role,)).fetchone()
        if role_exists:
            connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(app_role), sql.Literal(app_password)))
        else:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(app_role), sql.Literal(app_password)))
        for table in tables:
            columns = table_info[table]
            primary = [row[1] for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])]
            declarations = []
            for _, name, raw_type, not_null, default, _pk in columns:
                db_type = _TYPE_MAP.get((raw_type or "TEXT").upper().split("(")[0], "TEXT")
                null_clause = " NOT NULL" if not_null else ""
                default_clause = f" DEFAULT {default}" if default is not None else ""
                declarations.append(sql.SQL("{} {}{}{}").format(
                    sql.Identifier(name), sql.SQL(db_type), sql.SQL(null_clause), sql.SQL(default_clause)))
            tenant_scoped = table != "data_meta"
            if tenant_scoped:
                declarations.append(sql.SQL("tenant_id TEXT NOT NULL"))
                if primary:
                    primary_cols = [sql.Identifier("tenant_id"), *(sql.Identifier(name) for name in primary)]
                    declarations.append(sql.SQL("PRIMARY KEY ({})").format(sql.SQL(", ").join(primary_cols)))
            elif primary:
                declarations.append(sql.SQL("PRIMARY KEY ({})").format(
                    sql.SQL(", ").join(sql.Identifier(name) for name in primary)))
            connection.execute(sql.SQL("CREATE TABLE {} ({})").format(
                sql.Identifier("public", table), sql.SQL(", ").join(declarations)))

        # Preserve the source query indexes, with tenant_id first so PostgreSQL
        # can use them under the row-level security predicate as well.
        tenant_indexes = {
            "orders": (("ordered_at", "region"),),
            "refunds": (("refunded_at", "status", "order_id"), ("order_id", "status")),
            "refund_reversals": (("refund_id", "recorded_at"),),
            "campaign_attributions": (("order_id",),),
            "campaign_spend_events": (("business_event_id", "revision", "recorded_at"),
                                       ("spent_at", "campaign_id")),
        }
        for table, indexes in tenant_indexes.items():
            for index_number, indexed_columns in enumerate(indexes, start=1):
                index_name = f"ip4_{table}_{index_number}_tenant_idx"
                connection.execute(sql.SQL("CREATE INDEX {} ON {} ({})").format(
                    sql.Identifier(index_name), sql.Identifier("public", table),
                    sql.SQL(", ").join(sql.Identifier(column)
                                        for column in (("tenant_id",) + indexed_columns))))

        connection.execute(bootstrap_path.read_text(encoding="utf-8"))
        connection.execute(sql.SQL("GRANT insightpilot_reader TO {}").format(sql.Identifier(app_role)))
        for table in tables:
            if table == "data_meta":
                columns = [row[1] for row in table_info[table]]
                copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier("public", table), sql.SQL(", ").join(sql.Identifier(name) for name in columns))
                with connection.cursor().copy(copy_sql) as copy:
                    for row in source_rows[table]:
                        copy.write_row(row)
                continue
            columns = [row[1] for row in table_info[table]] + ["tenant_id"]
            copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier("public", table), sql.SQL(", ").join(sql.Identifier(name) for name in columns))
            for tenant in ("tenant-a", "tenant-b"):
                with connection.cursor().copy(copy_sql) as copy:
                    for row in source_rows[table]:
                        copy.write_row((*row, tenant))

        for table in tables:
            if table != "data_meta":
                connection.execute(sql.SQL("ANALYZE {}").format(sql.Identifier("public", table)))

    return {"database": "insightpilot_ip4", "source_table_count": len(tables),
            "tenant_rows": {"tenant-a": sum(len(source_rows[name]) for name in tables if name != "data_meta"),
                            "tenant-b": sum(len(source_rows[name]) for name in tables if name != "data_meta")},
            "tenant_isolation": "each RLS table has composite tenant primary key; all source rows loaded for both tenants"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--bootstrap", type=Path, default=Path("docs/postgresql-rls-bootstrap.sql"))
    args = parser.parse_args()
    dsn = os.environ.get("INSIGHTPILOT_IP4_ADMIN_DSN")
    app_role = os.environ.get("INSIGHTPILOT_IP4_APP_ROLE")
    app_password = os.environ.get("INSIGHTPILOT_IP4_APP_PASSWORD")
    if not dsn or not app_role or not app_password:
        parser.error("set INSIGHTPILOT_IP4_ADMIN_DSN, INSIGHTPILOT_IP4_APP_ROLE, and INSIGHTPILOT_IP4_APP_PASSWORD for the isolated test database")
    print(prepare(args.database, dsn, app_role, app_password, args.bootstrap))


if __name__ == "__main__":
    main()
