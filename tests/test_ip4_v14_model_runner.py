import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from insightpilot.config import Settings
from insightpilot.ip4_v14_model_runner import (
    MAX_BUDGET_USD, MAX_PROVIDER_REQUESTS, ModelRunError, _NoRedirectHandler, _provider_call,
    dry_run, execute_model_run, write_preregistration,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(tmp_path, monkeypatch):
    import insightpilot.ip4_v14_model_runner as runner

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    cases = [{"case_id": f"CASE-{index:02d}", "domain": "refunds",
              "question": f"synthetic question {index}"}
             for index in range(MAX_PROVIDER_REQUESTS)]
    blind = "".join(json.dumps(row) + "\n" for row in cases).encode("utf-8")
    gold = b"sealed bytes hashed only by runner tests\n"
    database = b"synthetic sqlite bytes for hash gate"
    (candidate / "blind.jsonl").write_bytes(blind)
    (candidate / "gold.jsonl").write_bytes(gold)
    (candidate / "synthetic.sqlite").write_bytes(database)
    manifest = {
        "suite_id": "insightpilot-ip4-v14-private-candidate",
        "status": "candidate_pending_owner_review", "synthetic_only": True,
        "model_calls": 0, "case_count": MAX_PROVIDER_REQUESTS,
        "data_version": "synthetic-v1", "data_fingerprint": "a" * 64,
        "blind_sha256": _sha(blind), "gold_sha256": _sha(gold),
        "database_sha256": _sha(database),
    }
    (candidate / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        runner, "_payload_records",
        lambda rows, _manifest: [{"question": row["question"],
                                  "allowed_metric_ids": ["metric_alpha"]} for row in rows],
    )
    return candidate


def _settings():
    return Settings(api_key="test-api-secret", base_url="https://provider.example/v1",
                    model="test-model", timeout_seconds=1, max_completion_tokens=32,
                    database_path=Path("unused.sqlite"))


def _lock(candidate, tmp_path, monkeypatch):
    settings = _settings()
    prereg_path = tmp_path / "preregistration.json"
    prereg = write_preregistration(candidate, settings, prereg_path)
    prereg_sha = _sha(prereg_path.read_bytes())
    auth_path = tmp_path / "owner-authorization.json"
    auth_path.write_text(json.dumps({
        "schema_version": 1, "status": "approved", "external_calls_authorized": True,
        "preregistration_sha256": prereg_sha, "model_id": settings.model,
        "provider_origin_sha256": prereg["provider_origin_sha256"],
        "max_provider_requests": MAX_PROVIDER_REQUESTS,
        "max_cost_usd": float(MAX_BUDGET_USD), "reserved_usd": float(MAX_BUDGET_USD),
        "retry_policy": "none", "authorized_by": "test owner",
        "candidate_hashes_reviewed": True, "blind_and_gold_reviewed": True,
        "prompt_and_model_reviewed": True, "parity_evidence_reviewed": True,
        "budget_reviewed": True,
        "authorized_at_utc": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    budget_path = tmp_path / "budget-snapshot.json"
    budget_path.write_text(json.dumps({
        "schema_version": 1, "project_cap_usd": 1.0, "spent_usd": 0.0,
        "external_reserved_usd": 0.0, "as_of_utc": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    return settings, prereg, prereg_path, auth_path, budget_path


class _Response:
    def __init__(self, content=None, extra=None):
        payload = {"choices": [{"message": {"content": json.dumps(
            content or {"selected_metric_ids": ["metric_alpha"]})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
                      "secret": "must not persist"}}
        if extra:
            payload.update(extra)
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_default_dry_run_locks_candidate_without_calls(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, _prereg, prereg_path, *_ = _lock(candidate, tmp_path, monkeypatch)
    result = dry_run(candidate, prereg_path, settings)
    assert result["external_calls_authorized"] is False
    assert result["model_calls"] == result["provider_requests"] == 0
    assert result["case_count"] == MAX_PROVIDER_REQUESTS


def test_preregistration_hash_mismatch_blocks_before_any_request(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, prereg, prereg_path, *_ = _lock(candidate, tmp_path, monkeypatch)
    prereg["candidate_sha256"]["gold.jsonl"] = "0" * 64
    prereg_path.write_text(json.dumps(prereg), encoding="utf-8")
    with pytest.raises(ModelRunError, match="hashes or policy"):
        dry_run(candidate, prereg_path, settings)


def test_request_body_has_only_two_user_fields_and_rejects_extras(monkeypatch):
    captured = []

    def opener(request, timeout):
        assert timeout == 30
        body = json.loads(request.data)
        payload = json.loads(body["messages"][1]["content"])
        assert set(payload) == {"question", "allowed_metric_ids"}
        assert body["max_tokens"] == 64
        captured.append(payload)
        return _Response()

    selected, usage = _provider_call(_settings(),
                                     {"question": "synthetic", "allowed_metric_ids": ["metric_alpha"]},
                                     opener=opener)
    assert selected == ["metric_alpha"]
    assert usage == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    assert len(captured) == 1
    with pytest.raises(ValueError):
        _provider_call(_settings(), {"question": "synthetic", "allowed_metric_ids": ["metric_alpha"],
                                     "gold": "forbidden"}, opener=opener)
    assert len(captured) == 1


def test_provider_redirects_are_not_followed():
    assert _NoRedirectHandler().redirect_request(None, None, 302, "Found", {},
                                                 "https://other.example/collect") is None


def test_budget_rejection_happens_before_request_and_full_reserve(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, _, prereg_path, auth_path, budget_path = _lock(candidate, tmp_path, monkeypatch)
    snapshot = json.loads(budget_path.read_text(encoding="utf-8"))
    snapshot["project_cap_usd"] = float(MAX_BUDGET_USD) - 0.000001
    budget_path.write_text(json.dumps(snapshot), encoding="utf-8")
    state = tmp_path / "state"
    output = tmp_path / "report.json"
    calls = []
    with pytest.raises(ModelRunError, match="full v14 reservation"):
        execute_model_run(candidate, prereg_path, auth_path, budget_path, state, output,
                          settings, opener=lambda *_args, **_kwargs: calls.append(True))
    assert calls == []
    journal = state / "ip4-v14-reservations.json"
    assert not journal.exists()


def test_timeout_is_recorded_redacted_and_never_retried(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, _, prereg_path, auth_path, budget_path = _lock(candidate, tmp_path, monkeypatch)
    output = tmp_path / "timeout-report.json"
    calls = []

    def timeout(*_args, **_kwargs):
        calls.append(True)
        raise TimeoutError("secret prompt echo")

    report = execute_model_run(candidate, prereg_path, auth_path, budget_path,
                               tmp_path / "timeout-state", output, settings, opener=timeout)
    text = output.read_text(encoding="utf-8")
    assert report["status"] == "timed_out"
    assert report["requests_attempted"] == report["model_calls"] == 1
    assert report["retry_count"] == 0
    assert report["reserved_usd"] == float(MAX_BUDGET_USD)
    assert len(calls) == 1
    assert "synthetic question" not in text
    assert "test-api-secret" not in text
    assert "provider.example" not in text
    assert "secret prompt echo" not in text


def test_full_run_redacts_output_and_same_lock_cannot_replay(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, _, prereg_path, auth_path, budget_path = _lock(candidate, tmp_path, monkeypatch)
    output = tmp_path / "complete-report.json"
    calls = []

    def responder(request, timeout):
        calls.append(True)
        payload = json.loads(request.data)
        assert set(json.loads(payload["messages"][1]["content"])) == {"question", "allowed_metric_ids"}
        return _Response(extra={"echo": "private provider text"})

    state = tmp_path / "complete-state"
    report = execute_model_run(candidate, prereg_path, auth_path, budget_path,
                               state, output, settings, opener=responder)
    assert report["status"] == "completed"
    assert report["requests_attempted"] == MAX_PROVIDER_REQUESTS
    assert report["provider_responses_received"] == MAX_PROVIDER_REQUESTS
    assert len(calls) == MAX_PROVIDER_REQUESTS
    journal = json.loads((state / "ip4-v14-reservations.json").read_text(encoding="utf-8"))
    assert journal["reservations"][0]["reserved_usd"] == float(MAX_BUDGET_USD)
    output_text = output.read_text(encoding="utf-8")
    for forbidden in ("synthetic question", "test-api-secret", "provider.example", "private provider text"):
        assert forbidden not in output_text

    with pytest.raises(ModelRunError, match="already has a reservation"):
        execute_model_run(candidate, prereg_path, auth_path, budget_path,
                          state, tmp_path / "replay-report.json", settings, opener=responder)
    assert len(calls) == MAX_PROVIDER_REQUESTS


def test_provider_unexpected_response_fields_are_rejected_and_redacted(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, monkeypatch)
    settings, _, prereg_path, auth_path, budget_path = _lock(candidate, tmp_path, monkeypatch)
    output = tmp_path / "invalid-response-report.json"
    calls = []

    def responder(*_args, **_kwargs):
        calls.append(True)
        return _Response(content={"selected_metric_ids": [], "question": "raw private question"})

    report = execute_model_run(candidate, prereg_path, auth_path, budget_path,
                               tmp_path / "invalid-response-state", output, settings, opener=responder)
    text = output.read_text(encoding="utf-8")
    assert report["status"] == "invalid_response_or_preflight"
    assert report["requests_attempted"] == report["provider_responses_received"] == 1
    assert report["retry_count"] == 0
    assert len(calls) == 1
    assert "raw private question" not in text
    assert '"question"' not in text
