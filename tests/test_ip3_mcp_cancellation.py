from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anyio

from insightpilot.mcp_server import bridge_sdk_cancellation, build_mcp_server


class IP3McpCancellationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_context():
        cancel_requested = anyio.Event()
        session = SimpleNamespace(_request_outbound=SimpleNamespace(cancel_requested=cancel_requested))
        request_context = SimpleNamespace(session=session, request_id="42")
        return SimpleNamespace(request_context=request_context), cancel_requested

    async def test_mcp_tool_returns_analysis_result_on_normal_completion(self):
        class AnswerCore:
            def __init__(self):
                self.arguments = None

            def analyze(self, question, *, cancel_event=None, authorized_region=None):
                self.arguments = (question, cancel_event, authorized_region)
                return {"status": "answered", "run_id": "mcp-run"}

        core = AnswerCore()
        context, _cancel_requested = self.make_context()
        tool = build_mcp_server(core, authorized_region="华东")._tool_manager.get_tool("analyze_refund")

        async def immediate_run_sync(function, *, abandon_on_cancel):
            self.assertTrue(abandon_on_cancel)
            return function()

        with patch("insightpilot.mcp_server.anyio.to_thread.run_sync", immediate_run_sync):
            result = await tool.fn("退款分析", context)
        self.assertEqual(result.structured_content, {"status": "answered", "run_id": "mcp-run"})
        self.assertEqual(core.arguments[0], "退款分析")
        self.assertIsNotNone(core.arguments[1])
        self.assertFalse(core.arguments[1].is_set())
        self.assertEqual(core.arguments[2], "华东")

    async def test_cancelled_mcp_tool_signals_core_worker(self):
        sdk_event = asyncio.Event()
        core_event = threading.Event()
        sdk_event.set()
        await bridge_sdk_cancellation(sdk_event, core_event)
        self.assertTrue(core_event.is_set())


if __name__ == "__main__":
    unittest.main()
