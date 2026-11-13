from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import _register_cancel_event, create_app
from insightpilot.planner import PlanError, QueryPlan
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


class SlowTimeoutPlanner:
    def __init__(self):
        self.calls = 0
        self.last_call_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def plan(self, _question):
        self.calls += 1
        self.last_call_count = 1
        if self.calls == 1:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("test did not release the fake model request")
            raise PlanError("synthetic model request timed out")
        return PLAN


class PauseAfterStartRunStore(RunStore):
    def __init__(self, path: Path):
        self.persisted = threading.Event()
        self.release_start = threading.Event()
        super().__init__(path)

    def start(self, *args, **kwargs):
        run_id = super().start(*args, **kwargs)
        self.persisted.set()
        if not self.release_start.wait(timeout=5):
            raise AssertionError("test did not release run-store start")
        return run_id


class CountingSQLiteEngine(SQLiteQueryEngine):
    def __init__(self, path: Path):
        super().__init__(path)
        self.query_count = 0

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        self.query_count += 1
        return super().execute_readonly(query, tenant_id=tenant_id, cancel_event=cancel_event)


class IP3DatabaseRecoveryTests(unittest.TestCase):
    def test_concurrent_same_key_reservation_creates_one_executable_run(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RunStore(Path(temp) / "runs.sqlite")
            thread_count = 8
            scoped_key = "default:parallel-reservation"
            barrier = threading.Barrier(thread_count)
            proposed_ids = [str(uuid.uuid4()) for _ in range(thread_count)]
            reservations = []
            errors = []

            def reserve(run_id):
                try:
                    barrier.wait(timeout=3)
                    stored_id = store.start(run_id, QUESTION, scoped_key)
                    reservations.append((run_id, stored_id))
                except Exception as exc:
                    errors.append(exc)

            workers = [threading.Thread(target=reserve, args=(run_id,)) for run_id in proposed_ids]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)

            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            stored_ids = {stored_id for _, stored_id in reservations}
            self.assertEqual(len(reservations), thread_count)
            self.assertEqual(len(stored_ids), 1)
            winner_id = stored_ids.pop()
            self.assertEqual(sum(proposed == stored_id for proposed, stored_id in reservations), 1)

            store.finish(winner_id, {"run_id": winner_id, "status": "failed", "stage": "query"})
            replay_id = store.start(str(uuid.uuid4()), QUESTION, scoped_key)
            self.assertEqual(replay_id, winner_id)
            self.assertEqual(store.get(winner_id)["status"], "failed")
            self.assertEqual(store.get(winner_id)["result"]["status"], "failed")
            connection = sqlite3.connect(store.path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            finally:
                connection.close()

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

    def test_cancel_between_persisted_start_and_event_registration_is_reconciled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            engine = CountingSQLiteEngine(database)
            store = PauseAfterStartRunStore(root / "runs.sqlite")
            run_id = str(uuid.uuid4())
            scoped_key = "default:cancel-start-window"
            event = threading.Event()
            events: dict[str, threading.Event] = {}
            lock = threading.Lock()
            result_box = []
            errors = []
            core = InsightPilot(planner, database, query_engine=engine, tracer=None)

            def start_and_execute():
                try:
                    self.assertEqual(store.start(run_id, QUESTION, scoped_key), run_id)
                    _register_cancel_event(store, events, lock, run_id, event, None)
                    result = core.analyze(QUESTION, run_id=run_id, cancel_event=event)
                    store.finish(run_id, result)
                    result_box.append(result)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=start_and_execute)
            worker.start()
            try:
                self.assertTrue(store.persisted.wait(timeout=3), "run row was not persisted")
                self.assertTrue(store.request_cancel(run_id))
                self.assertTrue(store.get(run_id)["cancel_requested"])

                store.release_start.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "run worker did not finish")
                self.assertEqual(errors, [])
                self.assertTrue(event.is_set())
                self.assertEqual(result_box[0]["status"], "cancelled")
                self.assertEqual(store.get(run_id)["status"], "cancelled")
                self.assertEqual(store.get(run_id)["result"]["status"], "cancelled")
                self.assertEqual(engine.query_count, 0)

                replayed_id = store.find_idempotent(scoped_key, QUESTION)
                self.assertEqual(replayed_id, run_id)
                self.assertEqual(store.start(str(uuid.uuid4()), QUESTION, scoped_key), run_id)
                self.assertEqual(store.get(run_id)["result"]["status"], "cancelled")
                self.assertEqual(planner.calls, 1)
                self.assertEqual(engine.query_count, 0)
            finally:
                store.release_start.set()
                worker.join(timeout=2)

    def test_model_timeout_persists_failure_and_idempotent_retry_does_not_replan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = SlowTimeoutPlanner()
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database), run_store=store,
                             api_token="local-test-token", api_scopes={"refund:read"},
                             max_requests_per_minute=20, request_timeout_seconds=0.15)
            headers = {"Authorization": "Bearer local-test-token", "Idempotency-Key": "slow-model-timeout-1"}
            response_box = []
            with TestClient(app) as client:
                request_thread = threading.Thread(target=lambda: response_box.append(
                    client.post("/analysis", json={"question": QUESTION}, headers=headers)))
                request_thread.start()
                try:
                    self.assertTrue(planner.entered.wait(timeout=2), "fake model request did not start")
                    request_thread.join(timeout=2)
                    self.assertFalse(request_thread.is_alive(), "HTTP request did not time out")
                    timeout_response = response_box[0]
                    self.assertEqual(timeout_response.status_code, 504)
                    run_id = timeout_response.json()["run_id"]
                    self.assertEqual(store.get(run_id)["status"], "running")

                    active_replay = client.post("/analysis", json={"question": QUESTION}, headers=headers)
                    self.assertEqual(active_replay.status_code, 202)
                    self.assertEqual(active_replay.json()["run_id"], run_id)
                    self.assertEqual(active_replay.headers.get("Idempotency-Replay"), "true")
                    self.assertEqual(planner.calls, 1)

                    planner.release.set()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and store.get(run_id)["status"] == "running":
                        time.sleep(0.01)
                    state = store.get(run_id)
                    self.assertEqual(state["status"], "failed")
                    self.assertEqual(state["result"]["status"], "failed")
                    self.assertEqual(state["result"]["stage"], "planning")
                    self.assertEqual(state["result"]["message"], "synthetic model request timed out")
                    self.assertNotIn("plan", state["result"])
                    self.assertNotIn("rows", state["result"])

                    retry = client.post("/analysis", json={"question": QUESTION}, headers=headers)
                    self.assertEqual(retry.status_code, 502)
                    self.assertEqual(retry.json()["run_id"], run_id)
                    self.assertEqual(retry.json()["status"], "failed")
                    self.assertEqual(retry.headers.get("Idempotency-Replay"), "true")
                    self.assertEqual(planner.calls, 1)

                    recovered = client.post("/analysis", json={"question": QUESTION}, headers={
                        "Authorization": "Bearer local-test-token",
                        "Idempotency-Key": "slow-model-timeout-recovered-1",
                    })
                    self.assertEqual(recovered.status_code, 200)
                    self.assertEqual(recovered.json()["status"], "answered")
                    self.assertEqual(planner.calls, 2)
                    recovered_state = client.get(
                        f"/runs/{recovered.json()['run_id']}",
                        headers={"Authorization": "Bearer local-test-token"},
                    ).json()
                    self.assertEqual(recovered_state["status"], "answered")
                finally:
                    planner.release.set()
                    request_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
