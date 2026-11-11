"""Stop the isolated PostgreSQL service under a live API, then verify recovery."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.multidomain_seed import create_multidomain_database
from insightpilot.runner import read_data_identity
from scripts.prepare_ip4_postgres import prepare

COMPOSE = ROOT / "docker-compose.ip3-db-outage.yml"
PROJECT = "insightpilot-ip3-db-outage"
DATABASE = ROOT / "data/insightpilot-multidomain.sqlite"
MANIFEST = ROOT / "evals/ip4-v3-manifest.json"
OUTPUT = ROOT / "reports/ip3-postgres-service-outage.json"
API_BASE = "http://127.0.0.1:58082"
ADMIN_PASSWORD = "ip3-db-outage-test-only"
APP_ROLE = "insightpilot_ip3_app"
APP_PASSWORD = "ip3-app-test-only"
QUESTION = "2026-10-05 到 2026-10-11 华东批准退款金额率是多少？"
TOKEN = "ip3-db-outage-token"


def compose(*args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", "compose", "--project-name", PROJECT, "-f", str(COMPOSE), *args],
                            cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed ({result.returncode})")
    return result


def _api(method: str, path: str, *, body: dict | None = None, key: str | None = None,
         timeout: float = 25) -> tuple[int, dict, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Idempotency-Key"] = key
    request = urllib.request.Request(API_BASE + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), {
                name.lower(): value for name, value in response.headers.items()}
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read()), {
            name.lower(): value for name, value in response.headers.items()}


def _wait_postgres(timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = compose("exec", "-T", "postgres", "pg_isready", "-U", "postgres",
                         "-d", "insightpilot_ip4", check=False, timeout=5)
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise TimeoutError("synthetic PostgreSQL did not become ready")


def _wait_api(timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _body, _headers = _api("GET", "/health", timeout=2)
            if status == 200:
                return
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError("synthetic API did not become healthy")


def _planner_call_count() -> int:
    status, body, _headers = _api("GET", "/probe/planner-calls")
    if status != 200:
        raise RuntimeError("planner probe endpoint failed")
    return int(body["total_calls"])


def _record(status: int, body: dict, headers: dict) -> dict:
    result = body.get("result") if isinstance(body.get("result"), dict) else body
    return {"http_status": status, "run_id": body.get("run_id"), "status": body.get("status"),
            "stage": body.get("stage"), "reason": body.get("reason"),
            "failure_layer": (result.get("failure_detail") or {}).get("layer"),
            "failure_category": (result.get("failure_detail") or {}).get("category"),
            "model_call_count": result.get("model_call_count"),
            "idempotency_replay": headers.get("idempotency-replay") == "true"}


def _port_available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_ports_available(timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_available(55436) and _port_available(58082):
            return
        time.sleep(0.25)
    raise RuntimeError("ports 55436 or 58082 remained unavailable after waiting")


def main() -> None:
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "scenario": "stop PostgreSQL service while API remains live; restore same container",
              "synthetic_data_only": True, "external_model_calls": 0,
              "planner": "fixed local planner with invocation counter",
              "expected": {"baseline": "200 answered; planner calls 1",
                           "database_down": "502 failed at dataset; model calls 0; run persisted failed",
                           "same_key_replay": "502 same run_id with replay marker; planner count unchanged",
                           "recovery": "200 answered after PostgreSQL restart; planner calls 2"},
              "steps": {}, "compose_cleanup": False}
    started = time.perf_counter()
    try:
        _wait_ports_available()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if not DATABASE.is_file():
            create_multidomain_database(DATABASE)
        if read_data_identity(DATABASE) != (manifest["data_version"], manifest["data_fingerprint"]):
            raise RuntimeError("local synthetic multidomain SQLite fixture identity mismatch")

        compose("up", "-d", "postgres")
        _wait_postgres()
        admin_dsn = f"postgresql://postgres:{ADMIN_PASSWORD}@127.0.0.1:55436/insightpilot_ip4"
        prepare(DATABASE, admin_dsn, APP_ROLE, APP_PASSWORD,
                ROOT / "docs/postgresql-rls-bootstrap.sql")
        compose("up", "-d", "api")
        _wait_api()

        status, baseline, headers = _api("POST", "/analysis", body={"question": QUESTION}, key="baseline")
        calls_after_baseline = _planner_call_count()
        report["steps"]["baseline"] = {**_record(status, baseline, headers),
                                         "planner_total_calls": calls_after_baseline}
        if status != 200 or baseline.get("status") != "answered" or calls_after_baseline != 1:
            raise RuntimeError("baseline API query did not pass with one fixed planner invocation")

        compose("stop", "postgres")
        status, health, _headers = _api("GET", "/health")
        report["steps"]["api_liveness_during_db_outage"] = {"http_status": status,
                                                                "status": health.get("status")}
        if status != 200:
            raise RuntimeError("API liveness endpoint did not survive database outage")
        status, unavailable, headers = _api("POST", "/analysis", body={"question": QUESTION},
                                             key="db-outage")
        outage_run_id = unavailable.get("run_id")
        calls_during_outage = _planner_call_count()
        status_get, saved_run, _headers = _api("GET", f"/runs/{outage_run_id}")
        state = saved_run.get("status")
        saved_result = saved_run.get("result") or {}
        trajectory = saved_result.get("trajectory") or []
        planning_not_reached = len(trajectory) > 1 and trajectory[1].get("outcome") == "not_reached"
        report["steps"]["database_unavailable"] = {
            **_record(status, unavailable, headers), "planner_total_calls": calls_during_outage,
            "run_store_http_status": status_get, "persisted_run_status": state,
            "persisted_result_stage": saved_result.get("stage"),
            "persisted_model_call_count": saved_result.get("model_call_count"),
            "planning_not_reached": planning_not_reached}
        if not (status == 502 and unavailable.get("status") == "failed" and
                unavailable.get("stage") == "dataset" and unavailable.get("planning_skipped") is True and
                calls_during_outage == 1 and status_get == 200 and state == "failed" and
                saved_result.get("model_call_count") == 0 and planning_not_reached):
            raise RuntimeError("database outage did not stop analysis before planner/model invocation")

        replay_status, replay, replay_headers = _api("POST", "/analysis", body={"question": QUESTION},
                                                       key="db-outage")
        calls_after_replay = _planner_call_count()
        report["steps"]["idempotent_replay_while_down"] = {
            **_record(replay_status, replay, replay_headers), "planner_total_calls": calls_after_replay}
        if not (replay_status == 502 and replay.get("run_id") == outage_run_id and
                replay_headers.get("idempotency-replay") == "true" and calls_after_replay == 1):
            raise RuntimeError("outage idempotency replay changed result or invoked planner")

        compose("start", "postgres")
        _wait_postgres()
        direct_status, direct_probe, _headers = _api("GET", "/probe/direct-db")
        pool_status, pool_before, _headers = _api("GET", "/probe/pool-stats")
        status, recovered, headers = _api("POST", "/analysis", body={"question": QUESTION},
                                          key="db-recovered")
        calls_after_recovery = _planner_call_count()
        _stats_status, pool_after, _headers = _api("GET", "/probe/pool-stats")
        report["steps"]["after_database_recovery"] = {
            **_record(status, recovered, headers), "planner_total_calls": calls_after_recovery,
            "direct_db_probe_http_status": direct_status, "direct_db_probe": direct_probe,
            "pool_stats_http_status": pool_status, "pool_stats_before_analysis": pool_before,
            "pool_stats_after_analysis": pool_after}
        if not (status == 200 and recovered.get("status") == "answered" and calls_after_recovery == 2):
            raise RuntimeError("API/connection pool did not recover after PostgreSQL service restart")
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_class"] = type(exc).__name__
        detail = str(exc)
        detail = re.sub(r"postgres(?:ql)?://[^\s\"']+", "[test DSN omitted]", detail)
        report["error"] = detail[:500] or "no exception text"
    finally:
        if report["status"] != "passed":
            try:
                logs = compose("logs", "--no-color", "--tail", "60", check=False, timeout=15)
                detail = (logs.stdout + logs.stderr).replace(ADMIN_PASSWORD, "[test admin password]")
                detail = detail.replace(APP_PASSWORD, "[test app password]").replace(TOKEN, "[test token]")
                report["compose_log_tail"] = detail[-12_000:]
            except Exception as exc:
                report["compose_log_error_class"] = type(exc).__name__
        try:
            cleanup = compose("down", "--remove-orphans", check=False, timeout=60)
            report["compose_cleanup"] = cleanup.returncode == 0
            if cleanup.returncode:
                report["cleanup_error"] = f"docker compose down exited with {cleanup.returncode}"
        except Exception as exc:
            report["compose_cleanup"] = False
            report["cleanup_error_class"] = type(exc).__name__
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed" or not report["compose_cleanup"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
