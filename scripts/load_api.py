"""Offline API load probe. Uses a fixed planner; makes no model calls."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore


class FixedPlanner:
    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self.lock = Lock()

    def plan(self, _question: str) -> QueryPlan:
        time.sleep(self.delay_seconds)
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


def run_case(concurrency: int, delay_seconds: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="insightpilot-load-") as temp:
        root = Path(temp)
        database = create_database(root / "fixture.sqlite")
        core = InsightPilot(FixedPlanner(delay_seconds), database)
        app = create_app(core, run_store=RunStore(root / "runs.sqlite"),
                         api_token="offline-load-token", api_scopes={"refund:read"},
                         max_concurrent=8, max_requests_per_minute=1000,
                         request_timeout_seconds=10)
        headers = {"Authorization": "Bearer offline-load-token"}

        def request(index: int) -> tuple[int, float]:
            started = time.perf_counter()
            response = TestClient(app).post("/analysis", json={"question": f"退款分析 {index}"}, headers=headers)
            return response.status_code, (time.perf_counter() - started) * 1000

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(request, range(concurrency)))
        latencies = [latency for _, latency in results]
        counts: dict[str, int] = {}
        for status, _ in results:
            key = str(status)
            counts[key] = counts.get(key, 0) + 1
        return {
            "concurrency": concurrency,
            "requests": len(results),
            "status_counts": counts,
            "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95),
                           "max": round(max(latencies), 3)},
            "planner_delay_ms": round(delay_seconds * 1000, 3),
            "mode": "in_process_synthetic_sqlite_fixed_planner_no_model_calls",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=25,
                        help="deterministic planner delay used to expose concurrency (default: 25)")
    args = parser.parse_args()
    if args.delay_ms < 0:
        parser.error("--delay-ms must be non-negative")
    print(json.dumps({"cases": [run_case(count, args.delay_ms / 1000) for count in (1, 5, 20)]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
