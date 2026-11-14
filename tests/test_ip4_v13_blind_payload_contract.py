import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from insightpilot.config import Settings
from insightpilot.metrics import retrieve_metrics
from insightpilot.planner import OpenAICompatiblePlanner


ROOT = Path(__file__).resolve().parents[1]


class IP4V13BlindPayloadContractTests(unittest.TestCase):
    def test_runner_gives_agent_only_the_blind_question(self):
        path = ROOT / "scripts/run_ip4_v13_model_blind.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "analyze"
                 and isinstance(node.func.value, ast.Call)
                 and isinstance(node.func.value.func, ast.Name)
                 and node.func.value.func.id == "InsightPilot"]

        self.assertEqual(1, len(calls))
        analyze_call = calls[0]
        self.assertEqual(1, len(analyze_call.args))
        self.assertEqual([], analyze_call.keywords)
        question = analyze_call.args[0]
        self.assertIsInstance(question, ast.Subscript)
        self.assertIsInstance(question.value, ast.Name)
        self.assertEqual("case", question.value.id)
        self.assertEqual("question", question.slice.value)

    def test_mocked_planner_requests_include_blind_inputs_but_no_gold_payload(self):
        blind_path = ROOT / "evals/ip4-v13-blind.jsonl"
        gold_path = ROOT / "evals/ip4-v13-gold.jsonl"
        blind = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line]
        gold = {row["case_id"]: row for row in (
            json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line)}
        self.assertEqual({row["case_id"] for row in blind}, set(gold))
        self.assertTrue(all(set(row) == {"case_id", "question"} for row in blind))

        requests = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{}"}}]}'

        def capture_request(request, timeout):
            body = json.loads(request.data)
            requests.append(json.loads(body["messages"][1]["content"]))
            return FakeResponse()

        settings = Settings("offline-contract-key", "https://example.invalid", "offline-contract-model",
                            1, 64, ROOT / "unused.sqlite")
        with patch("insightpilot.planner.urllib.request.urlopen", side_effect=capture_request):
            for case in blind:
                planner = OpenAICompatiblePlanner(settings)
                candidate_ids = [metric.metric_id for metric in retrieve_metrics(case["question"])]
                planner._complete(case["question"], candidate_ids, candidate_ids)

        self.assertEqual(len(blind), len(requests))
        for case, payload in zip(blind, requests):
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual({"question", "candidate_metrics", "allowed_metric_ids"}, set(payload))
                self.assertEqual(case["question"], payload["question"])
                self.assertNotIn(case["case_id"], json.dumps(payload, ensure_ascii=False))
                self.assertNotIn("expected_status", payload)
                self.assertNotIn("analysis_start", payload)
                self.assertNotIn("analysis_end_exclusive", payload)
                self.assertNotIn("expected_rows", payload)


if __name__ == "__main__":
    unittest.main()
