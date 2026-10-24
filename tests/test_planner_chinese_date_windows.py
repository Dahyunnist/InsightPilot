import unittest
import json
from pathlib import Path
from unittest.mock import patch

from insightpilot.metrics import METRICS
from insightpilot.planner import OpenAICompatiblePlanner


class ChineseDateWindowTests(unittest.TestCase):
    def test_v10_x12_em_dash_range_failure_sample_ends_on_day_seven(self):
        sample = json.loads((Path(__file__).parent / "fixtures/date-window-failure-v10-x12.json").read_text(encoding="utf-8"))
        question = sample["question"]
        planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
        planner._complete = lambda *_: {
            "clarify": False, "metric_id": "approved_refund_amount_rate",
            "start_date": sample["expected_start_date"],
            "end_date_exclusive": sample["expected_end_date_exclusive"],
            "comparison_periods": [], "region": None, "dimension": None,
        }
        with patch("insightpilot.planner.retrieve_metrics", return_value=[METRICS["approved_refund_amount_rate"]]):
            plan = planner.plan(question)
        self.assertEqual((plan.start_date, plan.end_date),
                         (sample["expected_start_date"], sample["expected_end_date_exclusive"]))

    def test_year_month_quarter_and_day_range_normalize_to_inclusive_calendar_windows(self):
        cases = [
            ("统计 2026 年 10 月每个商品品类中，至少有一笔批准退款的付款订单占比。",
             "approved_refund_order_rate", "category", None, "2026-10-01", "2026-11-01"),
            ("从 2026 年 10 月到 12 月，逐月统计有批准退款的付款订单比例。",
             "approved_refund_order_rate", "month_utc", None, "2026-10-01", "2027-01-01"),
            ("只看华东地区，按 campaign 归因权重列出 2026 年第四季度批准退款金额贡献比例。",
             "approved_refund_campaign_attribution", "campaign", "华东", "2026-10-01", "2027-01-01"),
            ("2028 年 1 月 1 日到 7 日，批准退款额占订单实付金额多少？",
             "approved_refund_amount_rate", None, None, "2028-01-01", "2028-01-08"),
        ]
        for question, metric_id, dimension, region, start, end_exclusive in cases:
            with self.subTest(question=question):
                planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)

                def complete(_question, _candidates, _allowed):
                    return {"clarify": False, "metric_id": metric_id,
                            "start_date": start, "end_date_exclusive": end_exclusive,
                            "comparison_periods": [], "region": region, "dimension": dimension}

                planner._complete = complete
                with patch("insightpilot.planner.retrieve_metrics", return_value=[METRICS[metric_id]]):
                    plan = planner.plan(question)
                self.assertEqual((plan.start_date, plan.end_date), (start, end_exclusive))


if __name__ == "__main__":
    unittest.main()
