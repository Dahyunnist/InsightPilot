from __future__ import annotations

import sqlite3
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import AnalysisRequest, _register_cancel_event, create_app
from insightpilot.planner import PlanError, QueryPlan
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError


QUESTION = "2026-10-05 到 2026-10-11 华东批准退款金额率是多少？"
PLAN = QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


def route_endpoint(app, path):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def response_json(response):
    return json.loads(response.body)


class FailOnceSQLiteEngine(SQLiteQueryEngine):
    def __init__(self, path: Path):
        super().__init__(path)
        self.fail_next = True
        self.calls = 0

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        self.calls += 1
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


class BarrierFindRunStore(RunStore):
    def __init__(self, path: Path):
        self.find_barrier = threading.Barrier(2)
        super().__init__(path)

    def find_idempotent(self, key: str, question: str) -> str | None:
        existing_id = super().find_idempotent(key, question)
        self.find_barrier.wait(timeout=3)
        return existing_id


class CountingSQLiteEngine(SQLiteQueryEngine):
    def __init__(self, path: Path):
        super().__init__(path)
        self.query_count = 0

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        self.query_count += 1
        return super().execute_readonly(query, tenant_id=tenant_id, cancel_event=cancel_event)


class FailingStartRunStore(RunStore):
    def start(self, *args, **kwargs):
        raise sqlite3.OperationalError("injected run-store unavailable")


