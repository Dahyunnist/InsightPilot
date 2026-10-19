import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.metrics import METRICS
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner, PlanError, QueryPlan
from insightpilot.runner import QueryExecutionError, execute_readonly
from insightpilot.sql import CompiledQuery, compile_query


class StubPlanner:
    def __init__(self, plan=None, error=None):
        self._plan = plan
        self._error = error

    def plan(self, question):
        if self._error:
            raise self._error
        return self._plan


class S1FlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = create_database(Path(self.tempdir.name) / "fixture.sqlite")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_amount_rate_end_to_end_has_metric_sql_and_evidence(self):
        plan = QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)
        result = InsightPilot(StubPlanner(plan), self.db_path).analyze("问题")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["metric"]["id"], "approved_refund_amount_rate")
        self.assertEqual(result["data_version"], "insightpilot-s0-v1")
        self.assertEqual(result["rows"][0]["numerator_cents"], 50000)
        self.assertEqual(result["rows"][0]["denominator_cents"], 90000)
        self.assertAlmostEqual(result["rows"][0]["value"], 5 / 9)
        self.assertIn("WHERE status = 'approved'", result["sql"])
        self.assertEqual(result["parameters"], ["2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z", "华东"])

    def test_category_contribution_end_to_end(self):
        plan = QueryPlan("approved_refund_amount_contribution", "2026-10-05", "2026-10-12", "华东", "category")
        result = InsightPilot(StubPlanner(plan), self.db_path).analyze("问题")
        self.assertEqual(result["status"], "answered")
        self.assertEqual([row["group_value"] for row in result["rows"]], ["家电", "服饰", "食品"])
        self.assertEqual([row["numerator_cents"] for row in result["rows"]], [20000, 10000, 20000])
        self.assertEqual([row["denominator_cents"] for row in result["rows"]], [50000, 50000, 50000])
        self.assertEqual([round(row["value"], 2) for row in result["rows"]], [0.4, 0.2, 0.4])

    def test_vague_metric_and_relative_time_stop_before_model(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_: self.fail("ambiguous prompt must not call the model")
        with self.assertRaises(NeedsClarification):
            planner.plan("上周华东退款率是多少？")
        with self.assertRaises(NeedsClarification):
            planner.plan("2026-10-05 至 2026-10-11 退款率和品类贡献度")

    def test_write_and_personal_data_requests_are_refused(self):
        planner = StubPlanner(error=AssertionError("planner must not run"))
        app = InsightPilot(planner, self.db_path)
        self.assertEqual(app.analyze("把订单金额改成 0")["reason"], "write_operation_not_supported")
        self.assertEqual(app.analyze("导出客户手机号")["reason"], "restricted_personal_data")

    def test_readonly_connection_rejects_mutation(self):
        with self.assertRaises(QueryExecutionError):
            execute_readonly(self.db_path, CompiledQuery("DELETE FROM orders", ()))
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM orders").fetchone()[0], 30)
        finally:
            conn.close()

    def test_model_and_query_failures_return_explicit_states(self):
        model_error = InsightPilot(StubPlanner(error=PlanError("LLM request failed: TimeoutError")), self.db_path)
        self.assertEqual(model_error.analyze("问题")["stage"], "planning")
        missing_data = InsightPilot(
            StubPlanner(QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)),
            Path(self.tempdir.name) / "missing.sqlite",
        )
        result = missing_data.analyze("问题")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "query")

    def test_model_plan_is_validated_against_retrieved_metric_and_dimensions(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_: {
            "clarify": False, "metric_id": "approved_refund_amount_rate",
            "start_date": "2026-10-05", "end_date_exclusive": "2026-10-12",
            "region": "华东", "dimension": "category",
        }
        plan = planner.plan("2026-10-05 至 2026-10-11 华东金额退款率按品类")
        self.assertEqual(plan.end_date, "2026-10-12")
        self.assertEqual(plan.dimension, "category")

        planner._complete = lambda *_: {
            "clarify": False, "metric_id": "approved_refund_order_rate",
            "start_date": "2026-10-05", "end_date_exclusive": "2026-10-12",
            "region": "华东", "dimension": None,
        }
        with self.assertRaises(PlanError):
            planner.plan("2026-10-05 至 2026-10-11 华东金额退款率")


if __name__ == "__main__":
    unittest.main()
