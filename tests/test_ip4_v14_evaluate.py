import json
import tempfile
from pathlib import Path

from data.build_ip4_v14_candidate import build_candidate, generate_cases
from insightpilot.ip4_v14_evaluate import adapt_gold, evaluate
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner
from insightpilot.query_engine import SQLiteQueryEngine


class SQLiteBackedPostgresContract:
    """Test double for evaluator tenant plumbing; does not claim live PG parity."""

    backend_name = "postgresql"
    requires_tenant_scope = True

    def __init__(self, database: Path, tenant_id: str):
        self.tenant_id = tenant_id
        self.sqlite = SQLiteQueryEngine(database)

    def read_data_identity(self, *, tenant_id=None):
        assert tenant_id == self.tenant_id
        return self.sqlite.read_data_identity()

    def execute_readonly(self, query, *, tenant_id=None, cancel_event=None):
        assert tenant_id == self.tenant_id
        rows, _backend = self.sqlite.execute_readonly(query, cancel_event=cancel_event)
        return rows, "postgresql"


def test_v14_gold_adapter_maps_fields_without_mutating_sealed_shape():
    gold = {"case_id": "A", "domain": "refunds", "expected_status": "answered",
            "metric": "approved_refund_order_rate", "dimension": None, "region": None,
            "start": "2026-11-01", "end_exclusive": "2026-11-04", "rows": []}
    adapted = adapt_gold(gold)
    assert adapted["analysis_start"] == gold["start"]
    assert adapted["analysis_end_exclusive"] == gold["end_exclusive"]
    assert adapted["expected_rows"] == gold["rows"]
    assert set(gold) == {"case_id", "domain", "expected_status", "metric", "dimension",
                         "region", "start", "end_exclusive", "rows"}


def test_v14_reason_codes_cover_frozen_clarification_cases_without_requests():
    cases = {case["case_id"]: case for case in generate_cases(20261115)}
    expected = {
        "IP4V14-R-G01": "ambiguous_metric",
        "IP4V14-R-G02": "multiple_metrics",
        "IP4V14-M-G01": "missing_time_range",
        "IP4V14-M-G02": "unsupported_region_dimension",
        "IP4V14-M-G03": "multiple_metric_definitions",
    }
    for case_id, reason in expected.items():
        planner = OpenAICompatiblePlanner(None)
        planner._complete = lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected model path"))
        try:
            planner.plan(cases[case_id]["question"])
        except NeedsClarification as exc:
            assert exc.reason == reason, case_id
        else:
            raise AssertionError(f"{case_id} did not clarify")


def test_v14_offline_scorer_matches_sqlite_and_tenant_engine_contract():
    with tempfile.TemporaryDirectory(prefix="ip4-v14-score-") as temporary_root:
        candidate = Path(temporary_root) / "candidate"
        build_candidate(candidate, seed=20261115, retail_seed=20261116, order_count=10_000)
        args = (candidate / "synthetic.sqlite", candidate / "blind.jsonl",
                candidate / "gold.jsonl", candidate / "manifest.json")
        sqlite_report = evaluate(*args)
        tenant_report = evaluate(*args, query_engine=SQLiteBackedPostgresContract(args[0], "tenant-a"),
                                 tenant_id="tenant-a")
        to_view = lambda report: {row["case_id"]: (row["passed"], row["actual_status"],
                                                    row["actual_reason"])
                                  for row in report["cases"]}
        assert sqlite_report["model_calls"] == tenant_report["model_calls"] == 0
        assert tenant_report["backend"] == "postgresql"
        assert to_view(sqlite_report) == to_view(tenant_report)