class IP3DatabaseRecoveryTests(unittest.TestCase):
    def test_invalid_idempotency_key_returns_request_id_without_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            app = create_app(InsightPilot(planner, database, tracer=None),
                             run_store=RunStore(root / "runs.sqlite"),
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"})
            analysis = route_endpoint(app, "/analysis")

            try:
                response = analysis(AnalysisRequest(question=QUESTION),
                                    authorization="Bearer synthetic-local-test-token", idempotency_key="bad key")
                body = response_json(response)

                self.assertEqual(response.status_code, 422)
                self.assertEqual(body["reason"], "invalid_idempotency_key")
                self.assertEqual(response.headers["X-Request-ID"], body["run_id"])
                self.assertEqual(planner.calls, 0)
            finally:
                app.state.analysis_executor.shutdown(wait=True)

    def test_rate_limit_preserves_retry_after_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database, tracer=None), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"},
                             max_requests_per_minute=1)
            analysis = route_endpoint(app, "/analysis")
            authorization = "Bearer synthetic-local-test-token"

            try:
                first = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                 idempotency_key="rate-replay")
                replay = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                  idempotency_key="rate-replay")
                conflict = analysis(AnalysisRequest(question=QUESTION + " 按渠道"),
                                    authorization=authorization, idempotency_key="rate-replay")
                limited = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                   idempotency_key=None)

                self.assertEqual((first.status_code, replay.status_code, conflict.status_code,
                                  limited.status_code), (200, 200, 409, 429))
                self.assertEqual(first.headers["X-Request-ID"], response_json(first)["run_id"])
                self.assertEqual(replay.headers["Idempotency-Replay"], "true")
                self.assertEqual(replay.headers["X-Request-ID"], response_json(first)["run_id"])
                self.assertEqual(conflict.headers["X-Request-ID"], response_json(conflict)["run_id"])
                self.assertEqual(response_json(conflict)["reason"], "idempotency_key_question_conflict")
                self.assertGreaterEqual(int(limited.headers["Retry-After"]), 1)
                self.assertEqual(planner.calls, 1)
            finally:
                app.state.analysis_executor.shutdown(wait=True)

    def test_run_store_start_failure_returns_503_without_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            store = FailingStartRunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database, tracer=None), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"})
            analysis = route_endpoint(app, "/analysis")

            try:
                response = analysis(AnalysisRequest(question=QUESTION),
                                    authorization="Bearer synthetic-local-test-token",
                                    idempotency_key="run-store-unavailable")

                body = response_json(response)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(body["reason"], "run_store_unavailable")
                self.assertEqual(response.headers["X-Request-ID"], body["run_id"])
                self.assertEqual(planner.calls, 0)
            finally:
                app.state.analysis_executor.shutdown(wait=True)

    def test_request_timeout_persists_cancel_marker_and_worker_finishes_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = BlockingPlanner()
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"},
                             request_timeout_seconds=0.05)
            analysis_endpoint = next(route.endpoint for route in app.routes
                                     if getattr(route, "path", None) == "/analysis")

            try:
                response = analysis_endpoint(
                    AnalysisRequest(question=QUESTION),
                    authorization="Bearer synthetic-local-test-token",
                    idempotency_key="timeout-cancel-marker",
                )
                self.assertEqual(response.status_code, 504)
                run_id = response.headers["X-Request-ID"]
                self.assertTrue(planner.entered.is_set())
                state = store.get(run_id)
                self.assertEqual(state["status"], "running")
                self.assertTrue(state["cancel_requested"])

                planner.release.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and store.get(run_id)["status"] == "running":
                    time.sleep(0.01)
                state = store.get(run_id)
                self.assertEqual(state["status"], "cancelled")
                self.assertTrue(state["cancel_requested"])
                replay = analysis_endpoint(
                    AnalysisRequest(question=QUESTION),
                    authorization="Bearer synthetic-local-test-token",
                    idempotency_key="timeout-cancel-marker",
                )
                self.assertEqual(replay.status_code, 409)
                self.assertEqual(replay.headers["Idempotency-Replay"], "true")
                self.assertEqual(store.get(run_id)["result"]["run_id"], run_id)
                self.assertEqual(planner.calls, 1)
            finally:
                planner.release.set()
                app.state.analysis_executor.shutdown(wait=True)

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

    def test_concurrent_api_requests_with_same_key_share_one_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = BlockingPlanner()
            store = BarrierFindRunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database, tracer=None), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"},
                             max_concurrent=2, max_requests_per_minute=10,
                             request_timeout_seconds=4)
            analysis = route_endpoint(app, "/analysis")
            authorization = "Bearer synthetic-local-test-token"
            responses = []
            errors = []

            def submit():
                try:
                    response = analysis(AnalysisRequest(question=QUESTION),
                                        authorization=authorization,
                                        idempotency_key="parallel-api-key")
                    responses.append(response)
                except Exception as exc:
                    errors.append(exc)

            workers = [threading.Thread(target=submit) for _ in range(2)]
            try:
                for worker in workers:
                    worker.start()
                self.assertTrue(planner.entered.wait(timeout=3), "analysis worker did not start")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not responses:
                    time.sleep(0.01)
                self.assertEqual(len(responses), 1)
                replay = responses[0]
                self.assertEqual(replay.status_code, 202)
                self.assertEqual(replay.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)

                planner.release.set()
                for worker in workers:
                    worker.join(timeout=5)
                self.assertTrue(all(not worker.is_alive() for worker in workers))
                self.assertEqual(errors, [])
                self.assertEqual({response.status_code for response in responses}, {200, 202})
                run_ids = {response_json(response)["run_id"] for response in responses}
                self.assertEqual(len(run_ids), 1)
                run_id = run_ids.pop()
                self.assertTrue(all(response.headers["X-Request-ID"] == run_id for response in responses))
                self.assertEqual(store.get(run_id)["status"], "answered")
                self.assertEqual(planner.calls, 1)
                connection = sqlite3.connect(store.path)
                try:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
                finally:
                    connection.close()
            finally:
                planner.release.set()
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(timeout=2)
                app.state.analysis_executor.shutdown(wait=True)

    def test_sqlite_disconnect_is_terminal_idempotent_and_new_run_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = FixedPlanner()
            engine = FailOnceSQLiteEngine(database)
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database, query_engine=engine), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"},
                             max_requests_per_minute=20)
            analysis = route_endpoint(app, "/analysis")
            get_run = route_endpoint(app, "/runs/{run_id}")
            authorization = "Bearer synthetic-local-test-token"
            try:
                failed = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                  idempotency_key="db-outage-1")
                failed_body = response_json(failed)
                self.assertEqual(failed.status_code, 502)
                run_id = failed_body["run_id"]
                self.assertEqual(failed.headers["X-Request-ID"], run_id)
                self.assertEqual(failed_body["stage"], "query")
                self.assertEqual(failed_body["failure_detail"]["category"], "database_execution")
                self.assertNotIn("rows", failed_body)
                self.assertNotIn("answer", failed_body)
                self.assertEqual(planner.calls, 1)  # one plan call; no automatic retry budget consumed
                self.assertEqual(engine.calls, 1)
                self.assertEqual(get_run(run_id, authorization=authorization)["status"], "failed")

                replay = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                  idempotency_key="db-outage-1")
                replay_body = response_json(replay)
                self.assertEqual(replay.status_code, 502)
                self.assertEqual(replay_body["run_id"], run_id)
                self.assertEqual(replay.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)
                self.assertEqual(engine.calls, 1)

                conflict = analysis(AnalysisRequest(question=QUESTION + " 按渠道"),
                                    authorization=authorization, idempotency_key="db-outage-1")
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(planner.calls, 1)
                self.assertEqual(engine.calls, 1)

                recovered = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                     idempotency_key="db-recovered-1")
                recovered_body = response_json(recovered)
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual(recovered_body["status"], "answered")
                self.assertEqual(planner.calls, 2)
                self.assertEqual(engine.calls, 2)
            finally:
                app.state.analysis_executor.shutdown(wait=True)

    def test_cancelled_run_persists_cancel_state_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            planner = BlockingPlanner()
            store = RunStore(root / "runs.sqlite")
            app = create_app(InsightPilot(planner, database), run_store=store,
                             api_token="synthetic-local-test-token", api_scopes={"refund:read", "refund:cancel"},
                             max_requests_per_minute=20)
            analysis = route_endpoint(app, "/analysis")
            cancel_run = route_endpoint(app, "/runs/{run_id}/cancel")
            get_run = route_endpoint(app, "/runs/{run_id}")
            authorization = "Bearer synthetic-local-test-token"
            response_box = []
            request_thread = threading.Thread(target=lambda: response_box.append(
                analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                         idempotency_key="cancel-run-1")))
            request_thread.start()
            try:
                self.assertTrue(planner.entered.wait(timeout=3), "planner did not start")
                run_id = store.find_idempotent("default:cancel-run-1", QUESTION)
                self.assertIsNotNone(run_id)
                cancelled = cancel_run(run_id, authorization=authorization)
                self.assertEqual(cancelled.status_code, 202)
                planner.release.set()
                request_thread.join(timeout=5)
                self.assertFalse(request_thread.is_alive(), "analysis request did not finish")

                result = response_box[0]
                result_body = response_json(result)
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result_body["status"], "cancelled")
                state = get_run(run_id, authorization=authorization)
                self.assertEqual(state["status"], "cancelled")
                self.assertTrue(state["cancel_requested"])
                replay = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                  idempotency_key="cancel-run-1")
                self.assertEqual(replay.status_code, 409)
                self.assertEqual(response_json(replay)["run_id"], run_id)
                self.assertEqual(replay.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)
            finally:
                planner.release.set()
                request_thread.join(timeout=2)
                app.state.analysis_executor.shutdown(wait=True)

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
                             api_token="synthetic-local-test-token", api_scopes={"refund:read"},
                             max_requests_per_minute=20, request_timeout_seconds=0.15)
            analysis = route_endpoint(app, "/analysis")
            authorization = "Bearer synthetic-local-test-token"
            try:
                timeout_response = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                            idempotency_key="slow-model-timeout-1")
                timeout_body = response_json(timeout_response)
                self.assertTrue(planner.entered.is_set(), "fake model request did not start")
                self.assertEqual(timeout_response.status_code, 504)
                run_id = timeout_body["run_id"]
                self.assertEqual(store.get(run_id)["status"], "running")

                active_replay = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                         idempotency_key="slow-model-timeout-1")
                self.assertEqual(active_replay.status_code, 202)
                self.assertEqual(response_json(active_replay)["run_id"], run_id)
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

                retry = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                 idempotency_key="slow-model-timeout-1")
                self.assertEqual(retry.status_code, 502)
                self.assertEqual(response_json(retry)["run_id"], run_id)
                self.assertEqual(response_json(retry)["status"], "failed")
                self.assertEqual(retry.headers.get("Idempotency-Replay"), "true")
                self.assertEqual(planner.calls, 1)

                recovered = analysis(AnalysisRequest(question=QUESTION), authorization=authorization,
                                     idempotency_key="slow-model-timeout-recovered-1")
                recovered_body = response_json(recovered)
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual(recovered_body["status"], "answered")
                self.assertEqual(planner.calls, 2)
                recovered_state = route_endpoint(app, "/runs/{run_id}")(
                    recovered_body["run_id"], authorization=authorization)
                self.assertEqual(recovered_state["status"], "answered")
            finally:
                planner.release.set()
                app.state.analysis_executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
