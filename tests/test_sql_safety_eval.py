import tempfile
import unittest
from pathlib import Path

from data.seed import create_database
from scripts.evaluate_sql_safety import evaluate, load_cases


class SqlSafetyEvaluationTests(unittest.TestCase):
    def test_frozen_cases_block_dangerous_sql_and_allow_approved_read(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = create_database(Path(temp) / "fixture.sqlite")
            report = evaluate(load_cases(), database_path)

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["case_count"], 11)
        self.assertEqual(report["passed"], 11)
        self.assertEqual(report["dangerous_blocked"], 10)
        self.assertEqual(report["dangerous_total"], 10)
        self.assertEqual(report["approved_allowed"], 1)
        self.assertEqual(report["approved_total"], 1)
        self.assertEqual(report["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
