from __future__ import annotations

import asyncio

import httpx2
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.auth import BearerScopeMiddleware
from insightpilot.metrics import required_access_scopes
from insightpilot.mcp_server import build_mcp_server
from insightpilot.run_store import RunStore
from tests.test_s6_runtime import MarketingPlanner


TOKEN = "local-test-token"
MARKETING_QUESTION = "2026-10-01 到 2026-10-31 各营销活动投资回报率是多少？"


async def _call_mcp(app, question: str) -> tuple[int, dict]:
    transport = httpx2.ASGITransport(app=app)
    responses = []

    async def capture_response(response):
        await response.aread()
        responses.append(response)

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://localhost:8000",
            headers={"Authorization": f"Bearer {TOKEN}"},
            event_hooks={"response": [capture_response]},
        ) as http_client:
            async with streamable_http_client("http://localhost:8000/mcp", http_client=http_client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    try:
                        result = await session.call_tool("analyze_refund", {"question": question})
                    except MCPError:
                        response = responses[-1]
                        return response.status_code, response.json()
                    return 200, result.structuredContent or {}


def test_api_and_real_mcp_transport_apply_the_same_question_scope(tmp_path):
    scopes = {"refund:read"}
    planner = MarketingPlanner()
    database = create_database(tmp_path / "fixture.sqlite")
    core = InsightPilot(planner, database)

    api = create_app(core, run_store=RunStore(tmp_path / "runs.sqlite"), api_token=TOKEN, api_scopes=scopes)
    api_response = TestClient(api).post(
        "/analysis", json={"question": MARKETING_QUESTION},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    mcp = build_mcp_server(core).streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, host="127.0.0.1"
    )
    mcp.add_middleware(
        BearerScopeMiddleware, token=TOKEN, scopes=scopes,
        question_scope_resolver=required_access_scopes,
    )
    mcp_status, mcp_body = asyncio.run(_call_mcp(mcp, MARKETING_QUESTION))

    api_body = api_response.json()
    mcp_error = mcp_body["error"]
    assert api_response.status_code == 403
    assert mcp_status == 403
    assert api_body["reason"] == mcp_error["reason"] == "insufficient_scope"
    assert [api_body["required_scope"]] == mcp_error["required_scopes"]
    assert not planner.called
    assert "sql" not in str(mcp_body).lower()
    assert "sql" not in str(api_body).lower()
