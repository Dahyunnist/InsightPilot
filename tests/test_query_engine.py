import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from insightpilot.planner import QueryPlan
from insightpilot.query_engine import PostgresQueryEngine, QueryEngine, SQLiteQueryEngine
from insightpilot.runner import QueryExecutionError, QueryTimeoutError
from insightpilot.sql import compile_query


class FakeConnection:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []
        self.description = [SimpleNamespace(name="group_value"), SimpleNamespace(name="value")]

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if self.error and "FROM orders" in sql:
            raise self.error
        if sql.startswith("SELECT ") and "FROM orders" in sql:
            return self
        return self

    def fetchmany(self, size):
        return self.rows[:size]


class FakePool:
    def __init__(self, connection):
        self.value = connection

    @contextmanager
    def connection(self):
        yield self.value


class QueryEngineTests(unittest.TestCase):
    def test_postgres_adapter_is_query_engine_and_binds_scope_and_parameters(self):
        connection = FakeConnection([("全部", 0.25)])
        engine = PostgresQueryEngine("unused", tenant_id="tenant-a", pool=FakePool(connection))
        query = compile_query(QueryPlan("approved_refund_order_rate", "2026-10-01", "2026-10-08", None, None))
        self.assertIsInstance(engine, QueryEngine)

        rows, version = engine.execute_readonly(query)

        self.assertEqual(rows, [{"group_value": "全部", "value": 0.25}])
        self.assertEqual(version, "postgresql")
        self.assertIn(("SET TRANSACTION READ ONLY", ()), connection.calls)
        self.assertIn(("SET LOCAL ROLE insightpilot_reader", ()), connection.calls)
        self.assertIn(("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",)), connection.calls)
        self.assertIn(("SELECT set_config('statement_timeout', %s, true)", ("2000ms",)), connection.calls)
        statement, params = connection.calls[-1]
        self.assertIn("LIMIT 100", statement)
        self.assertEqual(params, query.params)

    def test_postgres_requires_trusted_tenant_and_rejects_mutations_before_pool(self):
        pool = MagicMock()
        engine = PostgresQueryEngine("unused", pool=pool)
        query = compile_query(QueryPlan("approved_refund_order_rate", "2026-10-01", "2026-10-08", None, None))
        with self.assertRaises(QueryExecutionError):
            engine.execute_readonly(query)
        for sql in ("DELETE FROM orders", "DROP TABLE orders", "SELECT customer_id FROM orders LIMIT 1"):
            with self.subTest(sql=sql), self.assertRaises(QueryExecutionError):
                engine.execute_readonly(type(query)(sql, ()), tenant_id="tenant-a")
        with self.assertRaises(QueryExecutionError):
            engine.execute_readonly(type(query)(query.sql, ("one-value",)), tenant_id="tenant-a")
        pool.connection.assert_not_called()

    def test_postgres_maps_statement_timeout_and_enforces_result_budget(self):
        timeout = RuntimeError("canceling statement due to statement timeout")
        timeout.sqlstate = "57014"
        engine = PostgresQueryEngine("unused", tenant_id="tenant-a", pool=FakePool(FakeConnection(error=timeout)))
        query = compile_query(QueryPlan("approved_refund_order_rate", "2026-10-01", "2026-10-08", None, None))
        with self.assertRaises(QueryTimeoutError):
            engine.execute_readonly(query)

        engine = PostgresQueryEngine("unused", tenant_id="tenant-a", pool=FakePool(FakeConnection([(str(i), 0.1) for i in range(3)])), max_rows=2)
        with self.assertRaises(QueryExecutionError):
            engine.execute_readonly(query)

    def test_sqlite_compatibility_adapter_rejects_unsupported_tenant_scope(self):
        engine = SQLiteQueryEngine(None)
        with self.assertRaises(QueryExecutionError):
            engine.execute_readonly(compile_query(QueryPlan("approved_refund_order_rate", "2026-10-01", "2026-10-08", None, None)), tenant_id="tenant-a")


if __name__ == "__main__":
    unittest.main()
