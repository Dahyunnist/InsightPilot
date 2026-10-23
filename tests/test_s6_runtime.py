import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.auth import BearerScopeMiddleware
from insightpilot.metrics import required_access_scopes
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryCancelledError, execute_readonly
from insightpilot.sql import CompiledQuery
from threading import Event


class StubPlanner:
    def plan(self, _question):
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


class MarketingPlanner:
    def __init__(self):
        self.called = False

    def plan(self, _question):
        self.called = True
        return QueryPlan("marketing_net_roi", "2026-10-01", "2026-11-01", None, "campaign")


class S6RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = create_database(self.root / "fixture.sqlite")
        self.core = InsightPilot(StubPlanner(), self.database)
        self.store = RunStore(self.root / "runs.sqlite")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_run_survives_store_reopen_and_redacts_personal_identifiers(self):
        self.store.start("run-1", "退款分析 13800138000 user@example.com")
        self.store.finish("run-1", {"run_id": "run-1", "status": "answered", "sql": "SELECT 1", "parameters": []})
        record = RunStore(self.root / "runs.sqlite").get("run-1")
        self.assertEqual(record["result"]["sql"], "SELECT 1")
        self.assertNotIn("13800138000", record["question_redacted"])
        self.assertNotIn("user@example.com", record["question_redacted"])
        self.assertEqual(len(record["question_hash"]), 64)

    def test_startup_marks_orphaned_work_as_interrupted(self):
        self.store.start("run-2", "退款分析")
        record = RunStore(self.root / "runs.sqlite").get("run-2")
        self.assertEqual(record["status"], "interrupted")

    def test_api_enforces_bearer_and_scopes_and_persists_result(self):
        app = create_app(self.core, run_store=self.store, api_token="secret-token", api_scopes={"refund:read"})
        client = TestClient(app)
        denied = client.post("/analysis", json={"question": "退款分析"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers["x-request-id"], denied.json()["request_id"])
        ok = client.post("/analysis", json={"question": "退款分析"}, headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(ok.status_code, 200)
        run_id = ok.json()["run_id"]
        self.assertEqual(client.get(f"/runs/{run_id}", headers={"Authorization": "Bearer secret-token"}).json()["result"], ok.json())
        no_cancel_scope = client.post(f"/runs/{run_id}/cancel", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(no_cancel_scope.status_code, 403)

    def test_api_idempotency_replays_result_and_rejects_changed_question(self):
        headers = {"Authorization": "Bearer secret-token", "Idempotency-Key": "client-run-1"}
        client = TestClient(create_app(self.core, run_store=self.store, api_token="secret-token",
                                       api_scopes={"refund:read"}))
        first = client.post("/analysis", json={"question": "退款分析"}, headers=headers)
        replay = client.post("/analysis", json={"question": "退款分析"}, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()["run_id"], replay.json()["run_id"])
        self.assertEqual(replay.headers["idempotency-replay"], "true")
        conflict = client.post("/analysis", json={"question": "退款分析 华南"}, headers=headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["reason"], "idempotency_key_question_conflict")

    def test_api_rate_limit_returns_retry_after(self):
        app = create_app(self.core, run_store=self.store, api_token="secret-token",
                         api_scopes={"refund:read"}, max_requests_per_minute=1)
        client = TestClient(app)
        headers = {"Authorization": "Bearer secret-token"}
        self.assertEqual(client.post("/analysis", json={"question": "退款分析"}, headers=headers).status_code, 200)
        limited = client.post("/analysis", json={"question": "退款分析"}, headers=headers)
        self.assertEqual(limited.status_code, 429)
        self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)

    def test_api_timeout_requests_cooperative_cancel_and_worker_recovers(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingPlanner:
            def plan(self, _question):
                entered.set()
                release.wait(2)
                return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)

        core = InsightPilot(BlockingPlanner(), self.database)
        app = create_app(core, run_store=self.store, api_token="secret-token",
                         api_scopes={"refund:read"}, max_concurrent=1, request_timeout_seconds=0.05)
        client = TestClient(app)
        response_holder = []
        request_thread = threading.Thread(target=lambda: response_holder.append(client.post(
            "/analysis", json={"question": "退款分析"}, headers={"Authorization": "Bearer secret-token"})))
        request_thread.start()
        self.assertTrue(entered.wait(1))
        request_thread.join(1)
        self.assertEqual(response_holder[0].status_code, 504)
        run_id = response_holder[0].json()["run_id"]
        self.assertEqual(self.store.get(run_id)["status"], "running")
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.store.get(run_id)["status"] == "running":
            time.sleep(0.01)
        self.assertEqual(self.store.get(run_id)["status"], "cancelled")
        recovered = client.post("/analysis", json={"question": "退款分析恢复"},
                                headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(recovered.status_code, 200)

    def test_api_requires_marketing_scope_for_marketing_roi(self):
        planner = MarketingPlanner()
        core = InsightPilot(planner, self.database)
        question = "2026-10-01 到 2026-10-31 各营销活动投资回报率是多少？"
        denied_app = create_app(core, run_store=self.store, api_token="secret-token", api_scopes={"refund:read"})
        denied = TestClient(denied_app).post(
            "/analysis", json={"question": question}, headers={"Authorization": "Bearer secret-token"}
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(planner.called)

        allowed_app = create_app(core, run_store=self.store, api_token="secret-token", api_scopes={"marketing:read"})
        client = TestClient(allowed_app)
        allowed = client.post("/analysis", json={"question": question},
                              headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(allowed.status_code, 422)
        self.assertTrue(planner.called)
        run_id = allowed.json()["run_id"]
        self.assertEqual(client.get(f"/runs/{run_id}", headers={"Authorization": "Bearer secret-token"}).status_code, 200)

    def test_cancel_endpoint_marks_active_run(self):
        self.store.start("run-3", "退款分析")
        app = create_app(self.core, run_store=self.store, api_token="secret-token", api_scopes={"refund:read", "refund:cancel"})
        response = TestClient(app).post("/runs/run-3/cancel", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(self.store.get("run-3")["cancel_requested"])

    def test_authorized_region_cannot_be_overridden_by_question_or_model(self):
        result = self.core.analyze("退款分析", authorized_region="华南")
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["reason"], "region_scope_denied")

    def test_pre_cancelled_query_stops_before_database_access(self):
        event = Event()
        event.set()
        with self.assertRaises(QueryCancelledError):
            execute_readonly(self.database, CompiledQuery("SELECT order_id FROM orders LIMIT 1", ()), event)

    def test_shared_bearer_middleware_protects_mcp_http_surface(self):
        async def endpoint(_request):
            return JSONResponse({"status": "ok"})

        app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
        app.add_middleware(BearerScopeMiddleware, token="secret-token", scopes={"refund:read"})
        client = TestClient(app)
        self.assertEqual(client.post("/mcp").status_code, 401)
        response = client.post("/mcp", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(response.status_code, 200)

    def test_mcp_scope_resolver_enforces_marketing_scope_from_tool_question(self):
        async def endpoint(request: Request):
            payload = await request.json()
            question = payload["params"]["arguments"]["question"]
            return JSONResponse({"question": question})

        app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])] )
        app.add_middleware(BearerScopeMiddleware, token="secret-token", scopes={"refund:read"},
                           question_scope_resolver=required_access_scopes)
        client = TestClient(app)
        payload = {"jsonrpc": "2.0", "method": "tools/call", "params": {"arguments": {
            "question": "2026-10-01 到 2026-10-31 各营销活动投资回报率是多少？"}}}
        denied = client.post("/mcp", json=payload, headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(denied.status_code, 403)

        app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])] )
        app.add_middleware(BearerScopeMiddleware, token="secret-token", scopes={"marketing:read"},
                           question_scope_resolver=required_access_scopes)
        allowed = TestClient(app).post("/mcp", json=payload,
                                       headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["question"], payload["params"]["arguments"]["question"])


if __name__ == "__main__":
    unittest.main()
