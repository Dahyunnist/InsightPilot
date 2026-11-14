"""Verify tenant RLS with isolated synthetic markers in the dedicated IP-4 test DB."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path


def visible_markers_match_tenant(tenant_id: str | None, markers: list[str],
                                expected_marker: str | None = None) -> bool:
    expected = {"tenant-a": ["canary-a"], "tenant-b": ["canary-b"]}
    if expected_marker is not None and tenant_id in ("tenant-a", "tenant-b"):
        return markers == [expected_marker]
    return markers == expected.get(tenant_id, [])


def verify() -> dict:
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError:
        raise RuntimeError("Install requirements.txt before PostgreSQL canary verification") from None

    admin_dsn = os.environ.get("INSIGHTPILOT_IP4_ADMIN_DSN")
    app_dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    app_role = os.environ.get("INSIGHTPILOT_IP4_APP_ROLE")
    if not admin_dsn or not app_dsn or not app_role:
        raise RuntimeError("Set the dedicated IP-4 admin/app DSNs and app role")

    table_created = False
    pool = None
    try:
        with psycopg.connect(admin_dsn) as admin:
            database = admin.execute("SELECT current_database()").fetchone()[0]
            if database != "insightpilot_ip4":
                raise RuntimeError("Refusing canary outside dedicated insightpilot_ip4 test database")
            if admin.execute("SELECT to_regclass('public.ip1_rls_canary')").fetchone()[0] is not None:
                raise RuntimeError("Canary table already exists; refusing to overwrite it")
            roles = {}
            for role in (app_role, "insightpilot_reader"):
                row = admin.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("Required application role is missing")
                roles[role] = {"superuser": bool(row[0]), "bypassrls": bool(row[1])}
                if row[0] or row[1]:
                    raise RuntimeError("Application roles must not be superuser or BYPASSRLS")

            marker_a, marker_b = uuid.uuid4().hex, uuid.uuid4().hex
            admin.execute("""
                CREATE TABLE public.ip1_rls_canary (
                    tenant_id TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, marker)
                )
            """)
            table_created = True
            admin.execute("ALTER TABLE public.ip1_rls_canary ENABLE ROW LEVEL SECURITY")
            admin.execute("ALTER TABLE public.ip1_rls_canary FORCE ROW LEVEL SECURITY")
            admin.execute("""
                CREATE POLICY ip1_rls_canary_tenant_policy ON public.ip1_rls_canary
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
            """)
            admin.execute("GRANT SELECT ON public.ip1_rls_canary TO insightpilot_reader")
            admin.execute("INSERT INTO public.ip1_rls_canary (tenant_id, marker) VALUES (%s, %s), (%s, %s)",
                          ("tenant-a", marker_a, "tenant-b", marker_b))

        pool = ConnectionPool(conninfo=app_dsn, min_size=1, max_size=1, timeout=5, open=True)

        def read_markers(tenant_id: str | None) -> tuple[list[str], int, str, str]:
            with pool.connection() as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    session_user, database = connection.execute(
                        "SELECT session_user, current_database()"
                    ).fetchone()
                    if session_user != app_role or database != "insightpilot_ip4":
                        raise RuntimeError("Application DSN role or database mismatch")
                    connection.execute("SET LOCAL ROLE insightpilot_reader")
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id or "",))
                    rows = connection.execute(
                        "SELECT marker FROM public.ip1_rls_canary ORDER BY marker"
                    ).fetchall()
                    backend_pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                    current_user = connection.execute("SELECT current_user").fetchone()[0]
                    return [row[0] for row in rows], backend_pid, session_user, current_user

        observations = {}
        for label, tenant in (("tenant_a", "tenant-a"), ("tenant_b", "tenant-b"),
                              ("tenant_a_reused", "tenant-a"), ("missing", None),
                              ("unknown", "tenant-unknown")):
            markers, pid, session_user, current_user = read_markers(tenant)
            observations[label] = {
                "visible_count": len(markers),
                "visibility_matches_context": visible_markers_match_tenant(
                    tenant, markers, marker_a if tenant == "tenant-a" else marker_b if tenant == "tenant-b" else None),
                "backend_pid": pid,
                "session_role_matches": session_user == app_role,
                "effective_role_is_reader": current_user == "insightpilot_reader",
            }

        pids = [observations[key]["backend_pid"] for key in ("tenant_a", "tenant_b", "tenant_a_reused")]
        passed = (all(item["visibility_matches_context"] and item["session_role_matches"] and
                      item["effective_role_is_reader"] for item in observations.values())
                  and pids[0] == pids[1] == pids[2]
                  and all(not value["superuser"] and not value["bypassrls"] for value in roles.values()))
        return {"status": "passed" if passed else "failed", "database": "insightpilot_ip4",
                "roles": roles, "observations": observations,
                "pool_connection_reused": pids[0] == pids[1] == pids[2], "model_calls": 0}
    finally:
        if pool is not None:
            pool.close()
        if table_created:
            with psycopg.connect(admin_dsn) as admin:
                if admin.execute("SELECT current_database()").fetchone()[0] != "insightpilot_ip4":
                    raise RuntimeError("Refusing canary cleanup outside dedicated test database")
                admin.execute("DROP TABLE public.ip1_rls_canary")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = verify()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "model_calls": 0}))
        raise SystemExit(1) from None
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
