"""Synthetic fixed-planner FastAPI service for Compose HTTP acceptance."""

from __future__ import annotations

import os
import time
from pathlib import Path

import uvicorn

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore


class FixedPlanner:
    def plan(self, _question: str) -> QueryPlan:
        time.sleep(float(os.environ.get("IP3_PLANNER_DELAY_MS", "150")) / 1000)
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
