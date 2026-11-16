from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from scripts.probe_ip3_http import matrix_passes, summarize_pressure

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "assemble_ip3_reliability_matrix", ROOT / "scripts/assemble_ip3_reliability_matrix.py")
ASSEMBLER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ASSEMBLER)


def http_report() -> dict:
    cases = []
    for concurrency in (1, 5, 20):
        results = []
        for index in range(concurrency):
            status = 429 if concurrency == 20 and index >= 8 else 200
            run_id = f"run-{concurrency}-{index}"
            body = {"run_id": run_id}
            if status == 429:
                body["reason"] = "concurrency_limit"
            results.append((status, float(index + 1), body, {"x-request-id": run_id}))
        cases.append(summarize_pressure(concurrency, results))
    idem = {"first_status": 200, "replay_status": 200, "same_run_id": True,
            "run_id": "run-idem", "replay_header": "true", "conflict_status": 409,
            "request_id_matches": True}
    cancel = {"running_replay_status": 202, "run_id": "run-cancel", "replay_header": "true",
              "cancel_status": 202, "cancel_run_id_matches": True, "final_http_status": 409,
              "final_status": "cancelled", "final_run_id_matches": True,
              "persisted_status": "cancelled", "cancel_requested": True}
    return {"model_calls": 0, "cases": cases, "idempotency": idem, "cancellation": cancel}


def postgres_report() -> dict:
    return {"steps": {
        "connection_termination_and_recovery": {"status": "passed", "backend_terminated": True,
                                                   "recovery_query_completed": True},
        "baseline": {"http_status": 200, "run_id": "run-base"},
        "database_unavailable": {"http_status": 502, "run_id": "run-down"},
        "after_database_recovery": {"http_status": 200, "run_id": "run-up"},
    }}


def restart_report() -> dict:
    return {"passed": True, "compose_cleanup": True, "container_id_stable": True,
            "pending_replay": {"run_id": "run-restart"},
            "recovered_run": {"status": "interrupted"},
            "idempotent_retry": {"http_status": 409, "run_id": "run-restart",
                                 "replay_header": "true"}}


def cleanup_report() -> dict:
    projects = ("insightpilot-ip3-compose", "insightpilot-ip3-db-outage", "insightpilot-ip3-restart")
    return {"status": "passed", "checked_projects": [
        {"project": project, "container_check": "passed", "container_count": 0,
         "network_check": "passed", "network_count": 0, "empty": True}
        for project in projects
    ]}


class IP3ReliabilityMatrixTests(unittest.TestCase):
    def test_pressure_summary_carries_only_status_latency_and_run_ids(self):
        summary = summarize_pressure(2, [
            (200, 10.0, {"run_id": "a", "answer": "hidden"}, {"x-request-id": "a"}),
            (429, 20.0, {"run_id": "b", "reason": "concurrency_limit", "sql": "hidden"},
             {"x-request-id": "b"}),
        ])
        self.assertEqual(summary["latency_ms"], {"p50": 10.0, "p95": 10.0, "max": 20.0})
        self.assertEqual((summary["success_count"], summary["http_429_count"], summary["http_503_count"]),
                         (1, 1, 0))
        self.assertEqual(summary["http_429_reason_counts"], {"concurrency_limit": 1})
        self.assertEqual(summary["request_id_match_count"], 2)
        self.assertNotIn("hidden", str(summary))

    def test_matrix_summary_passes_and_redacts_component_details(self):
        summary = ASSEMBLER.build_summary(http_report(), postgres_report(), restart_report(), cleanup_report())
        self.assertEqual(summary["status"], "passed")
        self.assertEqual([item["concurrency"] for item in summary["http_concurrency"]], [1, 5, 20])
        self.assertEqual(summary["compose_cleanup"]["status"], "passed")
        self.assertNotIn("hidden", str(summary))

    def test_http_matrix_rejects_503_and_missing_run_ids(self):
        report = http_report()
        self.assertTrue(matrix_passes(report["idempotency"], report["cases"], report["cancellation"]))
        report["cases"][2]["status_counts"]["503"] = 1
        report["cases"][2]["requests"] += 1
        self.assertFalse(matrix_passes(report["idempotency"], report["cases"], report["cancellation"]))

    def test_matrix_summary_fails_if_postgres_connection_recovery_is_missing(self):
        pg = postgres_report()
        pg["steps"]["connection_termination_and_recovery"]["recovery_query_completed"] = False
        self.assertEqual(ASSEMBLER.build_summary(http_report(), pg, restart_report(), cleanup_report())["status"], "failed")

    def test_matrix_summary_requires_empty_container_and_network_evidence(self):
        cleanup = cleanup_report()
        cleanup["checked_projects"][1]["network_count"] = 1
        summary = ASSEMBLER.build_summary(http_report(), postgres_report(), restart_report(), cleanup)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["compose_cleanup"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
