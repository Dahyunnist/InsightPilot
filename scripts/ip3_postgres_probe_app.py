"""Synthetic PostgreSQL API for database service outage acceptance."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
import psycopg

os.environ.setdefault("LLM_API_KEY", "ip3-db-outage-no-model")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9/v1")
os.environ.setdefault("LLM_MODEL", "ip3-db-outage-fixed-planner")
os.environ.setdefault("INSIGHTPILOT_DATABASE_URL", "")

from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.planner import QueryPlan
from insightpilot.query_engine import PostgresQueryEngine
from insightpilot.run_store import RunStore


class FixedPlanner:
    def __init__(self):
        self.total_calls = 0
        self.last_call_count = 0

    def plan(self, _question: str) -> QueryPlan:
        self.total_calls += 1
        self.last_call_count = 1
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


planner = FixedPlanner()
data_path = Path("/workspace/data/insightpilot-multidomain.sqlite")
run_root = Path("/tmp/insightpilot-ip3-db-outage")
run_root.mkdir(parents=True, exist_ok=True)
engine = PostgresQueryEngine(os.environ["IP3_TEST_DSN"])
core = InsightPilot(planner, data_path, query_engine=engine)
app = create_app(core, run_store=RunStore(run_root / "runs.sqlite"),
                 api_tenant_tokens={"ip3-db-outage-token": "tenant-a"},
                 api_scopes={"refund:read", "refund:cancel"},
                 max_concurrent=2, max_requests_per_minute=60, request_timeout_seconds=20)


@app.get("/probe/planner-calls")
def planner_calls() -> dict:
    return {"total_calls": planner.total_calls}


@app.get("/probe/pool-stats")
def pool_stats() -> dict:
    return engine._connection_pool().get_stats()


@app.get("/probe/direct-db")
def direct_db():
    try:
        with psycopg.connect(os.environ["IP3_TEST_DSN"], connect_timeout=3) as connection:
            database, user = connection.execute("SELECT current_database(), current_user").fetchone()
        return {"status": "connected", "database": database, "user": user}
    except Exception as exc:
        return {"status": "unavailable", "error_class": type(exc).__name__,
                "sqlstate": getattr(exc, "sqlstate", None)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
