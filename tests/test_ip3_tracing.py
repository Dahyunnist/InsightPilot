from __future__ import annotations

import contextvars
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.run_store import RunStore
from insightpilot.runner import QueryExecutionError
from insightpilot.tracing import configure_otlp_tracing


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


class FakeBatchSpanProcessor:
    def __init__(self, exporter):
        self.exporter = exporter


class FakeTracerProvider:
    def __init__(self, resource, exporter):
        self.resource = resource
        self.exporter = exporter
        self.processor = None
        self.shutdown_called = False

    def add_span_processor(self, processor):
        self.processor = processor

    def get_tracer(self, _name):
        return self.exporter

    def shutdown(self):
        self.shutdown_called = True


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
    def make_app(self, root, *, engine=None, tracer, shutdown=None):
        database = create_database(root / "synthetic.sqlite")
        engine = engine or TenantSQLiteEngine(database, "tenant-sensitive-marker")
        core = InsightPilot(FixedPlanner(), database, query_engine=engine, tracer=tracer)
        app = create_app(core, run_store=RunStore(root / "runs.sqlite"),
                         api_tenant_tokens={"test-bearer": "tenant-sensitive-marker"},
                         api_scopes={"refund:read"}, tracer=tracer, trace_shutdown=shutdown)
        return app

    @staticmethod
    def configured_memory_tracer():
        state = {}

        def exporter_factory(*, endpoint):
            state["endpoint"] = endpoint
            state["exporter"] = InMemorySpanExporter()
            return state["exporter"]

        def provider_factory(*, resource):
            state["provider"] = FakeTracerProvider(resource, state["exporter"])
            return state["provider"]

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.invalid:4318"}, clear=False):
            with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ""}):
                tracer, shutdown = configure_otlp_tracing(
                    provider_factory=provider_factory,
                    exporter_factory=exporter_factory,
                    batch_processor_factory=FakeBatchSpanProcessor,
                    resource_factory=lambda attrs: attrs,
                )
        return state, tracer, shutdown

    def test_missing_endpoint_keeps_optional_tracing_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            tracer, shutdown = configure_otlp_tracing()
        self.assertIsNone(tracer)
        self.assertIsNone(shutdown)

    def test_endpoint_configures_batch_otlp_pipeline_without_network(self):
        state, tracer, shutdown = self.configured_memory_tracer()
        provider = state["provider"]
        self.assertEqual(state["endpoint"], "http://collector.invalid:4318/v1/traces")
        self.assertEqual(provider.resource, {"service.name": "insightpilot"})
        self.assertIsInstance(provider.processor, FakeBatchSpanProcessor)
        self.assertIs(provider.processor.exporter, state["exporter"])
        self.assertIs(tracer, state["exporter"])
        shutdown()
        self.assertTrue(provider.shutdown_called)

    def test_success_creates_safe_parent_child_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            state, tracer, shutdown = self.configured_memory_tracer()
            exporter = state["exporter"]
            app = self.make_app(Path(temp), tracer=tracer, shutdown=shutdown)
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
            self.assertTrue(state["provider"].shutdown_called)

    def test_database_failure_records_classification_without_sensitive_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = create_database(root / "synthetic.sqlite")
            state, tracer, shutdown = self.configured_memory_tracer()
            exporter = state["exporter"]
            engine = FailingIdentityEngine(database)
            engine.tenant_id = "tenant-sensitive-marker"
            core = InsightPilot(FixedPlanner(), database, query_engine=engine, tracer=tracer)
            app = create_app(core, run_store=RunStore(root / "runs.sqlite"),
                             api_tenant_tokens={"test-bearer": "tenant-sensitive-marker"},
                             api_scopes={"refund:read"}, tracer=tracer, trace_shutdown=shutdown)
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
            self.assertTrue(state["provider"].shutdown_called)

    @staticmethod
    def assert_safe_export(exporter, question):
        serialized = repr([(span.name, span.attributes, span.status_description) for span in exporter.spans])
        for sensitive in (question, "tenant-sensitive-marker", "2026-10-05", "华东"):
            if sensitive in serialized:
                raise AssertionError(f"sensitive input appeared in span export: {sensitive}")


if __name__ == "__main__":
    unittest.main()
