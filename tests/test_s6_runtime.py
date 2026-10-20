import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.auth import BearerScopeMiddleware
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryCancelledError, execute_readonly
from insightpilot.sql import CompiledQuery
from threading import Event


class StubPlanner:
    def plan(self, _question):
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


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


if __name__ == "__main__":
    unittest.main()
