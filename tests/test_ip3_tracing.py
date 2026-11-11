from __future__ import annotations

import contextvars
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError


class MemorySpan:
    def __init__(self, exporter, name, parent):
        self.exporter = exporter
        self.name = name
        self.parent = parent
        self.attributes = {}
        self.status = "unset"
        self.status_description = None

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, status):
        self.status = status.status_code.name
        self.status_description = status.description

    def end(self):
        self.exporter.spans.append(self)


class InMemorySpanExporter:
    """Tiny in-memory tracer seam for deterministic offline API integration tests."""

    def __init__(self):
        self.spans = []
        self.current = contextvars.ContextVar("test_current_span", default=None)

    @contextmanager
    def start_as_current_span(self, name):
        parent = self.current.get()
        span = MemorySpan(self, name, parent)
        token = self.current.set(span)
        try:
            yield span
        finally:
            self.current.reset(token)
            span.end()


class FixedPlanner:
    def plan(self, _question):
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


class FailingIdentityEngine(SQLiteQueryEngine):
    def read_data_identity(self, *, tenant_id=None):
        raise QueryExecutionError("synthetic database unavailable", failure_code="database_execution")


class TenantSQLiteEngine(SQLiteQueryEngine):
    def __init__(self, path, tenant_id):
        super().__init__(path)
        self.tenant_id = tenant_id

    def read_data_identity(self, *, tenant_id=None):
        return super().read_data_identity()

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        return super().execute_readonly(query, cancel_event=cancel_event)


class IP3TracingTests(unittest.TestCase):
    def make_app(self, root, *, engine=None, tracer):
        database = create_database(root / "synthetic.sqlite")
        engine = engine or TenantSQLiteEngine(database, "tenant-sensitive-marker")
        core = InsightPilot(FixedPlanner(), database, query_engine=engine, tracer=tracer)
        app = create_app(core, run_store=RunStore(root / "runs.sqlite"),
                         api_tenant_tokens={"test-bearer": "tenant-sensitive-marker"},
                         api_scopes={"refund:read"}, tracer=tracer)
        return app

    def test_success_creates_safe_parent_child_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            exporter = InMemorySpanExporter()
            app = self.make_app(Path(temp), tracer=exporter)
            question = "退款率 marker-question-sensitive"
            with TestClient(app) as client:
                response = client.post("/analysis", json={"question": question},
                                       headers={"Authorization": "Bearer test-bearer"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual([span.name for span in exporter.spans],
                             ["insightpilot.agent.run", "insightpilot.api.analysis"])
            agent_span, api_span = exporter.spans
            self.assertIs(agent_span.parent, api_span)
            self.assertEqual(agent_span.status, "OK")
            self.assertEqual(api_span.status, "OK")
            self.assertEqual(agent_span.attributes["insightpilot.run.status"], "answered")
            self.assertEqual(api_span.attributes["insightpilot.run.id"], response.json()["run_id"])
            self.assert_safe_export(exporter, question)

    def test_database_failure_records_classification_without_sensitive_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            exporter = InMemorySpanExporter()
            engine = FailingIdentityEngine(database)
            engine.tenant_id = "tenant-sensitive-marker"
            core = InsightPilot(FixedPlanner(), database, query_engine=engine, tracer=exporter)
            app = create_app(core, run_store=RunStore(root / "runs.sqlite"),
                             api_tenant_tokens={"test-bearer": "tenant-sensitive-marker"},
                             api_scopes={"refund:read"}, tracer=exporter)
            question = "退款率 marker-database-question"
            with TestClient(app) as client:
                response = client.post("/analysis", json={"question": question},
                                       headers={"Authorization": "Bearer test-bearer"})

            self.assertEqual(response.status_code, 502)
            self.assertEqual([span.name for span in exporter.spans],
                             ["insightpilot.agent.run", "insightpilot.api.analysis"])
            agent_span, api_span = exporter.spans
            self.assertIs(agent_span.parent, api_span)
            self.assertIsNone(api_span.parent)
            for span in (agent_span, api_span):
                self.assertEqual(span.status, "ERROR")
                self.assertEqual(span.attributes["insightpilot.run.stage"], "dataset")
                self.assertEqual(span.attributes["insightpilot.failure.category"], "database_execution")
                self.assertEqual(span.attributes["insightpilot.failure.layer"], "db_execution")
            self.assert_safe_export(exporter, question)

    @staticmethod
    def assert_safe_export(exporter, question):
        serialized = repr([(span.name, span.attributes, span.status_description) for span in exporter.spans])
        for sensitive in (question, "tenant-sensitive-marker", "2026-10-05", "华东"):
            if sensitive in serialized:
                raise AssertionError(f"sensitive input appeared in span export: {sensitive}")


if __name__ == "__main__":
    unittest.main()
