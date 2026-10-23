"""MCP tools and metric resource over the shared guarded analysis service."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
import uvicorn

from .agent import InsightPilot
from .auth import BearerScopeMiddleware
from .config import load_settings
from .metrics import METRICS, catalog_document, required_access_scopes
from .planner import OpenAICompatiblePlanner


def build_mcp_server(core: InsightPilot, contract_path: Path | None = None,
                     authorized_region: str | None = None) -> MCPServer:
    server = MCPServer("InsightPilot", version="0.4.0", description="Read-only evidence-backed refund analysis")

    @server.tool()
    def analyze_refund(question: str) -> CallToolResult:
        """Analyze one refund metric with the same read-only policy as the CLI and API."""
        result = core.analyze(question, authorized_region=authorized_region)
        serialized = json.dumps(result, ensure_ascii=False)
        return CallToolResult(content=[TextContent(type="text", text=serialized)], structuredContent=result,
                              isError=result.get("status") in ("failed", "refused", "cancelled"))

    @server.resource("metrics://refund-catalog")
    def refund_metric_catalog() -> str:
        """Versioned metric definitions available to the analyzer."""
        return json.dumps(catalog_document(), ensure_ascii=False, indent=2)

    @server.resource("docs://product-contract")
    def product_contract() -> str:
        """Business definitions and query boundaries for the synthetic dataset."""
        path = contract_path or Path(__file__).resolve().parents[1] / "docs" / "product-contract.md"
        return path.read_text(encoding="utf-8")

    return server


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    core = InsightPilot(OpenAICompatiblePlanner(settings), settings.database_path)
    server = build_mcp_server(core, authorized_region=os.environ.get("INSIGHTPILOT_API_REGION") or None)
    app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="127.0.0.1")
    app.add_middleware(BearerScopeMiddleware, token=os.environ.get("INSIGHTPILOT_API_TOKEN") or None,
                       scopes=set(filter(None, os.environ.get(
                           "INSIGHTPILOT_API_SCOPES", "refund:read,refund:cancel,marketing:read"
                       ).split(","))),
                       question_scope_resolver=required_access_scopes)
    uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)


if __name__ == "__main__":
    main()
