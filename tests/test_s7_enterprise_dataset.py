import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from threading import Event, Thread

from data.enterprise_seed import create_enterprise_database, quality_report
from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.runner import execute_readonly
from insightpilot.sql import compile_query
from insightpilot.sql_policy import SQLPolicyError, validate_sql
from insightpilot.run_store import RunStore
from fastapi.testclient import TestClient


class FixedPlanner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, _question):
        return self.plan_value


class S7EnterpriseDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = create_enterprise_database(self.root / "enterprise.sqlite", order_count=5000)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_fixture_scale_quality_checks_and_deterministic_counts(self):
        report = quality_report(self.db_path)
        self.assertEqual(report["data_version"], "insightpilot-enterprise-v1")
        self.assertEqual(len(report["data_fingerprint"]), 64)
        self.assertEqual(report["snapshot_at"], "2027-03-01T00:00:00Z")
        self.assertEqual(report["row_counts"]["orders"], 5000)
        self.assertEqual(report["quality"]["orphan_refunds"], 0)
        self.assertEqual(report["quality"]["attribution_weight_sum_mismatches"], 0)
        self.assertEqual(report["quality"]["approved_over_refund_orders"], 0)
        self.assertGreater(report["quality"]["late_recorded_refunds_over_1d"], 0)
        self.assertGreater(report["quality"]["orders_without_campaign_attribution"], 0)
        connection = sqlite3.connect(self.db_path)
        try:
            latest = connection.execute("SELECT MAX(recorded_at) FROM refunds").fetchone()[0]
        finally:
            connection.close()
        self.assertLessEqual(latest, report["snapshot_at"])
        second = create_enterprise_database(self.root / "enterprise-2.sqlite", order_count=5000)
        self.assertEqual(quality_report(second)["row_counts"], report["row_counts"])
        self.assertEqual(quality_report(second)["data_fingerprint"], report["data_fingerprint"])
        self.assertEqual(quality_report(second)["data_fingerprint"], report["data_fingerprint"])

    def test_campaign_multi_touch_query_is_independently_verified(self):
        plan = QueryPlan("approved_refund_campaign_attribution", "2026-01-01", "2027-01-01", None, "campaign")
        result = InsightPilot(FixedPlanner(plan), self.db_path).analyze("campaign attribution")
        self.assertEqual(result["status"], "answered", result)
        self.assertTrue(result["verification"]["passed"], result["verification"])
        self.assertIn("未归因", [row["group_value"] for row in result["rows"]])
        self.assertAlmostEqual(sum(row["value"] for row in result["rows"]), 1.0, places=9)

    def test_campaign_metric_refuses_s0_data_before_query(self):
        old_db = create_database(self.root / "s0.sqlite")
        plan = QueryPlan("approved_refund_campaign_attribution", "2026-01-01", "2027-01-01", None, "campaign")
        result = InsightPilot(FixedPlanner(plan), old_db).analyze("campaign attribution")
        self.assertEqual(result["status"], "clarify")
        self.assertEqual(result["stage"], "dataset")

    def test_campaign_zero_denominator_and_empty_cohort_are_valid_null_cases(self):
        connection = sqlite3.connect(self.db_path)
        try:
            ordered_at = connection.execute("""SELECT substr(o.ordered_at,1,10) FROM orders o
                LEFT JOIN refunds r ON r.order_id=o.order_id AND r.status='approved'
                WHERE o.status='paid' GROUP BY substr(o.ordered_at,1,10)
                HAVING COALESCE(SUM(r.amount_cents),0)=0 LIMIT 1""").fetchone()[0]
        finally:
            connection.close()
        start = ordered_at
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        plan = QueryPlan("approved_refund_campaign_attribution", start, end, None, "campaign")
        result = InsightPilot(FixedPlanner(plan), self.db_path).analyze("question")
        self.assertEqual(result["status"], "answered", result)
        self.assertTrue(result["verification"]["passed"], result["verification"])
        self.assertTrue(all(row["value"] is None for row in result["rows"]))

        empty_plan = QueryPlan("approved_refund_campaign_attribution", "2027-01-01", "2027-01-02", None, "campaign")
        empty = InsightPilot(FixedPlanner(empty_plan), self.db_path).analyze("question")
        self.assertEqual(empty["status"], "answered", empty)
        self.assertEqual(empty["rows"], [])
        self.assertTrue(empty["verification"]["passed"])

    def test_month_and_region_groupings_are_independently_verified(self):
        for dimension in ("month_utc", "region"):
            plan = QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-11-03", None, dimension)
            result = InsightPilot(FixedPlanner(plan), create_database(self.root / f"small-{dimension}.sqlite")).analyze("question")
            self.assertEqual(result["status"], "answered", result)
            self.assertTrue(result["verification"]["passed"], result["verification"])
            if dimension == "month_utc":
                self.assertEqual(result["visualization"]["type"], "line")
            else:
                self.assertEqual(result["visualization"]["type"], "bar")

    def test_synthetic_customer_pii_decoys_remain_outside_sql_policy(self):
        with self.assertRaises(SQLPolicyError):
            validate_sql("SELECT email FROM customers LIMIT 1")
        with self.assertRaises(SQLPolicyError):
            validate_sql("SELECT phone FROM orders LIMIT 1")

    def test_concurrency_limit_and_live_cancel_survive_request_boundary(self):
        class BlockingPlanner:
            def __init__(self):
                self.entered, self.release = Event(), Event()

            def plan(self, _question):
                self.entered.set()
                self.release.wait(5)
                return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)

        planner = BlockingPlanner()
        core = InsightPilot(planner, create_database(self.root / "cancel.sqlite"))
        store = RunStore(self.root / "cancel-runs.sqlite")
        client = TestClient(create_app(core, run_store=store, api_token="token",
                                       api_scopes={"refund:read", "refund:cancel"}, max_concurrent=1))
        response_holder = []
        worker = Thread(target=lambda: response_holder.append(
            client.post("/analysis", json={"question": "slow"}, headers={"Authorization": "Bearer token"})))
        worker.start()
        self.assertTrue(planner.entered.wait(2))
        db = sqlite3.connect(store.path)
        try:
            run_id = db.execute("SELECT run_id FROM runs WHERE status='running'").fetchone()[0]
        finally:
            db.close()
        overloaded = client.post("/analysis", json={"question": "overflow"}, headers={"Authorization": "Bearer token"})
        self.assertEqual(overloaded.status_code, 429)
        cancelled = client.post(f"/runs/{run_id}/cancel", headers={"Authorization": "Bearer token"})
        self.assertEqual(cancelled.status_code, 202)
        planner.release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(response_holder[0].status_code, 409)
        self.assertEqual(response_holder[0].json()["status"], "cancelled")
        self.assertEqual(store.get(run_id)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
