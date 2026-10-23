"""Diagnose R01/R02 development copies without model calls or changing IP4 artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.agent import InsightPilot
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.rule_baseline import RulePlanner
from insightpilot.runner import QueryExecutionError


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/insightpilot-multidomain.sqlite"
DEV_CASES = ROOT / "evals/dev/ip4-r01-r02-dev-v1.jsonl"
OUTPUT = ROOT / "reports/ip4-r01-r02-dev-diagnostic.json"
EXPECTED = (("tenant-1", "tenant-a"), ("tenant-2", "tenant-b"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cases() -> list[dict]:
    return [json.loads(line) for line in DEV_CASES.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _run_copy(case: dict, backend: str, tenant_id: str | None) -> dict:
    engine = (SQLiteQueryEngine(DATABASE) if backend == "sqlite" else
              PostgresQueryEngine(os.environ["INSIGHTPILOT_IP4_APP_DSN"], tenant_id=tenant_id))
    core = InsightPilot(RulePlanner(), DATABASE, query_engine=engine, tenant_id=tenant_id)
    result = core.analyze(case["question"], tenant_id=tenant_id)
    identity_ok = (result.get("data_version") == "insightpilot-multidomain-v1" and
                   result.get("data_fingerprint") ==
                   "7205a30cb9a6559654b70d8cb93491120f1b572c5d06af4e90f940e68d864580")
    detail = {
        "dev_case_id": case["dev_case_id"], "source_case_id": case["source_case_id"],
        "backend": backend, "tenant_slot": ("tenant-1" if tenant_id == "tenant-a" else
                                              "tenant-2" if tenant_id == "tenant-b" else None),
        "status": result.get("status"),
        "stage": result.get("stage"), "failure_detail": result.get("failure_detail"),
        "plan_passed": result.get("status") in ("answered", "clarify", "refused") or
                       result.get("stage") != "planning",
        "schema_identity_passed": identity_ok,
        "sql_compile_passed": result.get("stage") not in ("planning", "dataset", "compilation"),
        "db_execution_passed": result.get("status") == "answered" or result.get("stage") != "query",
        "verification_passed": result.get("verification", {}).get("passed") is True,
        "model_calls": result.get("model_call_count", 0),
    }
    close_engine = getattr(engine, "close", None)
    if close_engine:
        close_engine()
    return detail


def main() -> None:
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise SystemExit("Set INSIGHTPILOT_IP4_APP_DSN to a non-superuser PG test role.")
    cases = _cases()
    details = [_run_copy(case, "sqlite", None) for case in cases]
    for tenant_slot, tenant_id in EXPECTED:
        details.extend(_run_copy(case, "postgresql", tenant_id) for case in cases)

    tenant_scope = {}
    import psycopg
    for tenant_slot, tenant_id in EXPECTED:
        with psycopg.connect(dsn) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL ROLE insightpilot_reader")
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                visible = sorted(row[0] for row in connection.execute(
                    "SELECT DISTINCT tenant_id FROM orders ORDER BY tenant_id").fetchall())
        tenant_scope[tenant_slot] = {"visible_tenant_count": len(visible),
                                     "only_own_tenant_visible": visible == [tenant_id]}
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL ROLE insightpilot_reader")
            visible = connection.execute(
                "SELECT DISTINCT tenant_id FROM orders ORDER BY tenant_id").fetchall()
    tenant_scope["missing_context"] = {"rejected": visible == [],
                                         "visible_tenant_count": len(visible)}

    result = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "development_only": True, "frozen_blind_rerun": False, "model_calls": 0,
        "dev_copy_sha256": hashlib.sha256(DEV_CASES.read_bytes()).hexdigest(),
        "dev_sql_fingerprints": {row["dev_case_id"]: row.get("failure_detail", {}).get("sql_sha256")
                                 for row in details if row.get("failure_detail")},
        "classification_note": "Errors include only layer/category/class, tenant-context presence, SQL fingerprint and parameter count; raw SQL, params, tenant IDs, DSN, and provider data are omitted from failure details.",
        "diagnostics": details, "tenant_scope_probe": tenant_scope,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "diagnostics": details,
                      "tenant_scope_probe": tenant_scope}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
