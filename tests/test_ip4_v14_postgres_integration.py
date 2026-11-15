import os
import tempfile
from pathlib import Path

import pytest

from data.build_ip4_v14_candidate import build_candidate
from insightpilot.ip4_v14_evaluate import evaluate
from insightpilot.query_engine import PostgresQueryEngine
from scripts.prepare_ip4_postgres import prepare


@pytest.mark.skipif(os.environ.get("INSIGHTPILOT_RUN_IP4_V14_POSTGRES_INTEGRATION") != "1",
                    reason="requires explicit opt-in to reset the dedicated insightpilot_ip4 test database")
def test_v14_sqlite_and_postgres_tenant_scores_match():
    required = ("INSIGHTPILOT_IP4_ADMIN_DSN", "INSIGHTPILOT_IP4_APP_DSN",
                "INSIGHTPILOT_IP4_APP_ROLE", "INSIGHTPILOT_IP4_APP_PASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        pytest.skip("dedicated PostgreSQL integration settings are not configured")

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ip4-v14-postgres-") as temporary_root:
        candidate = Path(temporary_root) / "candidate"
        manifest = build_candidate(candidate, seed=20261115, retail_seed=20261116, order_count=10_000)
        prepare(candidate / "synthetic.sqlite", os.environ["INSIGHTPILOT_IP4_ADMIN_DSN"],
                os.environ["INSIGHTPILOT_IP4_APP_ROLE"], os.environ["INSIGHTPILOT_IP4_APP_PASSWORD"],
                root / "docs/postgresql-rls-bootstrap.sql")
        paths = (candidate / "synthetic.sqlite", candidate / "blind.jsonl",
                 candidate / "gold.jsonl", candidate / "manifest.json")
        sqlite_report = evaluate(*paths)
        sqlite_view = {row["case_id"]: (row["passed"], row["actual_status"], row["actual_reason"])
                       for row in sqlite_report["cases"]}
        for tenant_id in ("tenant-a", "tenant-b"):
            engine = PostgresQueryEngine(os.environ["INSIGHTPILOT_IP4_APP_DSN"], tenant_id=tenant_id)
            try:
                pg_report = evaluate(*paths, query_engine=engine, tenant_id=tenant_id)
            finally:
                engine.close()
            pg_view = {row["case_id"]: (row["passed"], row["actual_status"], row["actual_reason"])
                       for row in pg_report["cases"]}
            assert pg_view == sqlite_view, tenant_id
        assert manifest["model_calls"] == 0
