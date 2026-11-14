"""Exercise PostgreSQL data_meta statement timeout and clean recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.query_engine import PostgresQueryEngine
from insightpilot.runner import QueryTimeoutError


def verify(app_dsn: str, admin_dsn: str, *, tenant_id: str = "tenant-a",
           statement_timeout_ms: int = 100) -> dict:
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("PostgreSQL verification requires psycopg[binary,pool]") from None

    timeout_result = "not_checked"
    probe_name = "insightpilot-ip1-data-meta-timeout-probe"
    probe_dsn = psycopg.conninfo.make_conninfo(app_dsn, application_name=probe_name)
    outcome: dict[str, object] = {}
    finished = threading.Event()
    with psycopg.connect(admin_dsn) as blocker:
        admin_database = blocker.execute("SELECT current_database()").fetchone()[0]
        if admin_database != "insightpilot_ip4":
            raise RuntimeError("Refusing any database except the dedicated insightpilot_ip4 test database")
        with psycopg.connect(app_dsn) as app_check:
            app_database = app_check.execute("SELECT current_database()").fetchone()[0]
        if app_database != admin_database:
            raise RuntimeError("Admin and reader test DSNs must target the same dedicated database")
        blocker.execute("LOCK TABLE public.data_meta IN ACCESS EXCLUSIVE MODE")
        engine = PostgresQueryEngine(probe_dsn, tenant_id=tenant_id,
                                     statement_timeout_ms=statement_timeout_ms)

        def read_identity() -> None:
            started = time.monotonic()
            try:
                engine.read_data_identity()
                outcome["result"] = "unexpectedly_completed"
            except QueryTimeoutError as exc:
                outcome["result"] = ("timed_out" if exc.failure_code == "statement_timeout"
                                      else "wrong_failure_code")
            except Exception as exc:
                outcome["result"] = f"unexpected_{type(exc).__name__}"
            finally:
                outcome["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
                finished.set()

        worker = threading.Thread(target=read_identity, name="ip1-data-meta-timeout-probe", daemon=True)
        worker.start()
        if not finished.wait(timeout=3):
            # Bound a regression where the engine stops applying statement_timeout.
            blocker.execute("SELECT pg_cancel_backend(pid) FROM pg_stat_activity "
                            "WHERE application_name = %s AND datname = current_database()",
                            (probe_name,))
            finished.wait(timeout=3)
        blocker.rollback()
        finished.wait(timeout=3)
        elapsed_ms = float(outcome.get("elapsed_ms", 99999))
        if worker.is_alive():
            timeout_result = "client_guard_timeout"
        elif outcome.get("result") == "timed_out":
            timeout_result = "timed_out" if elapsed_ms < 1500 else "client_guard_timeout"
        else:
            timeout_result = str(outcome.get("result", "worker_did_not_finish"))
        engine.close()

    recovery_engine = PostgresQueryEngine(app_dsn, tenant_id=tenant_id,
                                          statement_timeout_ms=statement_timeout_ms)
    try:
        version, fingerprint = recovery_engine.read_data_identity()
        recovery_result = ("recovered" if version and fingerprint and len(fingerprint) == 64
                           else "identity_mismatch")
    except Exception as exc:
        recovery_result = f"failed_{type(exc).__name__}"
    finally:
        recovery_engine.close()

    passed = timeout_result == "timed_out" and recovery_result == "recovered"
    return {
        "status": "passed" if passed else "failed",
        "check": "data_meta_access_exclusive_lock_statement_timeout",
        "statement_timeout_ms": statement_timeout_ms,
        "timeout_result": timeout_result,
        "timeout_elapsed_ms": outcome.get("elapsed_ms"),
        "recovery_result": recovery_result,
        "model_calls": 0,
        "synthetic_data_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app_dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    admin_dsn = os.environ.get("INSIGHTPILOT_IP4_ADMIN_DSN")
    if not app_dsn or not admin_dsn:
        parser.error("set the temporary INSIGHTPILOT_IP4_APP_DSN and INSIGHTPILOT_IP4_ADMIN_DSN")
    result = verify(app_dsn, admin_dsn)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
