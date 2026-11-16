"""Measure the Compose HTTP API with synthetic requests; no model calls."""

from __future__ import annotations

import concurrent.futures
import json
import argparse
import time
import urllib.error
import urllib.request
from uuid import uuid4
from pathlib import Path


BASE = "http://127.0.0.1:58080"
TOKEN = "ip3-compose-synthetic-token"


def new_idempotency_key(scenario: str) -> str:
    return f"ip3-matrix-{scenario}-{uuid4().hex}"


def request(path: str, *, payload: dict | None = None, headers: dict | None = None,
            method: str | None = None) -> tuple[int, dict, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method=method or ("GET" if data is None else "POST"))
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read()), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read()), {key.lower(): value for key, value in response.headers.items()}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


def summarize_pressure(concurrency: int, results: list[tuple[int, float, dict, dict]]) -> dict:
    counts: dict[str, int] = {}
    rejection_reasons: dict[str, int] = {}
    for status, _latency, _body, _headers in results:
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
        if status == 429:
            reason = _body.get("reason")
            reason = reason if reason in {"requests_per_minute_limit", "concurrency_limit"} else "other"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    latencies = [latency for _, latency, _, _ in results]
    run_evidence = [{
        "request_index": index,
        "http_status": status,
        "run_id": body.get("run_id"),
        "request_id_matches": bool(body.get("run_id") and headers.get("x-request-id") == body.get("run_id")),
    } for index, (status, _latency, body, headers) in enumerate(results)]
    run_ids = [item["run_id"] for item in run_evidence if item["run_id"]]
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "status_counts": counts,
        "latency_ms": {"p50": percentile(latencies, .50), "p95": percentile(latencies, .95),
                       "max": round(max(latencies), 3)},
        "success_count": counts.get("200", 0),
        "http_429_count": counts.get("429", 0),
        "http_503_count": counts.get("503", 0),
        "http_429_reason_counts": rejection_reasons,
        "run_id_evidence": run_evidence,
        "run_id_count": len(run_ids),
        "unique_run_id_count": len(set(run_ids)),
        "request_id_match_count": sum(item["request_id_matches"] for item in run_evidence),
        "mode": "real_http_synthetic_sqlite_fixed_planner_no_model_calls",
    }


def pressure(concurrency: int) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"}

    def send(index: int):
        started = time.perf_counter()
        status, body, response_headers = request(
            "/analysis", payload={"question": f"合成可靠性压测 {concurrency}-{index}"}, headers=headers)
        return status, (time.perf_counter() - started) * 1000, body, response_headers

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(send, range(concurrency)))
    return summarize_pressure(concurrency, results)


def idempotency_case() -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}",
               "Idempotency-Key": new_idempotency_key("idempotency")}
    first = request("/analysis", payload={"question": "合成幂等可靠性探针"}, headers=headers)
    replay = request("/analysis", payload={"question": "合成幂等可靠性探针"}, headers=headers)
    conflict = request("/analysis", payload={"question": "合成幂等冲突探针"}, headers=headers)
    ids = [first[1].get("run_id"), replay[1].get("run_id")]
    return {
        "first_status": first[0],
        "replay_status": replay[0],
        "same_run_id": bool(ids[0] and ids[0] == ids[1]),
        "run_id": ids[0],
        "replay_header": replay[2].get("idempotency-replay"),
        "request_id_matches": all(item[1].get("run_id") == item[2].get("x-request-id") for item in (first, replay, conflict)),
        "conflict_status": conflict[0],
        "conflict_run_id": conflict[1].get("run_id"),
    }


