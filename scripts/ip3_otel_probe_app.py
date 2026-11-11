"""Synthetic API process for isolated local Collector acceptance."""

from __future__ import annotations

import os
from pathlib import Path

os.environ["LLM_API_KEY"] = "ip3-otel-no-model"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["LLM_MODEL"] = "ip3-otel-fixed-planner"
os.environ["INSIGHTPILOT_DATABASE_URL"] = ""

data_root = Path(os.environ["IP3_OTEL_DATA_DIR"])
data_root.mkdir(parents=True, exist_ok=True)
os.environ["INSIGHTPILOT_DATABASE_PATH"] = str(data_root / "fixture.sqlite")

from data.seed import create_database
from insightpilot.planner import QueryPlan

create_database(Path(os.environ["INSIGHTPILOT_DATABASE_PATH"]))

from insightpilot.api import app


class FixedPlanner:
    def plan(self, _question: str) -> QueryPlan:
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


app.state.core.planner = FixedPlanner()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
