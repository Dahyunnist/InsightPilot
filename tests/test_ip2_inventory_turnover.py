import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.inventory_fixture import create_inventory_fixture
from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.metrics import METRICS, catalog_document, required_access_scopes, retrieve_metrics
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner
from insightpilot.planner import QueryPlan


class IP2InventoryTurnoverTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = create_inventory_fixture(Path(self.tempdir.name) / "inventory.sqlite")

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def planner(reply):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner.use_metric_retrieval = True
        calls = []

        def complete(question, candidate_ids, allowed_ids):
            calls.append({"question": question, "candidate_ids": candidate_ids,
                          "allowed_ids": allowed_ids, "reply": reply})
            return reply

        planner._complete = complete
        return planner, calls

    def test_inventory_metric_is_versioned_and_retrieved(self):
        metric = METRICS["inventory_turnover"]
        self.assertEqual((metric.definition_version, metric.effective_from), ("1.0.0", "2026-11-01"))
        self.assertEqual(metric.required_data_versions, ("insightpilot-inventory-v1",))
        self.assertEqual([item.metric_id for item in retrieve_metrics(
            "2026-11-01 至 2026-11-03 库存周转率按商品类别是多少？")], ["inventory_turnover"])
        catalog = catalog_document()
        entry = next(item for item in catalog["metrics"] if item["metric_id"] == "inventory_turnover")
        self.assertEqual(entry["timezone"], "Asia/Shanghai")
        self.assertIn("inventory_category", catalog["dimensions"])
        self.assertEqual(required_access_scopes("库存周转率"), {"inventory:read"})

    def test_structured_plan_compiles_and_matches_independent_python_gold(self):
        reply = {"clarify": False, "metric_id": "inventory_turnover", "start_date": "2026-11-01",
                 "end_date_exclusive": "2026-11-04", "comparison_periods": [], "region": None,
                 "dimension": "inventory_category"}
        planner, calls = self.planner(reply)
        question = "2026-11-01 至 2026-11-03 库存周转率按商品类别是多少？"
        result = InsightPilot(planner, self.database, tracer=None).analyze(question)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["metric"]["id"], "inventory_turnover")
        self.assertEqual(result["data_version"], "insightpilot-inventory-v1")
        self.assertEqual(result["model_call_count"], 0)
        self.assertEqual(calls[0]["candidate_ids"], ["inventory_turnover"])
        self.assertNotIn("sql", calls[0]["reply"])
        self.assertEqual(result["parameters"], ["2026-10-31T16:00:00Z", "2026-11-03T16:00:00Z"] * 2)
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(result["verification"]["method"], "independent_python_inventory_ledger")
        rows = {row["group_value"]: row for row in result["rows"]}
        self.assertAlmostEqual(rows["家电"]["denominator_cents"], 20_000)
        self.assertEqual(rows["家电"]["numerator_cents"], 12_000)
        self.assertAlmostEqual(rows["家电"]["value"], 0.6)
        self.assertEqual(rows["服饰"]["denominator_cents"], 0)
        self.assertIsNone(rows["服饰"]["value"])
        self.assertIsNone(rows["食品"]["denominator_cents"])
        self.assertIsNone(rows["食品"]["value"])

    def test_ambiguous_invalid_and_pre_effective_questions_skip_model(self):
        questions = (
            "2026-11-01 至 2026-11-03 库存效率是多少？",
            "库存周转率是多少？",
            "2026-10-31 至 2026-10-31 库存周转率是多少？",
            "2026-11-03 至 2026-11-01 库存周转率是多少？",
            "2026-11-01 至 2026-11-03 库存周转率按地区是多少？",
            "2026-11-01 至 2026-11-03 库存周转率和营销投资回报率是多少？",
            "2026-11-01 至 2026-11-03 库存周转率和退款订单率是多少？",
            "2026-11-01 至 2026-11-03 和 2026-11-05 至 2026-11-07 库存周转率对比？",
        )
        for question in questions:
            with self.subTest(question=question):
                planner, calls = self.planner({})
                with self.assertRaises(NeedsClarification):
                    planner.plan(question)
                self.assertEqual(calls, [])
                result = InsightPilot(planner, self.database, tracer=None).analyze(question)
                self.assertEqual(result["status"], "clarify")
                self.assertEqual(result["model_call_count"], 0)
                self.assertEqual(result["trajectory"][4]["outcome"], "not_reached")
                self.assertNotIn("sql", result)

    def test_unretrieved_metric_from_structured_reply_fails_before_query(self):
        reply = {"clarify": False, "metric_id": "marketing_net_roi", "start_date": "2026-11-01",
                 "end_date_exclusive": "2026-11-04", "comparison_periods": [], "region": None,
                 "dimension": None}
        planner, calls = self.planner(reply)
        result = InsightPilot(planner, self.database, tracer=None).analyze(
            "2026-11-01 至 2026-11-03 库存周转率是多少？")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "planning")
        self.assertEqual(calls[0]["allowed_ids"], ["inventory_turnover"])
        self.assertNotIn("sql", result)

    def test_scalar_turnover_aggregates_each_local_day_before_averaging(self):
        reply = {"clarify": False, "metric_id": "inventory_turnover", "start_date": "2026-11-01",
                 "end_date_exclusive": "2026-11-04", "comparison_periods": [], "region": None,
                 "dimension": None}
        planner, _ = self.planner(reply)
        result = InsightPilot(planner, self.database, tracer=None).analyze(
            "2026-11-01 至 2026-11-03 库存周转率是多少？")
        self.assertEqual(result["status"], "answered")
        row = result["rows"][0]
        self.assertEqual(row["numerator_cents"], 12_750)
        self.assertEqual(row["denominator_cents"], 20_000)
        self.assertAlmostEqual(row["value"], 0.6375)
        self.assertTrue(result["verification"]["passed"])

    def test_fixture_contains_local_boundary_and_synthetic_values_only(self):
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT value FROM data_meta WHERE key='data_version'").fetchone()[0],
                             "insightpilot-inventory-v1")
            self.assertEqual(connection.execute(
                "SELECT cogs_cents FROM inventory_cogs_events WHERE event_id='a-start'"
            ).fetchone()[0], 2_000)
            self.assertEqual(connection.execute(
                "SELECT cogs_cents FROM inventory_cogs_events WHERE event_id='a-end-excluded'"
            ).fetchone()[0], 99_999)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_daily_snapshots WHERE inventory_value_cents IS NULL"
            ).fetchone()[0], 3)
        finally:
            connection.close()

    def test_each_metric_refuses_an_incompatible_data_contract(self):
        class FixedPlan:
            def __init__(self, plan):
                self.plan_value = plan

            def plan(self, _question):
                return self.plan_value

        refund_plan = QueryPlan("approved_refund_amount_rate", "2026-11-01", "2026-11-04", None, None)
        result = InsightPilot(FixedPlan(refund_plan), self.database, tracer=None).analyze(
            "2026-11-01 至 2026-11-03 批准退款金额率是多少？")
        self.assertEqual(("clarify", "dataset"), (result["status"], result["stage"]))
        self.assertEqual(["insightpilot-s0-v1", "insightpilot-enterprise-v1", "insightpilot-multidomain-v1"],
                         result["required_data_versions"])

        base_database = create_database(Path(self.tempdir.name) / "base.sqlite")
        inventory_plan = QueryPlan("inventory_turnover", "2026-11-01", "2026-11-04", None, None)
        result = InsightPilot(FixedPlan(inventory_plan), base_database, tracer=None).analyze(
            "2026-11-01 至 2026-11-03 库存周转率是多少？")
        self.assertEqual(("clarify", "dataset"), (result["status"], result["stage"]))
        self.assertEqual(["insightpilot-inventory-v1"], result["required_data_versions"])


if __name__ == "__main__":
    unittest.main()
