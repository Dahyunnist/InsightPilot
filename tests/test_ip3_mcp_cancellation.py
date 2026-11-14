from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from insightpilot.mcp_server import build_mcp_server


class SuspendedAwaitable:
    def __await__(self):
        yield None


class BlockingCore:
    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.cancel_event = None

    def analyze(self, _question, *, cancel_event=None, authorized_region=None):
        self.cancel_event = cancel_event
        self.started.set()
        try:
            while not cancel_event.wait(0.01):
                pass
            return {"status": "cancelled", "reason": "cancel_requested"}
        finally:
            self.finished.set()


class IP3McpCancellationTests(unittest.TestCase):
    def test_mcp_tool_returns_analysis_result_on_normal_completion(self):
        class AnswerCore:
            def __init__(self):
                self.arguments = None

            def analyze(self, question, *, cancel_event=None, authorized_region=None):
                self.arguments = (question, cancel_event, authorized_region)
                return {"status": "answered", "run_id": "mcp-run"}

        core = AnswerCore()
        tool = build_mcp_server(core, authorized_region="华东")._tool_manager.get_tool("analyze_refund")

        async def immediate_run_sync(function, *, abandon_on_cancel):
            self.assertTrue(abandon_on_cancel)
            return function()

        with patch("insightpilot.mcp_server.anyio.to_thread.run_sync", immediate_run_sync):
            coroutine = tool.fn("退款分析")
            with self.assertRaises(StopIteration) as completed:
                coroutine.send(None)

        result = completed.exception.value
        self.assertEqual(result.structured_content, {"status": "answered", "run_id": "mcp-run"})
        self.assertEqual(core.arguments[0], "退款分析")
        self.assertIsNotNone(core.arguments[1])
        self.assertFalse(core.arguments[1].is_set())
        self.assertEqual(core.arguments[2], "华东")

    def test_cancelled_mcp_tool_signals_core_worker(self):
        core = BlockingCore()
        server = build_mcp_server(core)
        tool = server._tool_manager.get_tool("analyze_refund")
        self.assertTrue(tool.is_async)
        worker = None

        async def controlled_run_sync(function, *, abandon_on_cancel):
            nonlocal worker
            self.assertTrue(abandon_on_cancel)
            worker = threading.Thread(target=function)
            worker.start()
            await SuspendedAwaitable()

        with patch("insightpilot.mcp_server.anyio.to_thread.run_sync", controlled_run_sync):
            coroutine = tool.fn("退款分析")
            self.assertIsNone(coroutine.send(None))
            self.assertTrue(core.started.wait(timeout=1))
            with self.assertRaises(asyncio.CancelledError):
                coroutine.throw(asyncio.CancelledError())

        self.assertIsNotNone(core.cancel_event)
        self.assertTrue(core.cancel_event.is_set())
        self.assertTrue(core.finished.wait(timeout=1))
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
