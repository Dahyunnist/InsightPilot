"""Run a model-free local HTTP smoke check against the fixed S0 fixture."""

from __future__ import annotations

import json
import math
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn

# The API module builds a default app at import time; these placeholders keep
# the custom rule-planner smoke app independent of model credentials.
os.environ.setdefault("LLM_API_KEY", "offline-smoke-not-used")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("LLM_MODEL", "offline-smoke-not-used")
os.environ.setdefault("INSIGHTPILOT_DATABASE_URL", "")

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.rule_baseline import RulePlanner
from insightpilot.run_store import RunStore


QUESTION = "2026-10-05 到 2026-10-11 华东金额退款率是多少？"
START_AT = "2026-10-05T00:00:00Z"
END_AT = "2026-10-12T00:00:00Z"
REGION = "华东"


def independent_amount_rate(database_path: Path) -> tuple[int, int, float]:
    """Calculate the fixed fixture answer with a standalone SQL query."""
    sql = """SELECT
      (SELECT COALESCE(SUM(r.amount_cents), 0)
       FROM refunds AS r JOIN orders AS o ON o.order_id = r.order_id
       WHERE o.status = 'paid' AND o.ordered_at >= ? AND o.ordered_at < ?
         AND o.region = ? AND r.status = 'approved') AS numerator_cents,
      (SELECT COALESCE(SUM(o.paid_amount_cents), 0)
       FROM orders AS o
       WHERE o.status = 'paid' AND o.ordered_at >= ? AND o.ordered_at < ?
         AND o.region = ?) AS denominator_cents"""
    params = (START_AT, END_AT, REGION, START_AT, END_AT, REGION)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        numerator, denominator = connection.execute(sql, params).fetchone()
    finally:
        connection.close()
    value = numerator / denominator if denominator else math.nan
    return numerator, denominator, value


def _get_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    with tempfile.TemporaryDirectory(prefix="insightpilot-smoke-") as temporary:
        temporary_path = Path(temporary)
        database_path = create_database(temporary_path / "insightpilot-s0.sqlite")
        core = InsightPilot(RulePlanner(), database_path)
        app = create_app(core, run_store=RunStore(temporary_path / "runs.sqlite"))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        host, port = listener.getsockname()
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        server_thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        server_thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not server.started:
                raise RuntimeError("本地 API 未能在 5 秒内启动。")

            base_url = f"http://{host}:{port}"
            health_status, health = _get_json(base_url + "/health")
            if health_status != 200 or health.get("status") != "ok":
                raise RuntimeError(f"健康检查失败：HTTP {health_status}，{health}")

            response_status, response = _get_json(base_url + "/analysis", {"question": QUESTION})
            if response_status != 200 or response.get("status") != "answered":
                raise RuntimeError(f"只读查询失败：HTTP {response_status}，{response}")
            if response.get("metric", {}).get("id") != "approved_refund_amount_rate":
                raise RuntimeError("只读查询返回了意外指标。")

            expected_numerator, expected_denominator, expected_value = independent_amount_rate(database_path)
            row = response["rows"][0]
            actual = (row["numerator_cents"], row["denominator_cents"], row["value"])
            expected = (expected_numerator, expected_denominator, expected_value)
            if actual[:2] != expected[:2] or not math.isclose(actual[2], expected[2], rel_tol=0, abs_tol=1e-12):
                raise RuntimeError(f"答案与独立 SQL 不一致：答案={actual}，独立 SQL={expected}")

            verification = response.get("verification", {})
            if not verification.get("passed"):
                raise RuntimeError("InsightPilot 内置独立复核未通过。")

            print("离线冒烟通过")
            print(f"健康检查：{health['status']}")
            print(f"查询：{QUESTION}")
            print(f"金额退款率：{expected_numerator}/{expected_denominator} = {expected_value:.12f}")
            print("答案与独立 SQL 一致；模型调用：0")
        finally:
            server.should_exit = True
            server_thread.join(timeout=5)
            if server_thread.is_alive():
                server.force_exit = True
                server_thread.join(timeout=5)
            listener.close()
            if server_thread.is_alive():
                raise RuntimeError("本地 API 未能正常关闭。")


if __name__ == "__main__":
    main()
