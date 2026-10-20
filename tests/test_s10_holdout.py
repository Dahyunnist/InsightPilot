import json
from pathlib import Path
import shutil
import tempfile
import unittest

from data.build_enterprise_gold import build_gold
from data.enterprise_seed import create_enterprise_database
from insightpilot.enterprise_evaluate import _compare, _without_verification
from insightpilot.agent import InsightPilot
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner, QueryPlan


ROOT = Path(__file__).resolve().parents[1]


class S10HoldoutTests(unittest.TestCase):
    def test_v2_gold_is_independent_complete_and_not_exactly_leaked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = create_enterprise_database(root / "enterprise.sqlite")
            evals = root / "evals"
            evals.mkdir()
            blind = evals / "enterprise-blind-v2.jsonl"
            gold = evals / "enterprise-gold-v2.jsonl"
            shutil.copyfile(ROOT / "evals/enterprise-blind-v2.jsonl", blind)
            build_gold(database, gold, blind)
            cases = [json.loads(line) for line in blind.read_text(encoding="utf-8").splitlines()]
            answers = [json.loads(line) for line in gold.read_text(encoding="utf-8").splitlines()]
            manifest = json.loads((evals / "enterprise-holdout-manifest-v2.json").read_text(encoding="utf-8"))
            plans = {
                "B07": QueryPlan("approved_refund_campaign_attribution", "2026-01-15", "2026-01-16", "西南", "campaign"),
                "B08": QueryPlan("approved_refund_campaign_attribution", "2027-01-01", "2027-01-03", None, "campaign"),
                "B10": QueryPlan("approved_refund_order_rate", "2026-01-01", "2026-07-01", None, "region"),
                "B11": QueryPlan("approved_refund_event_amount", "2026-02-01", "2026-02-08", None, "refund_day_utc"),
                "B12": QueryPlan("approved_refund_campaign_attribution", "2026-01-01", "2027-01-01", "华东", "campaign"),
            }
            class FixedPlanner:
                last_call_usage = None
                last_call_latency_ms = 0
                def plan(self, question):
                    case = next(row for row in cases if row["question"] == question)
                    return plans[case["case_id"]]
            results = []
            for case in cases:
                if case["case_id"] in plans and case["case_id"] != "B10":
                    result = InsightPilot(FixedPlanner(), database).analyze(case["question"])
                    gold_row = next(row for row in answers if row["case_id"] == case["case_id"])
                    results.append(_compare(case, gold_row, result))
        self.assertEqual({row["case_id"] for row in cases}, {row["case_id"] for row in answers})
        self.assertEqual(manifest["case_count"], 11)
        self.assertEqual(manifest["exact_question_overlap_with_s4_development"], [])
        self.assertEqual(manifest["exact_question_overlap_with_prior_enterprise_evaluation"], [])
        self.assertEqual(next(row for row in answers if row["case_id"] == "B07")["expected"]["Campaign 01"]["value"], None)
        self.assertTrue(all(row["passed"] for row in results), results)

    def test_holdout_deterministic_guards_match_expected_outcomes(self):
        prompts = {
            "B04": "2026-01-01 到 2026-12-31 的退款率是多少？",
            "B05": "截至2026-12-31当时已入库且可见的批准退款金额率是多少？",
            "B06": "2026-01-01 到 2026-12-31 按地区和渠道交叉查看批准退款订单率。",
            "B13": "2026-02-30 到 2026-03-02 按月查看批准退款金额率。",
            "B14": "2026-01-01 到 2026-12-31 同时筛选华东和华南地区的批准退款金额率。",
        }
        planner = object.__new__(OpenAICompatiblePlanner)
        for question in prompts.values():
            with self.subTest(question=question), self.assertRaises(NeedsClarification):
                planner.plan(question)

    def test_expected_refusal_is_scored_and_no_verifier_ablation_keeps_preflight(self):
        case, gold = {"case_id": "B09"}, {"expected_status": "refused"}
        compared = _compare(case, gold, {"status": "refused"})
        self.assertTrue(compared["passed"])

        class NeverCallPlanner:
            last_call_usage = None
            def plan(self, _question):
                raise AssertionError("restricted request must stop before planner")

        denied = _without_verification(NeverCallPlanner(), Path("unused.sqlite"), "列出客户手机号")
        self.assertEqual(denied["status"], "refused")


if __name__ == "__main__":
    unittest.main()
