"""Synthetic fixed-planner FastAPI service for Compose HTTP acceptance."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import uvicorn

from data.seed import create_database
from insightpilot.agent import InsightPilot

# Importing the API module builds its unused default app; keep this fixture
# bootable without model settings because the served app uses FixedPlanner.
os.environ.setdefault("LLM_API_KEY", "ip3-fixed-planner-no-model")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("LLM_MODEL", "ip3-fixed-planner-no-model")
os.environ.setdefault("INSIGHTPILOT_DATABASE_URL", "")

from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore


class ProbeSignals:
class ProbeSignals:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._planner_starts = 0
        self._cancel_plans_started = 0

    def planner_started(self) -> None:
        with self._lock:
            self._planner_starts += 1

    def planner_start_count(self) -> int:
        with self._lock:
            return self._planner_starts

    def mark_cancel_plan_started(self) -> None:
        with self._lock:
            self._cancel_plans_started += 1

    def cancel_plans_started(self) -> int:
        with self._lock:
            return self._cancel_plans_started


probe_signals = ProbeSignals()
signals = probe_signals


def planner_start_status() -> dict[str, int]:
    return {"count": signals.planner_start_count()}


class FixedPlanner:
    def plan(self, question: str) -> QueryPlan:
        signals.planner_started()
        if question == "合成取消可靠性探针":
            probe_signals.mark_cancel_plan_started()
            delay_ms = float(os.environ.get("IP3_CANCEL_DELAY_MS", "1500"))
        else:
            delay_ms = float(os.environ.get("IP3_PLANNER_DELAY_MS", "150"))
        time.sleep(delay_ms / 1000)
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


root = Path(os.environ.get("IP3_DATA_DIR", "/tmp/insightpilot-ip3"))
root.mkdir(parents=True, exist_ok=True)
database = create_database(root / "fixture.sqlite")
core = InsightPilot(FixedPlanner(), database)
app = create_app(
    core,
    run_store=RunStore(root / "runs.sqlite"),
    api_token="ip3-compose-synthetic-token",
    api_scopes={"refund:read", "refund:cancel"},
    max_concurrent=8,
    max_requests_per_minute=1000,
    request_timeout_seconds=5,
)
app.add_api_route(
    "/_probe/planner-start-count",
    planner_start_status,
    methods=["GET"],
    include_in_schema=False,
)


def cancellation_start_count() -> dict[str, int]:
    return {"started_count": probe_signals.cancel_plans_started()}


app.add_api_route("/_probe/cancellation-start-count", cancellation_start_count,
                  methods=["GET"], include_in_schema=False)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
