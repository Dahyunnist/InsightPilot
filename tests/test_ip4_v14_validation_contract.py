import hashlib
import json
from pathlib import Path

import pytest

from insightpilot.ip4_v14_evaluate import case_score_vector, compare, evaluate
from insightpilot.ip4_v14_offline import (PreflightError, _load_candidate, _verify_candidate_artifact_hashes,
                                          sha256_file)
from insightpilot.run_manifest import PUBLIC_EVENT_TYPES, validate_public_run_manifest
from scripts.normalize_ip4_public_manifests import event_coverage


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_candidate_hash_check_binds_blind_gold_and_database_bytes(tmp_path):
    files = {
        "blind.jsonl": tmp_path / "blind.jsonl",
        "gold.jsonl": tmp_path / "gold.jsonl",
        "synthetic.sqlite": tmp_path / "synthetic.sqlite",
    }
    for name, path in files.items():
        path.write_bytes(name.encode("utf-8"))
    manifest = {
        "blind_sha256": _sha(files["blind.jsonl"].read_bytes()),
        "gold_sha256": _sha(files["gold.jsonl"].read_bytes()),
        "database_sha256": _sha(files["synthetic.sqlite"].read_bytes()),
    }
    _verify_candidate_artifact_hashes(files, manifest)
    for name in files:
        original = files[name].read_bytes()
        files[name].write_bytes(b"mutated " + original)
        with pytest.raises(PreflightError, match=f"{name} hash"):
            _verify_candidate_artifact_hashes(files, manifest)
        files[name].write_bytes(original)


def test_candidate_loader_binds_manifest_hash_and_candidate_hashes(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    blind = b'{"case_id":"CASE-1","domain":"refunds","question":"synthetic"}\n'
    gold = b"sealed bytes not parsed by candidate loader\n"
    database = b"synthetic database bytes"
    files = {"blind.jsonl": candidate / "blind.jsonl", "gold.jsonl": candidate / "gold.jsonl",
             "synthetic.sqlite": candidate / "synthetic.sqlite"}
    for name, data in (("blind.jsonl", blind), ("gold.jsonl", gold), ("synthetic.sqlite", database)):
        files[name].write_bytes(data)
    manifest = {
        "suite_id": "insightpilot-ip4-v14-private-candidate",
        "status": "candidate_pending_owner_review", "synthetic_only": True, "model_calls": 0,
        "case_count": 1, "data_version": "synthetic-v1", "data_fingerprint": "a" * 64,
        "blind_sha256": _sha(blind), "gold_sha256": _sha(gold), "database_sha256": _sha(database),
    }
    manifest_path = candidate / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lock = {
        "candidate_manifest_sha256": sha256_file(manifest_path),
        "candidate_sha256": {name: sha256_file(path) for name, path in files.items()},
        "suite_id": manifest["suite_id"], "data_version": manifest["data_version"],
        "data_fingerprint": manifest["data_fingerprint"],
    }
    loaded, blind_rows, _ = _load_candidate(candidate, lock)
    assert loaded["case_count"] == len(blind_rows) == 1

    manifest["database_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lock["candidate_manifest_sha256"] = sha256_file(manifest_path)
    with pytest.raises(PreflightError, match="synthetic.sqlite hash"):
        _load_candidate(candidate, lock)


def test_scorer_rejects_line_ending_change_and_database_digest_mismatch(tmp_path):
    database = tmp_path / "synthetic.sqlite"
    blind = tmp_path / "blind.jsonl"
    gold = tmp_path / "gold.jsonl"
    manifest_path = tmp_path / "manifest.json"
    database.write_bytes(b"synthetic sqlite bytes")
    blind.write_bytes(b"{}\r\n")
    gold.write_bytes(b"{}\n")
    manifest_path.write_text(json.dumps({
        "blind_sha256": _sha(b"{}\n"), "gold_sha256": _sha(b"{}\n"),
        "database_sha256": _sha(database.read_bytes()),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="blind file hash"):
        evaluate(database, blind, gold, manifest_path)

    blind.write_bytes(b"{}\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["database_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="database file hash"):
        evaluate(database, blind, gold, manifest_path)


def test_tenant_score_vector_detects_raw_result_changes():
    case = {"case_id": "CASE-1"}
    gold = {"domain": "refunds", "expected_status": "refused",
            "expected_reason": "prompt_injection_attempt"}
    first = compare(case, gold, {"status": "refused", "reason": "prompt_injection_attempt",
                                 "rows": [{"group_value": "A", "value": 10}]})
    second = compare(case, gold, {"status": "refused", "reason": "prompt_injection_attempt",
                                  "rows": [{"group_value": "A", "value": 11}]})
    assert case_score_vector({"cases": [first]}) != case_score_vector({"cases": [second]})


def test_public_event_coverage_rejects_unrecognized_nested_fields():
    coverage = event_coverage(PUBLIC_EVENT_TYPES, [{"event": "timeout"}])
    assert coverage["timeout"] == {"count": 1, "coverage": "covered"}
    coverage["timeout"]["unexpected"] = True
    errors = validate_public_run_manifest({"schema_version": 2,
                                           "trace_event_coverage": coverage})
    assert "invalid event coverage: timeout" in errors
