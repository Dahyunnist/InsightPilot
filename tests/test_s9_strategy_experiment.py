import json
from pathlib import Path
import tempfile
import unittest

from insightpilot.strategy_experiment import run


class S9StrategyExperimentTests(unittest.TestCase):
    def test_experiment_uses_train_pair_and_frozen_case_without_claiming_rl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            reports.mkdir()
            main = [{"case_id": f"B0{i}", "metric_ok": True, "dimension_ok": True,
                     "numeric_ok": True, "sql_verified": True, "result": {"status": "answered"}}
                    for i in range(1, 4)]
            ablated = [{**row, "numeric_ok": index == 2}
                       for index, row in enumerate(main, start=1)]
            (reports / "enterprise-evaluation-raw.jsonl").write_text(
                "\n".join(json.dumps(row) for row in main), encoding="utf-8")
            (reports / "enterprise-ablation-no-retrieval.jsonl").write_text(
                "\n".join(json.dumps(row) for row in ablated), encoding="utf-8")
            output = root / "strategy.json"
            result = run(reports, output)
            self.assertEqual(result["frozen_holdout_case_ids"], ["B03"])
            self.assertEqual(result["learned_action"], "semantic_retrieval")
            self.assertFalse(result["agentic_rl_claim_supported"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
