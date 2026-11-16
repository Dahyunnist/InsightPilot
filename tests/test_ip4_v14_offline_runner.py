import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from data.build_ip4_v14_candidate import build_candidate
from insightpilot.ip4_v14_offline import (
    EGRESS_WORKER,
    ROOT,
    PreflightError,
    create_offline_lock,
    run_offline,
    validate_payload,
    verify_result,
    write_result,
)


def _candidate(parent: Path) -> Path:
    path = parent / "synthetic-candidate"
    build_candidate(path, seed=20261115, retail_seed=20261116, order_count=10_000)
    return path


def test_v14_offline_runner_scores_synthetic_fixture_and_never_enables_network():
    with tempfile.TemporaryDirectory(prefix="ip4-v14-offline-runner-") as directory:
        candidate = _candidate(Path(directory))
        lock = create_offline_lock(candidate)
        assert lock["status"] == "offline_only_not_authorized"
        assert lock["external_calls_authorized"] is False
        assert lock["network_access"] == "disabled"
        result = run_offline(candidate, lock)
        assert result["status"] == "offline_preflight_passed"
        assert result["model_calls"] == result["network_calls"] == 0
        assert result["outbound_contract"]["records"] == 22
        assert result["outbound_contract"]["fields"] == ["question", "allowed_metric_ids"]
        assert result["offline_baseline"]["case_count"] == 22
        assert result["candidate_sha256"]["sealed_data"] == lock["candidate_sha256"]["gold.jsonl"]


@pytest.mark.parametrize("payload", [
    {"question": "Q", "allowed_metric_ids": ["refund_rate"], "gold_path": "C:\\private\\gold.jsonl"},
    {"question": "Q", "allowed_metric_ids": ["refund_rate"], "sql": "SELECT 1"},
    {"question": "Q", "allowed_metric_ids": ["refund_rate"], "query_results": []},
    {"question": "C:\\private\\gold.jsonl", "allowed_metric_ids": ["refund_rate"]},
    {"question": "Q", "allowed_metric_ids": ["refund_rate"], "candidate_metrics": []},
])
def test_v14_payload_rejects_gold_sql_query_result_path_and_unapproved_fields(payload):
    with pytest.raises(PreflightError):
        validate_payload(payload)


def test_independent_worker_rejects_paths_and_sensitive_fields():
    bad_payloads = [
        {"question": "Q", "allowed_metric_ids": ["refund_rate"], "gold": {"rows": []}},
        {"question": "Q", "allowed_metric_ids": ["refund_rate"], "source_path": "C:\\secret"},
        {"question": "/private/gold.jsonl", "allowed_metric_ids": ["refund_rate"]},
    ]
    for payload in bad_payloads:
        result = subprocess.run(
            [sys.executable, str(EGRESS_WORKER)], input=json.dumps(payload), text=True,
            capture_output=True, check=False,
        )
        assert result.returncode == 2
        assert result.stdout == ""


def test_egress_worker_has_no_filesystem_or_network_capabilities_in_its_code():
    sources = [EGRESS_WORKER, ROOT / "scripts/run_ip4_v14_offline.py",
               ROOT / "insightpilot/ip4_v14_offline.py"]
    forbidden_modules = {"socket", "urllib", "http", "httpx", "requests"}
    for source in sources[1:]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert not imports & forbidden_modules
    worker_tree = ast.parse(sources[0].read_text(encoding="utf-8"))
    worker_imports, worker_calls = set(), set()
    for node in ast.walk(worker_tree):
        if isinstance(node, ast.Import):
            worker_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            worker_imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            worker_calls.add(name)
    assert not worker_imports & {"os", "pathlib", "socket", "urllib", "http", "httpx", "requests"}
    assert not worker_calls & {"open", "urlopen", "connect", "create_connection", "request"}


def test_v14_runner_fails_closed_on_changed_dataset_prompt_source_and_result():
    with tempfile.TemporaryDirectory(prefix="ip4-v14-offline-lock-") as directory:
        candidate = _candidate(Path(directory))
        lock = create_offline_lock(candidate)
        prompt_lock = dict(lock, prompt_sha256="0" * 64)
        with pytest.raises(PreflightError, match="prompt hash"):
            run_offline(candidate, prompt_lock)

        (candidate / "gold.jsonl").write_text("{}\n", encoding="utf-8")
        with pytest.raises(PreflightError, match="dataset hash"):
            run_offline(candidate, lock)

        clean_candidate = _candidate(Path(directory) / "second")
        clean_lock = create_offline_lock(clean_candidate)
        report = run_offline(clean_candidate, clean_lock)
        report["gold_path"] = "C:\\private\\gold.jsonl"
        with pytest.raises(PreflightError):
            verify_result(report, clean_lock)


def test_v14_runner_refuses_to_overwrite_result_file():
    with tempfile.TemporaryDirectory(prefix="ip4-v14-offline-output-") as directory:
        candidate = _candidate(Path(directory))
        lock = create_offline_lock(candidate)
        report = run_offline(candidate, lock)
        output = Path(directory) / "prior-report.json"
        output.write_text("keep", encoding="utf-8")
        with pytest.raises(PreflightError, match="refusing overwrite"):
            write_result(report, output, lock)
        assert output.read_text(encoding="utf-8") == "keep"
