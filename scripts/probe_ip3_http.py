"""Measure the Compose HTTP API with synthetic requests; no model calls."""

from __future__ import annotations

import concurrent.futures
import json
import argparse
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = "http://127.0.0.1:58080"
TOKEN = "ip3-compose-synthetic-token"


def request(path: str, *, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read()), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read()), {key.lower(): value for key, value in response.headers.items()}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


def pressure(concurrency: int) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"}

    def send(index: int):
        started = time.perf_counter()
        status, body, _ = request("/analysis", payload={"question": f"合成退款压测 {concurrency}-{index}"}, headers=headers)
        return status, (time.perf_counter() - started) * 1000, body

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(send, range(concurrency)))
    counts: dict[str, int] = {}
    for status, _, _ in results:
        counts[str(status)] = counts.get(str(status), 0) + 1
    latencies = [lat for _, lat, _ in results]
    return {"concurrency": concurrency, "requests": len(results), "status_counts": counts,
            "latency_ms": {"p50": percentile(latencies, .50), "p95": percentile(latencies, .95),
                           "max": round(max(latencies), 3)},
            "success_count": counts.get("200", 0), "http_429_count": counts.get("429", 0),
            "http_503_count": counts.get("503", 0),
            "successful_responses": sum(status == 200 for status, _, _ in results),
            "mode": "real_http_synthetic_sqlite_fixed_planner_no_model_calls"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if request("/health")[0] == 200:
                break
        except (OSError, TimeoutError):
            time.sleep(.25)
    else:
        raise SystemExit("Compose API health check timed out")

    headers = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "ip3-replay-case"}
    first = request("/analysis", payload={"question": "合成退款幂等验收"}, headers=headers)
    replay = request("/analysis", payload={"question": "合成退款幂等验收"}, headers=headers)
    conflict = request("/analysis", payload={"question": "合成退款幂等冲突"}, headers=headers)
    idempotency = {"first_status": first[0], "replay_status": replay[0],
                   "same_run_id": first[1].get("run_id") == replay[1].get("run_id"),
                   "replay_header": replay[2].get("idempotency-replay"), "conflict_status": conflict[0]}
    cases = [pressure(n) for n in (1, 5, 20)]
    report = {"service": "FastAPI real HTTP; local runner because Docker Hub image pull was blocked",
              "planner": "fixed synthetic planner with 150ms delay", "backend": "synthetic SQLite",
              "model_calls": 0, "idempotency": idempotency, "cases": cases,
              "interpretation": "Synchronous HTTP requests wait for analysis results; concurrency is a burst, not async job throughput. Rejected 429s are included in latency percentiles."}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not (idempotency["first_status"] == idempotency["replay_status"] == 200 and
            idempotency["same_run_id"] and idempotency["replay_header"] == "true" and
            idempotency["conflict_status"] == 409 and
            all(case["requests"] == sum(int(case["status_counts"].get(code, 0)) for code in ("200", "429", "503"))
                for case in cases) and all(case["status_counts"].get("503", 0) == 0 for case in cases) and
            cases[2]["status_counts"].get("429", 0) > 0):
        raise SystemExit("IP-3 HTTP acceptance failed")


if __name__ == "__main__":
    main()

