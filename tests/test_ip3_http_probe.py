from __future__ import annotations

import threading

from scripts import probe_ip3_http as probe


def _run_cancel_probe_with_fake_http(monkeypatch) -> str:
    run_id = "synthetic-run-id"
    caller = threading.current_thread()
    initial_started = threading.Event()
    release_initial = threading.Event()
    seen_keys: list[str] = []
    ordered_analysis_callers: list[str] = []
    started_count = 0
    lock = threading.Lock()

    def fake_request(path, *, payload=None, headers=None, method=None):
        nonlocal started_count
        if path == "/_probe/cancellation-start-count":
            with lock:
                return (200, {"started_count": started_count}, {})
        if path == "/analysis":
            key = headers["Idempotency-Key"]
            with lock:
                seen_keys.append(key)
            if threading.current_thread() is caller:
                ordered_analysis_callers.append("main")
                assert initial_started.wait(1), "initial request did not start"
                return (202, {"run_id": run_id, "status": "running"},
                        {"idempotency-replay": "true"})
            ordered_analysis_callers.append("worker")
            with lock:
                started_count += 1
            initial_started.set()
            assert release_initial.wait(1), "cancel did not release initial request"
            return (409, {"run_id": run_id, "status": "cancelled"},
                    {"x-request-id": run_id})
        if path == f"/runs/{run_id}/cancel":
            release_initial.set()
            return (202, {"run_id": run_id, "status": "cancellation_requested"},
                    {"x-request-id": run_id})
        if path == f"/runs/{run_id}":
            return (200, {"run_id": run_id, "status": "cancelled",
                          "cancel_requested": True}, {})
        raise AssertionError(f"unexpected HTTP path: {path}")

    monkeypatch.setattr(probe, "request", fake_request)
    result = probe.cancellation_case()

    assert result["running_replay_status"] == 202
    assert result["replay_header"] == "true"
    assert result["cancel_status"] == 202
    assert result["final_status"] == "cancelled"
    assert result["persisted_status"] == "cancelled"
    assert len(seen_keys) == 2
    assert seen_keys[0] == seen_keys[1]
    assert ordered_analysis_callers == ["worker", "main"]
    return seen_keys[0]


def test_cancellation_probe_uses_fresh_key_and_observes_replay_before_cancel(monkeypatch):
    first_key = _run_cancel_probe_with_fake_http(monkeypatch)
    second_key = _run_cancel_probe_with_fake_http(monkeypatch)

    assert first_key.startswith("ip3-matrix-cancel-")
    assert second_key.startswith("ip3-matrix-cancel-")
    assert first_key != second_key
