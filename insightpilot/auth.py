"""Shared bearer gate for API and MCP HTTP surfaces."""

from __future__ import annotations

import hmac
import json
import uuid
from collections.abc import Callable


class BearerScopeMiddleware:
    def __init__(self, app, *, token: str | None, scopes: set[str], required_scope: str = "refund:read",
                 question_scope_resolver: Callable[[str], set[str]] | None = None):
        self.app, self.token, self.scopes, self.required_scope = app, token, scopes, required_scope
        self.question_scope_resolver = question_scope_resolver

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.token is None:
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, credential = auth.partition(" ")
        authenticated = scheme.lower() == "bearer" and hmac.compare_digest(credential, self.token or "")
        receive_for_app = receive
        required_scopes = {self.required_scope}
        if authenticated and self.question_scope_resolver and scope.get("path") == "/mcp" and scope.get("method") == "POST":
            buffered, body = [], bytearray()
            while True:
                message = await receive()
                buffered.append(message)
                if message.get("type") != "http.request":
                    break
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            try:
                payload = json.loads(body.decode("utf-8"))
                calls = payload if isinstance(payload, list) else [payload]
                for call in calls:
                    arguments = call.get("params", {}).get("arguments", {}) if isinstance(call, dict) else {}
                    question = arguments.get("question") if isinstance(arguments, dict) else None
                    if isinstance(question, str):
                        required_scopes = self.question_scope_resolver(question)
                        break
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                pass
            message_index = 0

            async def replay_receive():
                nonlocal message_index
                if message_index < len(buffered):
                    message = buffered[message_index]
                    message_index += 1
                    return message
                return await receive()

            receive_for_app = replay_receive
        status, reason = None, None
        if not authenticated:
            status, reason = 401, "invalid_bearer_token"
        elif not required_scopes.issubset(self.scopes):
            status, reason = 403, "insufficient_scope"
        if status is None:
            return await self.app(scope, receive_for_app, send)
        request_id = str(uuid.uuid4())
        body = json.dumps({"request_id": request_id, "status": "failed", "stage": "authorization",
                           "error": {"reason": reason, "required_scopes": sorted(required_scopes)}}).encode()
        response_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        response_headers.append((b"x-request-id", request_id.encode()))
        if status == 401:
            response_headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})
