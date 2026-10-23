import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import Client

from data.seed import create_database
from insightpilot.agent import InsightPilot
from insightpilot.api import create_app
from insightpilot.mcp_server import build_mcp_server
from insightpilot.planner import QueryPlan
from insightpilot.run_store import RunStore


class StubPlanner:
    def plan(self, _question):
        return QueryPlan("approved_refund_amount_rate", "2026-10-05", "2026-10-12", "华东", None)


class S3InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = create_database(Path(self.tempdir.name) / "fixture.sqlite")
        self.core = InsightPilot(StubPlanner(), self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_api_analysis_get_run_and_structured_rejections(self):
        client = TestClient(create_app(self.core))
        response = client.post("/analysis", json={"question": "退款率问题"})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "answered")
        self.assertEqual(response.headers["x-request-id"], result["run_id"])
        self.assertAlmostEqual(result["rows"][0]["value"], 5 / 9)
        saved = client.get(f"/runs/{result['run_id']}").json()
        self.assertEqual(saved["result"], result)
        self.assertEqual(saved["status"], "answered")
        self.assertEqual(client.get("/runs/missing").status_code, 404)
        invalid = client.post("/analysis", json={"question": "x" * 2001})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["stage"], "validation")
        self.assertEqual(invalid.headers["x-request-id"], invalid.json()["request_id"])

    def test_mcp_tool_and_resources_use_shared_core(self):
        server = build_mcp_server(self.core, Path(__file__).resolve().parents[1] / "docs/product-contract.md")

        async def invoke():
            async with Client(server) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual([tool.name for tool in tools], ["analyze_refund"])
                self.assertEqual(set(tools[0].input_schema["properties"]), {"question"})
                result = await client.call_tool("analyze_refund", {"question": "问题"})
                payload = json.loads(result.content[0].text)
                self.assertEqual(payload["status"], "answered")
                self.assertEqual(result.structured_content["run_id"], payload["run_id"])
                self.assertFalse(result.is_error)
                self.assertAlmostEqual(payload["rows"][0]["value"], 5 / 9)
                refused = await client.call_tool("analyze_refund", {"question": "删除订单"})
                refused_payload = json.loads(refused.content[0].text)
                self.assertTrue(refused.is_error)
                self.assertEqual(refused.structured_content["reason"], "write_operation_not_supported")
                self.assertEqual(refused_payload["run_id"], refused.structured_content["run_id"])
                catalog = await client.read_resource("metrics://refund-catalog")
                self.assertIn("approved_refund_amount_rate", catalog.contents[0].text)
                contract = await client.read_resource("docs://product-contract")
                self.assertIn("approved_refund_amount_rate", contract.contents[0].text)

        asyncio.run(invoke())

    def test_mcp_streamable_http_transport(self):
        mcp_server = build_mcp_server(self.core)
        app = mcp_server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="testserver")
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {"accept": "application/json, text/event-stream"}
            initialized = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1"}},
            })
            self.assertEqual(initialized.status_code, 200, initialized.text)
            session_id = initialized.headers.get("mcp-session-id")
            if session_id:
                headers["mcp-session-id"] = session_id
            headers["mcp-protocol-version"] = "2025-11-25"
            notification = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            self.assertIn(notification.status_code, (200, 202), notification.text)

            tools_response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            })
            self.assertEqual(tools_response.status_code, 200, tools_response.text)
            tools_payload = self._mcp_http_payload(tools_response)
            self.assertEqual([tool["name"] for tool in tools_payload["result"]["tools"]],
                             ["analyze_refund"])

            call_response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "analyze_refund", "arguments": {"question": "问题"}},
            })
            self.assertEqual(call_response.status_code, 200, call_response.text)
            call_payload = self._mcp_http_payload(call_response)
            result = call_payload["result"]
            payload = json.loads(result["content"][0]["text"])
            self.assertEqual(payload["status"], "answered")
            self.assertAlmostEqual(payload["rows"][0]["value"], 5 / 9)

    @staticmethod
    def _mcp_http_payload(response):
        if "text/event-stream" not in response.headers.get("content-type", ""):
            return response.json()
        data = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        if not data:
            raise AssertionError("MCP event stream contained no JSON data")
        return json.loads(data[-1])


if __name__ == "__main__":
    unittest.main()
