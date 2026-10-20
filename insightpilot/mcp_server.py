"""MCP tools and metric resource over the shared guarded analysis service."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server import MCPServer

from .agent import InsightPilot
from .config import load_settings
from .metrics import METRICS, catalog_document
from .planner import OpenAICompatiblePlanner


def build_mcp_server(core: InsightPilot, contract_path: Path | None = None) -> MCPServer:
    server = MCPServer("InsightPilot", version="0.3.0", description="Read-only evidence-backed refund analysis")

    @server.tool()
    def analyze_refund(question: str) -> str:
        """Analyze one refund metric with the same read-only policy as the CLI and API."""
        result = core.analyze(question)
        return json.dumps(result, ensure_ascii=False)

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
    server = build_mcp_server(core)
    server.run(transport="streamable-http", host="127.0.0.1", port=8001, stateless_http=True)


if __name__ == "__main__":
    main()
