import hashlib
import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from data.build_ip4_v14_candidate import (
    CASE_COUNT,
    MAX_PROVIDER_REQUESTS,
    MAX_RUN_RESERVE_USD,
    _normalize,
    build_agent_payload,
    build_candidate,
    generate_cases,
)


def test_ip4_v14_catalog_has_independent_22_case_coverage():
    cases = generate_cases(20261115)
    assert len(cases) == CASE_COUNT == 22
    assert len({case["case_id"] for case in cases}) == 22
    assert len({case["question"] for case in cases}) == 22
    for domain in ("refunds", "marketing_roi"):
        scoped = [case for case in cases if case["domain"] == domain]
        assert sum("metric" in case for case in scoped) == 7
        assert sum("expected_status" in case for case in scoped) == 4
    for case in cases:
        if "metric" not in case:
            continue
        prompt_dates = re.findall(r"202[67]-\d{2}-\d{2}", case["question"])
        assert prompt_dates[0] == case["start"]
        assert prompt_dates[-1] == (date.fromisoformat(case["end"]) - timedelta(days=1)).isoformat()
        assert "（含）" in case["question"]


def test_ip4_v14_payload_contains_only_approved_planner_fields():
    for case in generate_cases(20261115):
        payload = build_agent_payload(case)
        assert set(payload) == {"question", "candidate_metrics", "allowed_metric_ids"}
        assert payload["allowed_metric_ids"] == case["candidate_metric_ids"]
        assert all("metric_id" in item and "formula" in item for item in payload["candidate_metrics"])
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        for forbidden in ("gold", "sql", "query_result", "database_rows", "expected_status", "expected_reason"):
            assert forbidden not in serialized


def test_ip4_v14_private_candidate_build_cross_checks_gold_and_never_overwrites():
    with tempfile.TemporaryDirectory(prefix="ip4-v14-acceptance-") as temporary_root:
        parent = Path(temporary_root)
        output = parent / "candidate"
        manifest = build_candidate(output, seed=20261115, retail_seed=20261116, order_count=10_000)

        assert output.is_dir()
        blind_path = output / "blind.jsonl"
        gold_path = output / "gold.jsonl"
        database_path = output / "synthetic.sqlite"
        manifest_path = output / "manifest.json"
        assert all(path.is_file() for path in (blind_path, gold_path, database_path, manifest_path))
        blind = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines()]
        gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()]
        assert len(blind) == len(gold) == 22
        assert manifest["answerable_case_count"] == 14
        assert manifest["guard_case_count"] == 8
        assert manifest["domain_counts"] == {"refunds": 11, "marketing_roi": 11}
        assert manifest["max_provider_requests"] == MAX_PROVIDER_REQUESTS == 22
        assert manifest["worst_case_reserve_usd"] == MAX_RUN_RESERVE_USD
        assert manifest["model_calls"] == 0
        assert manifest["owner_review_required_before_external_call"] is True
        assert manifest["blind_sha256"] == hashlib.sha256(blind_path.read_bytes()).hexdigest()
        assert manifest["gold_sha256"] == hashlib.sha256(gold_path.read_bytes()).hexdigest()
        assert manifest["database_sha256"] == hashlib.sha256(database_path.read_bytes()).hexdigest()
        assert manifest["data_fingerprint"]
        assert manifest["r5_exact_normalized_overlap_count"] == 0

        prior_questions = set()
        prior_path = Path(__file__).resolve().parents[1] / "evals/ip4-v13-blind.jsonl"
        if prior_path.exists():
            prior_questions = {
                _normalize(json.loads(line)["question"])
                for line in prior_path.read_text(encoding="utf-8").splitlines() if line.strip()
            }
        assert not ({_normalize(item["question"]) for item in blind} & prior_questions)

        sentinel = output / "keep.txt"
        sentinel.write_text("retain", encoding="utf-8")
        with pytest.raises(FileExistsError):
            build_candidate(output, seed=20261117, retail_seed=20261118, order_count=1_000)
        assert sentinel.read_text(encoding="utf-8") == "retain"


def test_ip4_v14_refuses_candidate_inside_public_repository(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="outside the repository"):
        build_candidate(repo / "evals/ip4-v14", seed=1, retail_seed=2, order_count=1_000)
