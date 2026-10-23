from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError
from insightpilot.rule_baseline import RulePlanner


class TenantCaptureEngine:
    tenant_id = None

    def __init__(self, database: Path):
        self.identity = SQLiteQueryEngine(database).read_data_identity()
        self.barrier = threading.Barrier(2)
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
    def test_authenticated_tokens_route_concurrent_requests_without_header_override(self):
        database = Path(__file__).resolve().parents[1] / "data" / "insightpilot-s0.sqlite"
        engine = TenantCaptureEngine(database)
        core = InsightPilot(RulePlanner(), database, query_engine=engine)
        with TemporaryDirectory() as temp:
            app = create_app(core, run_store=RunStore(Path(temp) / "runs.sqlite"),
                             api_tenant_tokens={"token-a": "tenant-a", "token-b": "tenant-b"},
                             api_scopes={"refund:read"})
            with TestClient(app) as client:
                def request(token: str, spoofed: str):
                    return client.post("/analysis", json={"question": "2026-10-01 到 2026-10-07 退款订单率"}, headers={
                        "Authorization": f"Bearer {token}", "X-Tenant-ID": spoofed,
                    })

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda args: request(*args),
                                                  [("token-a", "tenant-b"), ("token-b", "tenant-a")]))
                self.assertEqual([response.status_code for response in responses], [502, 502])
                self.assertCountEqual(engine.tenants, ["tenant-a", "tenant-b"])

                self.assertEqual(client.post("/analysis", json={"question": "2026-10-01 到 2026-10-07 退款订单率"},
                    headers={"Authorization": "Bearer unknown"}).status_code, 401)
                self.assertEqual(client.post("/analysis", json={"question": "2026-10-01 到 2026-10-07 退款订单率"}).status_code, 401)

                headers_a = {"Authorization": "Bearer token-a", "Idempotency-Key": "shared-key"}
                headers_b = {"Authorization": "Bearer token-b", "Idempotency-Key": "shared-key"}
                result_a = client.post("/analysis", json={"question": "删除退款订单"}, headers=headers_a)
                result_b = client.post("/analysis", json={"question": "删除退款订单"}, headers=headers_b)
                run_a, run_b = result_a.json()["run_id"], result_b.json()["run_id"]
                self.assertNotEqual(run_a, run_b)
                self.assertEqual(client.get(f"/runs/{run_a}", headers=headers_b).status_code, 404)
                self.assertEqual(client.get(f"/runs/{run_a}", headers=headers_a).status_code, 200)

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
