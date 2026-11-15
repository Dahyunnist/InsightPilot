from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tests.temp_utils import TemporaryDirectory

from fastapi import HTTPException
from fastapi.routing import APIRoute

from insightpilot.agent import InsightPilot
from insightpilot.api import AnalysisRequest, create_app
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError, QueryTimeoutError
from insightpilot.rule_baseline import RulePlanner


class TenantCaptureEngine:
    backend_name = "postgresql"
    requires_tenant_scope = True
    tenant_id = None

    def __init__(self, database: Path):
        self.identity = SQLiteQueryEngine(database).read_data_identity()
        self.barrier = threading.Barrier(12)
        self.tenants: list[str | None] = []
        self.lock = threading.Lock()

    def read_data_identity(self, *, tenant_id=None):
        if not tenant_id:
            raise QueryExecutionError("tenant missing")
        return self.identity

    def execute_readonly(self, _query, *, tenant_id=None, cancel_event=None):
        with self.lock:
            self.tenants.append(tenant_id)
        self.barrier.wait(timeout=5)
        raise QueryExecutionError("test stop after tenant capture")


class ApiTenantIdentityTests(unittest.TestCase):
    def test_failure_trace_classifies_safe_layers_without_raw_sql_or_tenant(self):
        database = Path(__file__).resolve().parents[1] / "data" / "insightpilot-s0.sqlite"
        engine = TenantCaptureEngine(database)
        core = InsightPilot(RulePlanner(), database, query_engine=engine)
        trace = core._failure_detail(QueryTimeoutError("internal database text"), stage="query",
                                     sql="SELECT secret_value FROM private_table",
                                     parameter_count=2, tenant_id="tenant-secret")
        self.assertEqual(trace["layer"], "db_execution")
        self.assertEqual(trace["category"], "statement_timeout")
        self.assertEqual(trace["parameter_count"], 2)
        self.assertNotIn("private_table", str(trace))
        self.assertNotIn("tenant-secret", str(trace))
        self.assertNotIn("internal database text", str(trace))

    def test_authenticated_route_binds_concurrent_tenants_without_tenant_header_parameter(self):
        database = Path(__file__).resolve().parents[1] / "data" / "insightpilot-s0.sqlite"
        engine = TenantCaptureEngine(database)
        core = InsightPilot(RulePlanner(), database, query_engine=engine)
        with TemporaryDirectory() as temp:
            app = create_app(core, run_store=RunStore(Path(temp) / "runs.sqlite"),
                             api_tenant_tokens={"token-a": "tenant-a", "token-b": "tenant-b"},
                             api_scopes={"refund:read"}, max_concurrent=12)
            analysis_route = next(route for route in app.routes
                                  if isinstance(route, APIRoute) and route.path == "/analysis")
            analysis = analysis_route.endpoint
            get_run = next(route.endpoint for route in app.routes
                           if isinstance(route, APIRoute) and route.path == "/runs/{run_id}")
            header_aliases = {parameter.alias for parameter in analysis_route.dependant.header_params}
            self.assertIn("authorization", header_aliases)
            self.assertNotIn("X-Tenant-ID", header_aliases)

            def request(token: str):
                return analysis(AnalysisRequest(question="2026-10-01 到 2026-10-07 退款订单率"),
                                authorization=f"Bearer {token}", idempotency_key=None)

            try:
                tokens = ["token-a", "token-b"] * 6
                with ThreadPoolExecutor(max_workers=12) as executor:
                    responses = list(executor.map(request, tokens))
                self.assertEqual([response.status_code for response in responses], [502] * 12)
                self.assertCountEqual(engine.tenants, ["tenant-a"] * 6 + ["tenant-b"] * 6)
                trace = json.loads(responses[0].body)["failure_detail"]
                self.assertEqual(trace["layer"], "db_execution")
                self.assertEqual(trace["tenant_context"], "bound")
                self.assertRegex(trace["sql_sha256"], r"^[0-9a-f]{64}$")
                self.assertNotIn("tenant-a", str(trace))
                self.assertNotIn("tenant-b", str(trace))

                for credential in ("Bearer unknown", None):
                    with self.subTest(credential=credential), self.assertRaises(HTTPException) as raised:
                        analysis(AnalysisRequest(question="2026-10-01 到 2026-10-07 退款订单率"),
                                 authorization=credential, idempotency_key=None)
                    self.assertEqual(raised.exception.status_code, 401)

                question = "删除退款订单"
                result_a = analysis(AnalysisRequest(question=question), authorization="Bearer token-a",
                                    idempotency_key="shared-key")
                result_b = analysis(AnalysisRequest(question=question), authorization="Bearer token-b",
                                    idempotency_key="shared-key")
                run_a = json.loads(result_a.body)["run_id"]
                run_b = json.loads(result_b.body)["run_id"]
                self.assertNotEqual(run_a, run_b)
                with self.assertRaises(HTTPException) as hidden:
                    get_run(run_a, authorization="Bearer token-b")
                self.assertEqual(hidden.exception.status_code, 404)
                self.assertEqual(get_run(run_a, authorization="Bearer token-a")["run_id"], run_a)
            finally:
                app.state.analysis_executor.shutdown(wait=True)

        with self.assertRaisesRegex(ValueError, "token-to-tenant mapping"):
            create_app(InsightPilot(RulePlanner(), database,
                                   query_engine=TenantCaptureEngine(database)))

    def test_run_records_are_hidden_from_other_principals(self):
        with TemporaryDirectory() as temp:
            store = RunStore(Path(temp) / "runs.sqlite")
            store.start("run-a", "退款订单率", principal_id="tenant-a")
            self.assertIsNotNone(store.get("run-a", principal_id="tenant-a"))
            self.assertIsNone(store.get("run-a", principal_id="tenant-b"))
            self.assertFalse(store.request_cancel("run-a", principal_id="tenant-b"))


if __name__ == "__main__":
    unittest.main()
