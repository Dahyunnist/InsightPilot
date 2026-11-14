"""Verify MCP request cancellation reaches the cooperative analysis worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from insightpilot.mcp_server import build_mcp_server


class CancellationProbeCore:
    def __init__(self):
        self.entered = threading.Event()
        self.cancel_seen = threading.Event()
        self.finished = threading.Event()
        self.cancel_event = None

    def analyze(self, _question, *, cancel_event=None, authorized_region=None):
        self.cancel_event = cancel_event
        self.entered.set()
        try:
            if cancel_event.wait(5):
                self.cancel_seen.set()
                return {"status": "cancelled", "reason": "cancel_requested"}
            return {"status": "failed", "reason": "probe_timeout"}
        finally:
            self.finished.set()


async def verify() -> dict:
    core = CancellationProbeCore()
    app = build_mcp_server(core).streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, host="127.0.0.1")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    call_received_cancel = False
    finished = False
    cancel_observed = False
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while not server.started and asyncio.get_running_loop().time() < deadline:
            if server_task.done():
                raise RuntimeError("MCP HTTP server did not start")
            await asyncio.sleep(0.01)
        if not server.started:
            raise RuntimeError("MCP HTTP server startup timed out")
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(8.0)) as client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp", http_client=client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    request = asyncio.create_task(session.call_tool(
                        "analyze_refund", {"question": "合成 MCP 取消探针"}))
                    entered = await asyncio.wait_for(asyncio.to_thread(core.entered.wait, 5), timeout=6)
                    if not entered:
                        raise RuntimeError("analysis worker did not start")
                    request.cancel()
                    try:
                        await request
                    except asyncio.CancelledError:
                        call_received_cancel = True
                    finished = await asyncio.wait_for(asyncio.to_thread(core.finished.wait, 5), timeout=6)
                    cancel_observed = core.cancel_seen.is_set()
    finally:
        if not core.finished.is_set() and core.cancel_event is not None:
            core.cancel_event.set()
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5)
        except TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task
        listener.close()

    passed = call_received_cancel and finished and cancel_observed
    return {
        "status": "passed" if passed else "failed",
        "transport": "MCP Streamable HTTP over local TCP with synthetic ASGI app",
        "client_cancel_received": call_received_cancel,
        "core_cancel_event_observed": cancel_observed,
        "worker_finished": core.finished.is_set(),
        "model_calls": 0,
        "synthetic_data_only": True,
    }


def main() -> None:
    result = asyncio.run(verify())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
