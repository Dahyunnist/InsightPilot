from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

from scripts import check_ip4_provider_balance as gate


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class _FakeOpener:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[Request, int]] = []

    def open(self, request: Request, timeout: int) -> _Response:
        self.requests.append((request, timeout))
        return self.response


class IP4ProviderBalanceGateTests(unittest.TestCase):
    def run_gate(self, payload: dict | None, *, model: str = "test-model",
                 origin_hash: str = "locked-origin") -> tuple[int, dict, object]:
        with tempfile.TemporaryDirectory() as directory:
            prereg = Path(directory) / "prereg.json"
            prereg.write_text(json.dumps({
                "model_id": "test-model",
                "provider_origin_sha256": "locked-origin",
            }), encoding="utf-8")
            settings = SimpleNamespace(
                model=model,
                base_url="https://provider.invalid/v1",
                api_key="fake-test-secret",
            )
            response = _Response(payload or {})
            opener = _FakeOpener(response)
            stdout = io.StringIO()
            argv = ["check_ip4_provider_balance.py", "--reserve-usd", "0.187"]
            with patch.object(gate, "PREREGISTRATION", prereg), \
                 patch.object(gate, "load_settings", return_value=settings), \
                 patch.object(gate, "_provider_origin_hash", return_value=origin_hash), \
                 patch.object(gate, "build_opener", return_value=opener) as build_opener, \
                 patch.object(sys, "argv", argv), \
                 contextlib.redirect_stdout(stdout):
                result = gate.main()
            output = json.loads(stdout.getvalue())
            return result, output, build_opener.call_args, opener

    def test_balance_request_uses_locked_https_origin_and_never_follows_redirects(self):
        payload = {"is_available": True, "balance_infos": [
            {"currency": "USD", "total_balance": "1.00"},
        ]}
        code, output, opener_args, opener = self.run_gate(payload)

        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "passed")
        self.assertEqual(output["model_calls"], 0)
        self.assertEqual(output["provider_requests"], 1)
        self.assertNotIn("fake-test-secret", json.dumps(output))
        self.assertIs(opener_args.args[0], gate._RejectRedirects)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, "https://provider.invalid/user/balance")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-test-secret")
        self.assertEqual(timeout, 15)
        self.assertIsNone(gate._RejectRedirects().redirect_request(
            request, None, 302, "Found", {}, "https://attacker.invalid/user/balance",
        ))

    def test_mismatched_locked_provider_fails_before_request(self):
        code, output, opener_args, opener = self.run_gate(
            {"is_available": True, "balance_infos": []}, origin_hash="different-origin",
        )

        self.assertEqual(code, 2)
        self.assertEqual(output["provider_requests"], 0)
        self.assertIsNone(opener_args)
        self.assertEqual(opener.requests, [])

    def test_insufficient_balance_is_reported_without_saving_response(self):
        code, output, _opener_args, _opener = self.run_gate({
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "0"}],
            "api_key": "response-secret",
        })

        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "insufficient_or_unavailable")
        self.assertEqual(output["model_calls"], 0)
        self.assertEqual(output["provider_requests"], 1)
        self.assertNotIn("response-secret", json.dumps(output))


if __name__ == "__main__":
    unittest.main()
