from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError


QUESTION = "2026-10-05 到 2026-10-11 华东批准退款金额率是多少？"
PLAN = QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


class FailOnceSQLiteEngine(SQLiteQueryEngine):
    def __init__(self, path: Path):
        super().__init__(path)
        self.fail_next = True

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        if self.fail_next:
            self.fail_next = False
            raise QueryExecutionError("injected SQLite disconnect", failure_code="database_execution")
        return super().execute_readonly(query, tenant_id=tenant_id, cancel_event=cancel_event)


class FixedPlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, _question):
        self.calls += 1
        return PLAN


class BlockingPlanner(FixedPlanner):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def plan(self, _question):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test planner release timed out")
        return PLAN


class IP3DatabaseRecoveryTests(unittest.TestCase):
    def test_sqlite_disconnect_is_terminal_idempotent_and_new_run_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            engine = FailOnceSQLiteEngine(database)
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database, query_engine=engine), run_store=store,
                             api_token="local-test-token", api_scopes={"refund:read"},
                             max_requests_per_minute=20)
            headers = {"Authorization": "Bearer local-test-token", "Idempotency-Key": "db-outage-1"}
            with TestClient(app) as client:
                failed = client.post("/analysis", json={"question": QUESTION}, headers=headers)
                self.assertEqual(failed.status_code, 502)
                run_id = failed.json()["run_id"]
                self.assertEqual(failed.json()["stage"], "query")
                self.assertEqual(planner.calls, 1)  # one plan call; no automatic retry budget consumed
                self.assertEqual(client.get(f"/runs/{run_id}", headers=headers).json()["status"], "failed")

                replay = client.post("/analysis", json={"question": QUESTION}, headers=headers)
                self.assertEqual(replay.status_code, 502)
                self.assertEqual(replay.json()["run_id"], run_id)
                self.assertEqual(replay.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)

                conflict = client.post("/analysis", json={"question": QUESTION + " 按渠道"}, headers=headers)
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(planner.calls, 1)

                recovered = client.post("/analysis", json={"question": QUESTION}, headers={
                    **headers, "Idempotency-Key": "db-recovered-1"})
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual(recovered.json()["status"], "answered")
                self.assertEqual(planner.calls, 2)

    def test_cancelled_run_persists_cancel_state_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = BlockingPlanner()
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database), run_store=store,
                             api_token="local-test-token", api_scopes={"refund:read", "refund:cancel"},
                             max_requests_per_minute=20)
            headers = {"Authorization": "Bearer local-test-token", "Idempotency-Key": "cancel-run-1"}
            response_box = []
            with TestClient(app) as client:
                request_thread = threading.Thread(target=lambda: response_box.append(
                    client.post("/analysis", json={"question": QUESTION}, headers=headers)))
                request_thread.start()
                self.assertTrue(planner.entered.wait(timeout=3), "planner did not start")
                run_id = store.find_idempotent("default:cancel-run-1", QUESTION)
                self.assertIsNotNone(run_id)
                cancelled = client.post(f"/runs/{run_id}/cancel", headers=headers)
                self.assertEqual(cancelled.status_code, 202)
                planner.release.set()
                request_thread.join(timeout=5)
                self.assertFalse(request_thread.is_alive(), "analysis request did not finish")

                result = response_box[0]
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json()["status"], "cancelled")
                state = client.get(f"/runs/{run_id}", headers=headers).json()
                self.assertEqual(state["status"], "cancelled")
                self.assertTrue(state["cancel_requested"])
                replay = client.post("/analysis", json={"question": QUESTION}, headers=headers)
                self.assertEqual(replay.status_code, 409)
                self.assertEqual(replay.json()["run_id"], run_id)
                self.assertEqual(replay.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)


if __name__ == "__main__":
    unittest.main()
