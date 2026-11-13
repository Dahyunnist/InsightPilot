from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from threading import Event
from pathlib import Path
from unittest.mock import patch

from data.seed import create_database
from insightpilot.agent import build_core
from insightpilot.config import Settings
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import QueryEngine, SQLiteQueryEngine
from insightpilot.runner import QueryCancelledError, QueryExecutionError
from insightpilot.sql import CompiledQuery, compile_query


class FixedPlanner:
    def plan(self, _question):
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", None, None)


class QueryEngineContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = create_database(Path(self.tempdir.name) / "fixture.sqlite")
        self.engine = SQLiteQueryEngine(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_sqlite_adapter_satisfies_shared_readonly_query_contract(self):
        self.assertIsInstance(self.engine, QueryEngine)
        self.assertEqual(self.engine.backend_name, "sqlite")
        self.assertFalse(self.engine.requires_tenant_scope)
        self.assertIsNone(self.engine.tenant_id)
        self.assertEqual(self.engine.read_data_identity()[0], "insightpilot-s0-v1")

        query = compile_query(QueryPlan(
            "approved_refund_amount_rate", "2026-10-05", "2026-10-12", None, None))
        self.assertIn("?", query.sql)
        self.assertTrue(query.params)
        rows, version = self.engine.execute_readonly(query)
        self.assertEqual(version, "insightpilot-s0-v1")
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows), 100)

    def test_sqlite_adapter_enforces_readonly_allowlist_and_tenant_boundary(self):
        denied_queries = (
            CompiledQuery("DELETE FROM orders WHERE order_id = ?", ("order-1",)),
            CompiledQuery("SELECT customer_id FROM orders LIMIT 1", ()),
            CompiledQuery("SELECT email FROM customers LIMIT 1", ()),
            CompiledQuery("SELECT order_id FROM orders LIMIT 101", ()),
        )
        for query in denied_queries:
            with self.subTest(sql=query.sql), self.assertRaises(QueryExecutionError):
                self.engine.execute_readonly(query)

        safe_query = CompiledQuery("SELECT ? AS value FROM orders LIMIT 1", ("bound-value",))
        rows, _ = self.engine.execute_readonly(safe_query)
        self.assertEqual(rows[0]["value"], "bound-value")
        self.assertNotIn("bound-value", safe_query.sql)

        with self.assertRaises(QueryExecutionError):
            self.engine.execute_readonly(safe_query, tenant_id="tenant-a")

    def test_sqlite_adapter_preserves_cancellation_guard(self):
        cancelled = Event()
        cancelled.set()
        query = CompiledQuery("SELECT order_id FROM orders LIMIT 1", ())

        with self.assertRaises(QueryCancelledError):
            self.engine.execute_readonly(query, cancel_event=cancelled)

    def test_shared_core_factory_preserves_sqlite_default_and_injected_adapter(self):
        settings = Settings("key", "https://example.invalid", "unused", 1, 16,
                            self.database, None)
        default_core = build_core(settings)
        self.assertIsInstance(default_core.query_engine, SQLiteQueryEngine)

        injected_core = build_core(settings, query_engine=self.engine)
        self.assertIs(injected_core.query_engine, self.engine)
        self.assertEqual(injected_core.query_engine.backend_name, "sqlite")


class EntryPointCoreFactoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = create_database(Path(self.tempdir.name) / "fixture.sqlite")
        self.settings = Settings("key", "https://example.invalid", "unused", 1, 16,
                                 database, None)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_cli_mcp_and_api_startups_use_shared_core_factory(self):
        from insightpilot import agent, api, cli, mcp_server

        with patch.object(agent, "OpenAICompatiblePlanner", return_value=FixedPlanner()):
            with patch.object(cli, "build_core", wraps=agent.build_core) as cli_factory, \
                    patch.object(cli, "load_settings", return_value=self.settings), \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    patch.object(sys, "argv", ["insightpilot", "2026-10-05 至 2026-10-12 退款金额率"]):
                cli.main()
                self.assertEqual(json.loads(output.getvalue())["query_backend"], "sqlite")
                cli_factory.assert_called_once_with(self.settings)

            with patch.object(mcp_server, "build_core", wraps=agent.build_core) as mcp_factory, \
                    patch.object(mcp_server, "load_settings", return_value=self.settings), \
                    patch.object(mcp_server.uvicorn, "run") as run_server:
                mcp_server.main()
                run_server.assert_called_once()
                mcp_factory.assert_called_once_with(self.settings)

            with patch.object(api, "build_core", wraps=agent.build_core) as api_factory, \
                    patch.object(api, "load_settings", return_value=self.settings), \
                    patch.object(api, "configure_otlp_tracing", return_value=(None, None)):
                app = api.build_default_app()
                self.assertIsInstance(app.state.core.query_engine, SQLiteQueryEngine)
                api_factory.assert_called_once_with(self.settings, query_engine=None, tracer=None)


if __name__ == "__main__":
    unittest.main()
