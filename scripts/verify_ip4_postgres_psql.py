"""PostgreSQL/RLS parity probe using psql when psycopg wheels are unavailable."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlglot import transpile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.rule_baseline import RulePlanner
from insightpilot.sql import compile_query


def _pg_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _bound_sql(plan) -> str:
    query = compile_query(plan)
    statement = transpile(query.sql, read="sqlite", write="postgres", pretty=False)[0]
    parts = statement.split("%s")
    if len(parts) != len(query.params) + 1:
        raise ValueError("SQL placeholder count mismatch")
    return "".join(part + (_pg_literal(query.params[index]) if index < len(query.params) else "")
                   for index, part in enumerate(parts))


def _type_name(raw_type: str | None) -> str:
    value = (raw_type or "TEXT").upper().split("(")[0]
    return {"INTEGER": "BIGINT", "INT": "BIGINT", "REAL": "DOUBLE PRECISION",
            "FLOAT": "DOUBLE PRECISION", "TEXT": "TEXT", "BLOB": "BYTEA"}.get(value, "TEXT")


def _seed_script(database: Path, bootstrap_path: Path) -> str:
    import sqlite3

    source = sqlite3.connect(database)
    try:
        tables = [row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        infos = {table: source.execute(f'PRAGMA table_info("{table}")').fetchall() for table in tables}
        rows = {table: source.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
    finally:
        source.close()

    output = io.StringIO()
    output.write("DROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\n")
    output.write("DROP ROLE IF EXISTS insightpilot_ip4_app;\n")
    output.write("CREATE ROLE insightpilot_ip4_app LOGIN;\n")
    for table in tables:
        info = infos[table]
        primary = [item[1] for item in sorted((item for item in info if item[5]), key=lambda item: item[5])]
        declarations = [f'"{name}" {_type_name(raw_type)}' + (" NOT NULL" if not_null else "")
                        for _cid, name, raw_type, not_null, _default, _pk in info]
        if table == "data_meta":
            if primary:
                declarations.append("PRIMARY KEY (" + ",".join(f'"{name}"' for name in primary) + ")")
        else:
            declarations.append('"tenant_id" TEXT NOT NULL')
            if primary:
                declarations.append("PRIMARY KEY (\"tenant_id\"," + ",".join(f'"{name}"' for name in primary) + ")")
        output.write(f'CREATE TABLE public."{table}" ({", ".join(declarations)});\n')

    output.write(bootstrap_path.read_text(encoding="utf-8") + "\n")
    output.write("GRANT insightpilot_reader TO insightpilot_ip4_app;\n")
    for table in tables:
        info = infos[table]
        names = [item[1] for item in info]
        scoped = table != "data_meta"
        columns = names + (["tenant_id"] if scoped else [])
        output.write("COPY public.\"" + table + "\" (" + ",".join(f'"{name}"' for name in columns)
                     + ") FROM STDIN WITH (FORMAT csv);\n")
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer, lineterminator="\n")
        for tenant in (("tenant-a", "tenant-b") if scoped else (None,)):
            for row in rows[table]:
                writer.writerow((*row, tenant) if scoped else row)
        output.write(csv_buffer.getvalue())
        output.write("\\.\n")
    return output.getvalue()


def _psql(container: str, sql: str, *, role: str, required: bool = True) -> subprocess.CompletedProcess:
    command = ["docker", "exec", "-i", container, "psql", "-X", "-A", "-t", "-q",
               "-v", "ON_ERROR_STOP=1", "-U", role, "-d", "insightpilot_ip4"]
    result = subprocess.run(command, input=sql, text=True, capture_output=True, encoding="utf-8")
    if required and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


def verify(container: str, database: Path, blind_path: Path, gold_path: Path,
           bootstrap_path: Path) -> dict:
    _psql(container, _seed_script(database, bootstrap_path), role="postgres")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in
            (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    answered = [case for case in cases if gold[case["case_id"]]["expected_status"] == "answered"]
    checks = {"tenant-a": [], "tenant-b": []}
    for tenant in checks:
        for case in answered:
            plan = RulePlanner().plan(case["question"])
            query_sql = _bound_sql(plan)
            wrapped = ("BEGIN; SET LOCAL ROLE insightpilot_reader; "
                       f"SET LOCAL app.tenant_id = '{tenant}'; "
                       f"SELECT COALESCE(json_agg(to_jsonb(q)), '[]'::json)::text FROM ({query_sql}) q; COMMIT;")
            result = _psql(container, wrapped, role="insightpilot_ip4_app")
            actual = json.loads(result.stdout.strip().splitlines()[-1])
            expected = gold[case["case_id"]]["expected_rows"]
            # Compare group sets and every independently frozen expected field.
            actual_map = {row.get("group_value"): row for row in actual}
            expected_map = {row.get("group_value"): row for row in expected}
            matched = actual_map.keys() == expected_map.keys()
            if matched:
                for group, expected_row in expected_map.items():
                    for field, expected_value in expected_row.items():
                        actual_value = actual_map[group].get(field)
                        if expected_value is None:
                            matched &= actual_value is None
                        elif isinstance(expected_value, (int, float)):
                            matched &= isinstance(actual_value, (int, float)) and abs(actual_value - expected_value) <= 1e-6
                        else:
                            matched &= actual_value == expected_value
            checks[tenant].append({"case_id": case["case_id"], "passed": bool(matched)})

    no_scope = _psql(container,
        "BEGIN; SET LOCAL ROLE insightpilot_reader; SELECT COUNT(*) FROM orders LIMIT 1; COMMIT;",
        role="insightpilot_ip4_app")
    no_scope_rows = no_scope.stdout.strip().splitlines()
    no_scope_fails_closed = bool(no_scope_rows and no_scope_rows[-1] == "0")

    dml = _psql(container, "DELETE FROM orders;", role="insightpilot_ip4_app", required=False)
    dml_denied = dml.returncode != 0

    timeout_sql = ("BEGIN; SET LOCAL ROLE insightpilot_reader; SET LOCAL app.tenant_id='tenant-a'; "
                   "SET LOCAL statement_timeout='50ms'; "
                   "SELECT COUNT(*) FROM orders a CROSS JOIN orders b CROSS JOIN orders c LIMIT 1; COMMIT;")
    timeout = _psql(container, timeout_sql, role="insightpilot_ip4_app", required=False)
    timeout_observed = timeout.returncode != 0 and "statement timeout" in timeout.stderr.lower()
    recovered = _psql(container,
        "BEGIN; SET LOCAL ROLE insightpilot_reader; SET LOCAL app.tenant_id='tenant-a'; "
        "SELECT COUNT(*) FROM orders LIMIT 1; COMMIT;", role="insightpilot_ip4_app")
    recovery_observed = recovered.returncode == 0 and int(recovered.stdout.strip().splitlines()[-1]) > 0
    passed = (all(check["passed"] for tenant_checks in checks.values() for check in tenant_checks)
              and no_scope_fails_closed and dml_denied and timeout_observed and recovery_observed)
    return {"status": "passed" if passed else "failed", "backend": "PostgreSQL 16 / psql + compiled SQLGlot output",
            "answerable_case_count": len(answered), "tenant_results": checks,
            "missing_tenant_fails_closed": no_scope_fails_closed, "dml_denied": dml_denied,
            "statement_timeout_observed": timeout_observed, "connection_after_timeout_works": recovery_observed,
            "pool_exhaustion": "not_measured; psycopg_pool is unavailable in this host environment",
            "application_role": "insightpilot_ip4_app SET ROLE insightpilot_reader"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--blind", type=Path, default=Path("evals/ip4-blind-v1.jsonl"))
    parser.add_argument("--gold", type=Path, default=Path("evals/ip4-gold-v1.jsonl"))
    parser.add_argument("--bootstrap", type=Path, default=Path("docs/postgresql-rls-bootstrap.sql"))
    parser.add_argument("--output", type=Path, default=Path("reports/ip4-postgres-psql-verification.json"))
    args = parser.parse_args()
    result = verify(args.container, args.database, args.blind, args.gold, args.bootstrap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
