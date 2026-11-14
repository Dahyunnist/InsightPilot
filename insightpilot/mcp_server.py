"""MCP tools and metric resource over the shared guarded analysis service."""

from __future__ import annotations

import json
import os
from functools import partial
from pathlib import Path
from threading import Event

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent
import uvicorn

from .agent import InsightPilot, build_core
from .auth import BearerScopeMiddleware
from .config import load_settings
from .metrics import METRICS, catalog_document, required_access_scopes


async def bridge_sdk_cancellation(cancel_requested, cancel_event: Event) -> None:
    await cancel_requested.wait()
    cancel_event.set()


def build_mcp_server(core: InsightPilot, contract_path: Path | None = None,
                     authorized_region: str | None = None) -> MCPServer:
    server = MCPServer("InsightPilot", version="0.4.0", description="Read-only evidence-backed refund analysis")

    async def analyze_with_cancellation(question: str, ctx: Context) -> dict:
        # SDK 2.0.0 exposes this request-scoped event on ServerSession's outbound context.
        cancel_requested = ctx.request_context.session._request_outbound.cancel_requested
        cancel_event = Event()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(bridge_sdk_cancellation, cancel_requested, cancel_event)
            try:
                return await anyio.to_thread.run_sync(
                    partial(core.analyze, question, cancel_event=cancel_event,
                            authorized_region=authorized_region),
                    abandon_on_cancel=True,
                )
            except BaseException:
                cancel_event.set()
                raise
            finally:
                task_group.cancel_scope.cancel()

    @server.tool()
    async def analyze_refund(question: str, ctx: Context) -> CallToolResult:
        """Analyze one refund metric with the same read-only policy as the CLI and API."""
        result = await analyze_with_cancellation(question, ctx)
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


def build_streamable_http_app(core: InsightPilot, contract_path: Path | None = None,
                              authorized_region: str | None = None):
    """Build stateful HTTP sessions so cancellation notifications correlate with tool calls."""
    return build_mcp_server(core, contract_path, authorized_region).streamable_http_app(
        streamable_http_path="/mcp", stateless_http=False, host="127.0.0.1")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    core = build_core(settings)
    app = build_streamable_http_app(core, authorized_region=os.environ.get("INSIGHTPILOT_API_REGION") or None)
    app.add_middleware(BearerScopeMiddleware, token=os.environ.get("INSIGHTPILOT_API_TOKEN") or None,
                       scopes=set(filter(None, os.environ.get(
                           "INSIGHTPILOT_API_SCOPES", "refund:read,refund:cancel,marketing:read"
                       ).split(","))),
                       question_scope_resolver=required_access_scopes)
    uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)


if __name__ == "__main__":
    main()