def cancellation_case() -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}",
               "Idempotency-Key": new_idempotency_key("cancel")}
    payload = {"question": "合成取消可靠性探针"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        baseline_response = request("/_probe/cancellation-start-count")
        baseline_count = baseline_response[1].get("started_count")
        if baseline_response[0] != 200 or not isinstance(baseline_count, int):
            raise RuntimeError("synthetic cancellation planner-start signal is unavailable")
        initial = pool.submit(request, "/analysis", payload=payload, headers=headers)
        start_deadline = time.monotonic() + 3
        while time.monotonic() < start_deadline:
            if initial.done():
                early_status, early_body, _early_headers = initial.result()
                raise RuntimeError(
                    "synthetic cancellation request completed before planner start was observed "
                    f"(http_status={early_status}, stage={early_body.get('stage')}, "
                    f"reason={early_body.get('reason')})")
            started = request("/_probe/cancellation-start-count")
            if (started[0] == 200 and isinstance(started[1].get("started_count"), int)
                    and started[1]["started_count"] > baseline_count):
                break
            time.sleep(.025)
        else:
            raise TimeoutError("synthetic cancellation planner did not start before replay observation")

        replay_deadline = time.monotonic() + 1
        replay = None
        while time.monotonic() < replay_deadline:
            if initial.done():
                early_status, early_body, _early_headers = initial.result()
                raise RuntimeError(
                    "synthetic cancellation request completed before replay observation "
                    f"(http_status={early_status}, stage={early_body.get('stage')}, "
                    f"reason={early_body.get('reason')})")
            replay = request("/analysis", payload=payload, headers=headers)
            if replay[0] == 202 and replay[1].get("status") == "running":
                break
            time.sleep(.025)
        if replay is None or replay[0] != 202 or replay[1].get("status") != "running":
            raise TimeoutError("synthetic cancellation request did not enter running state")
        run_id = replay[1].get("run_id")
        cancel = request(f"/runs/{run_id}/cancel", payload={}, headers=headers, method="POST")
        final = initial.result(timeout=8)
        state = request(f"/runs/{run_id}", headers=headers)
    return {
        "running_replay_status": replay[0],
        "run_id": run_id,
        "replay_header": replay[2].get("idempotency-replay"),
        "cancel_status": cancel[0],
        "cancel_run_id_matches": cancel[1].get("run_id") == run_id,
        "final_http_status": final[0],
        "final_status": final[1].get("status"),
        "final_run_id_matches": final[1].get("run_id") == run_id,
        "persisted_status": state[1].get("status"),
        "cancel_requested": state[1].get("cancel_requested"),
    }


def matrix_passes(idempotency: dict, cases: list[dict], cancellation: dict) -> bool:
    return bool(
        idempotency["first_status"] == idempotency["replay_status"] == 200
        and idempotency["same_run_id"] and idempotency["replay_header"] == "true"
        and idempotency["conflict_status"] == 409 and idempotency["request_id_matches"]
        and all(case["requests"] == sum(int(case["status_counts"].get(code, 0))
                                         for code in ("200", "429", "503")) for case in cases)
        and all(case["status_counts"].get("503", 0) == 0 and
                case["run_id_count"] == case["requests"] and
                case["request_id_match_count"] == case["requests"] for case in cases)
        and cases[2]["status_counts"].get("429", 0) > 0
        and cancellation["running_replay_status"] == 202
        and cancellation["replay_header"] == "true"
        and cancellation["cancel_status"] == 202 and cancellation["cancel_run_id_matches"]
        and cancellation["final_http_status"] == 409 and cancellation["final_status"] == "cancelled"
        and cancellation["final_run_id_matches"] and cancellation["persisted_status"] == "cancelled"
        and cancellation["cancel_requested"] is True
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    deadline = time.monotonic() + 30
    last_health_error = "没有收到健康响应"
    while time.monotonic() < deadline:
        try:
            if request("/health")[0] == 200:
                break
        except (OSError, TimeoutError) as exc:
            last_health_error = f"{type(exc).__name__}: {exc}"
            time.sleep(.25)
    else:
        raise SystemExit(
            f"IP-3 API 在 {BASE}/health 30 秒内未就绪；最近错误：{last_health_error}。"
            "先运行 `scripts/run_ip3_compose.ps1`；若 Docker 不可用，改跑 "
            "`.\\.venv\\Scripts\\python.exe scripts\\smoke_local.py`。本机冒烟只验证功能，不代表容器性能。"
        )

    idempotency = idempotency_case()
    cases = [pressure(n) for n in (1, 5, 20)]
    cancellation = cancellation_case()
    report = {"service": "FastAPI real HTTP via Docker Compose",
              "planner": "fixed synthetic planner with 150ms delay", "backend": "synthetic SQLite",
              "model_calls": 0, "idempotency": idempotency, "cancellation": cancellation,
              "cases": cases,
              "interpretation": "Synchronous HTTP requests wait for analysis results; concurrency is a burst, not async job throughput. Rejected 429s are included in latency percentiles. Report includes only statuses and run-ID evidence, not answers, rows, SQL, or bound parameters."}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not matrix_passes(idempotency, cases, cancellation):
        raise SystemExit("IP-3 HTTP acceptance failed")


if __name__ == "__main__":
    main()

