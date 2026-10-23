import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.multidomain_seed import create_multidomain_database, quality_report
from data.build_marketing_roi_holdout import build
from insightpilot.agent import InsightPilot
from insightpilot.freeze import sha256_text
from insightpilot.marketing_oracle import calculate_marketing_net_roi
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner, QueryPlan
from insightpilot.runner import execute_readonly
from insightpilot.sql import compile_query
from insightpilot.sql_policy import SQLPolicyError, validate_sql


class FixedPlanner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, _question):
        return self.plan_value


class MultiDomainTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.database = create_multidomain_database(Path(self.root.name) / "multidomain.sqlite", order_count=1000)

    def tearDown(self):
        self.root.cleanup()

    def test_synthetic_extension_has_reproducible_edge_rows(self):
        report = quality_report(self.database)
        self.assertEqual(report["data_version"], "insightpilot-multidomain-v1")
        self.assertGreater(report["duplicate_business_events"], 0)
        self.assertGreater(report["late_spend_rows_over_1d"], 0)
        self.assertGreater(report["refund_reversal_rows"], 0)
        self.assertEqual(report["reversals_exceeding_approved_refund"], 0)
        repeated = create_multidomain_database(Path(self.root.name) / "repeated.sqlite", order_count=1000)
        other_seed = create_multidomain_database(Path(self.root.name) / "other-seed.sqlite",
                                                 seed=20261024, order_count=1000)
        self.assertEqual(report["data_fingerprint"], quality_report(repeated)["data_fingerprint"])
        self.assertNotEqual(report["data_fingerprint"], quality_report(other_seed)["data_fingerprint"])

    def test_marketing_roi_uses_join_dedup_reversal_and_independent_python_check(self):
        plan = QueryPlan("marketing_net_roi", "2026-01-01", "2027-01-01", None, "campaign")
        query = compile_query(plan)
        validate_sql(query.sql)
        rows, version = execute_readonly(self.database, query)
        result = InsightPilot(FixedPlanner(plan), self.database).analyze("marketing roi")

        self.assertEqual(version, "insightpilot-multidomain-v1")
        self.assertEqual(result["status"], "answered", result)
        self.assertTrue(result["verification"]["passed"], result["verification"])
        self.assertEqual(result["verification"]["method"], "independent_python_marketing_roi")
        self.assertEqual(rows, calculate_marketing_net_roi(plan, self.database))
        self.assertEqual(len(rows), len({row["group_value"] for row in rows}))
        self.assertIn("未归因", {row["group_value"] for row in rows})
        unattributed = next(row for row in rows if row["group_value"] == "未归因")
        self.assertEqual(unattributed["spend_cents"], 0)
        self.assertIsNone(unattributed["value"])

    def test_recorded_refund_reversal_increases_net_revenue(self):
        plan = QueryPlan("marketing_net_roi", "2026-01-01", "2027-01-01", None, "campaign")
        with_reversals = calculate_marketing_net_roi(plan, self.database)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("DELETE FROM refund_reversals")
            connection.commit()
        finally:
            connection.close()
        without_reversals = calculate_marketing_net_roi(plan, self.database)
        self.assertGreater(sum(row["net_revenue_cents"] for row in with_reversals),
                           sum(row["net_revenue_cents"] for row in without_reversals))

    def test_utc_end_is_exclusive_for_spend_events(self):
        def total_spend(start, end):
            plan = QueryPlan("marketing_net_roi", start, end, None, "campaign")
            rows, _ = execute_readonly(self.database, compile_query(plan))
            return sum(row["spend_cents"] for row in rows)

        month_total = total_spend("2026-10-01", "2026-11-01")
        before_month_end = total_spend("2026-10-01", "2026-10-31")
        self.assertGreater(month_total, before_month_end)

    def test_roi_region_request_clarifies_and_unapproved_sql_is_rejected(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_args: self.fail("unsupported marketing region reached model")
        with self.assertRaises(NeedsClarification):
            planner.plan("2026-10-01 到 2026-10-31 华东营销投资回报率")
        for sql in ("DELETE FROM campaign_spend_events", "DROP TABLE refund_reversals",
                    "SELECT customer_id FROM campaign_spend_events LIMIT 1"):
            with self.subTest(sql=sql), self.assertRaises(SQLPolicyError):
                validate_sql(sql)

    def test_planner_returns_only_catalogued_marketing_metric_plan(self):
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda _question, candidates, allowed: {
            "clarify": False, "metric_id": "marketing_net_roi", "region": None,
            "dimension": "campaign",
        }
        plan = planner.plan("2026-10-01 到 2026-10-31 各营销活动 ROI 是多少？")
        self.assertEqual(plan, QueryPlan("marketing_net_roi", "2026-10-01", "2026-11-01", None, "campaign",
                                         (("2026-10-01", "2026-11-01"),)))

    def test_dev_and_test_holdouts_freeze_questions_gold_and_data_fingerprint(self):
        output = Path(self.root.name) / "frozen-evals"
        manifest = build(self.database, output)
        path = output / "marketing-roi-manifest-v1.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["suite_id"], "marketing-roi-v1")
        self.assertEqual(stored["data_fingerprint"], quality_report(self.database)["data_fingerprint"])
        self.assertEqual(stored["exact_prompt_overlap_count"], 0)
        self.assertEqual(stored["splits"]["dev"]["case_count"], 3)
        self.assertEqual(stored["splits"]["test"]["case_count"], 4)
        for split, split_manifest in stored["splits"].items():
            blind = output / f"marketing-roi-{split}-v1.jsonl"
            gold = output / f"marketing-roi-{split}-gold-v1.jsonl"
            self.assertEqual(split_manifest["blind_sha256"], sha256_text(blind))
            self.assertEqual(split_manifest["gold_sha256"], sha256_text(gold))
        self.assertEqual(stored, manifest)


if __name__ == "__main__":
    unittest.main()
