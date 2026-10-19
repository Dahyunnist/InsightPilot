import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data.seed import create_database
from insightpilot.planner import QueryPlan
from insightpilot.runner import QueryBudgetError, QueryTimeoutError, execute_readonly
from insightpilot.sql import compile_query
from insightpilot.sql_policy import SQLPolicyError, validate_sql
from insightpilot.verification import verify_result


class S2PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = create_database(Path(self.tempdir.name) / "fixture.sqlite")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_compiled_metric_templates_pass_ast_policy_and_verification(self):
        from insightpilot.agent import InsightPilot

        cases = [
            QueryPlan("approved_refund_order_rate", "2026-10-05", "2026-10-12", "华东", "category"),
            QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", "channel"),
            QueryPlan("approved_refund_amount_contribution", "2026-10-05", "2026-10-12", "华东", "category"),
        ]
        for plan in cases:
            result = InsightPilot(type("Stub", (), {"plan": lambda _self, _question, p=plan: p})(), self.db_path).analyze("question")
            self.assertEqual(result["status"], "answered", result)
            self.assertTrue(result["verification"]["passed"])

    def test_rejects_write_multi_statement_unlisted_data_dangerous_function_and_budget(self):
        rejected = [
            "DELETE FROM orders",
            "SELECT order_id FROM orders LIMIT 1; SELECT order_id FROM orders LIMIT 1",
            "SELECT customer_id FROM orders LIMIT 1",
            "SELECT order_id FROM main.orders LIMIT 1",
            "SELECT load_extension('x') FROM orders LIMIT 1",
            "SELECT order_id FROM orders LIMIT 101",
            "SELECT * FROM orders LIMIT 1",
            "WITH x AS (SELECT order_id FROM orders) SELECT order_id FROM x LIMIT 1",
            "WITH RECURSIVE base AS (SELECT order_id FROM orders) SELECT order_id FROM base LIMIT 1",
        ]
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(SQLPolicyError):
                validate_sql(sql)

    def test_sqlite_authorizer_and_readonly_uri_block_mutation(self):
        uri = "file:" + self.db_path.resolve().as_posix() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM orders")
        finally:
            connection.close()

    def test_mismatched_independent_check_stops_answer(self):
        from insightpilot.agent import InsightPilot

        plan = QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)
        with patch("insightpilot.verification.execute_readonly", return_value=([{
            "numerator_cents": 49000, "denominator_cents": 90000, "value": 49000 / 90000,
        }], "insightpilot-s0-v1")):
            check = verify_result(plan, [{
                "numerator_cents": 50000, "denominator_cents": 90000, "value": 50000 / 90000,
            }], self.db_path)
        self.assertFalse(check["passed"])

        app = InsightPilot(type("Stub", (), {"plan": lambda _self, _question: plan})(), self.db_path)
        with patch("insightpilot.agent.verify_result", return_value={"passed": False, "checks": {}}):
            result = app.analyze("问题")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "verification")
        self.assertNotIn("rows", result)

    def test_progress_handler_returns_timeout_state(self):
        query = compile_query(QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", None, None))
        with patch("insightpilot.runner.QUERY_TIMEOUT_SECONDS", 0), patch("insightpilot.runner.PROGRESS_OP_INTERVAL", 1):
            with self.assertRaises(QueryTimeoutError):
                execute_readonly(self.db_path, query)

    def test_progress_handler_enforces_virtual_machine_budget(self):
        query = compile_query(QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", None, None))
        with patch("insightpilot.runner.MAX_VM_STEPS", 0), patch("insightpilot.runner.PROGRESS_OP_INTERVAL", 1):
            with self.assertRaises(QueryBudgetError):
                execute_readonly(self.db_path, query)


if __name__ == "__main__":
    unittest.main()
