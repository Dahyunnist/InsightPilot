"""Assemble redacted HTTP, PostgreSQL, and restart acceptance summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_summary(http: dict, postgres: dict, restart: dict, cleanup: dict | None = None) -> dict:
    cases = []
    for source in http.get("cases", []):
        latency = source.get("latency_ms") or {}
        cases.append({
            "concurrency": source.get("concurrency"),
            "requests": source.get("requests"),
            "p50_ms": latency.get("p50"),
            "p95_ms": latency.get("p95"),
            "success_count": source.get("success_count"),
            "http_429_count": source.get("http_429_count"),
            "http_429_reason_counts": source.get("http_429_reason_counts", {}),
            "http_503_count": source.get("http_503_count"),
            "run_id_count": source.get("run_id_count"),
            "unique_run_id_count": source.get("unique_run_id_count"),
            "request_id_match_count": source.get("request_id_match_count"),
            "run_id_evidence": source.get("run_id_evidence", []),
        })
    http_idempotency = http.get("idempotency") or {}
    cancellation = http.get("cancellation") or {}
    steps = postgres.get("steps") or {}
    connection = steps.get("connection_termination_and_recovery") or {}
    baseline = steps.get("baseline") or {}
    outage = steps.get("database_unavailable") or {}
    pg_recovery = steps.get("after_database_recovery") or {}
    pending = restart.get("pending_replay") or {}
    interrupted = restart.get("recovered_run") or {}
    retry = restart.get("idempotent_retry") or {}
    cleanup_projects = cleanup.get("checked_projects") or [] if isinstance(cleanup, dict) else []
    expected_projects = {
        "insightpilot-ip3-compose", "insightpilot-ip3-db-outage", "insightpilot-ip3-restart"
    }
    cleanup_passed = bool(
        isinstance(cleanup, dict)
        and cleanup.get("status") == "passed"
        and {item.get("project") for item in cleanup_projects} == expected_projects
        and all(item.get("empty") is True
                and item.get("container_check") == "passed"
                and item.get("container_count") == 0
                and item.get("network_check") == "passed"
                and item.get("network_count") == 0
                for item in cleanup_projects)
    )

    all_200_429_503 = all(
        item.get("requests") == item.get("success_count", 0) +
        item.get("http_429_count", 0) + item.get("http_503_count", 0)
        for item in cases
    )
    passed = bool(
        http.get("model_calls") == 0
        and [item.get("concurrency") for item in cases] == [1, 5, 20]
        and all_200_429_503
        and all(item.get("request_id_match_count") == item.get("requests") for item in cases)
        and cases[-1].get("http_429_count", 0) > 0
        and cases[-1].get("http_503_count", 0) == 0
        and http_idempotency.get("first_status") == http_idempotency.get("replay_status") == 200
        and http_idempotency.get("same_run_id") is True
        and http_idempotency.get("run_id")
        and http_idempotency.get("request_id_matches") is True
        and http_idempotency.get("replay_header") == "true"
        and http_idempotency.get("conflict_status") == 409
        and cancellation.get("run_id")
        and cancellation.get("cancel_status") == 202
        and cancellation.get("cancel_run_id_matches") is True
        and cancellation.get("final_status") == "cancelled"
        and cancellation.get("persisted_status") == "cancelled"
        and connection.get("status") == "passed"
        and connection.get("backend_terminated") is True
        and connection.get("recovery_query_completed") is True
        and baseline.get("http_status") == 200
        and outage.get("http_status") == 502
        and pg_recovery.get("http_status") == 200
        and restart.get("passed") is True
        and restart.get("compose_cleanup") is True
        and restart.get("container_id_stable") is True
        and pending.get("run_id")
        and retry.get("run_id") == pending.get("run_id")
        and retry.get("replay_header") == "true"
        and cleanup_passed
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "synthetic_only": True,
        "model_calls": 0,
        "http_concurrency": cases,
        "idempotency": {
            "first_status": http_idempotency.get("first_status"),
            "replay_status": http_idempotency.get("replay_status"),
            "same_run_id": http_idempotency.get("same_run_id"),
            "run_id": http_idempotency.get("run_id"),
            "conflict_status": http_idempotency.get("conflict_status"),
        },
        "cancellation": {
            "run_id": cancellation.get("run_id"),
            "cancel_status": cancellation.get("cancel_status"),
            "final_status": cancellation.get("final_status"),
            "persisted_status": cancellation.get("persisted_status"),
        },
        "postgres_connection": {
            "backend_terminated": connection.get("backend_terminated"),
            "disconnect_error_class": connection.get("disconnect_error_class"),
            "recovery_query_completed": connection.get("recovery_query_completed"),
        },
        "postgres_service": {
            "baseline_run_id": baseline.get("run_id"),
            "outage_run_id": outage.get("run_id"),
            "outage_http_status": outage.get("http_status"),
            "recovery_run_id": pg_recovery.get("run_id"),
            "recovery_http_status": pg_recovery.get("http_status"),
        },
        "service_restart": {
            "run_id": pending.get("run_id"),
            "container_id_stable": restart.get("container_id_stable"),
            "recovered_status": interrupted.get("status"),
            "retry_status": retry.get("http_status"),
            "retry_run_id": retry.get("run_id"),
        },
        "compose_cleanup": {
            "status": "passed" if cleanup_passed else "failed",
            "checked_projects": cleanup_projects,
        },
        "detail_policy": "statuses, latency, and run IDs only; no question, SQL, parameters, answer rows, or credentials",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-report", type=Path, required=True)
    parser.add_argument("--postgres-report", type=Path, required=True)
    parser.add_argument("--restart-report", type=Path, required=True)
    parser.add_argument("--cleanup-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in
               (args.http_report, args.postgres_report, args.restart_report)]
    cleanup = json.loads(args.cleanup_report.read_text(encoding="utf-8"))
    summary = build_summary(*reports, cleanup)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
