"""Shared bearer gate for API and MCP HTTP surfaces."""

from __future__ import annotations

import hmac
import json
import uuid


class BearerScopeMiddleware:
    def __init__(self, app, *, token: str | None, scopes: set[str], required_scope: str = "refund:read"):
        self.app, self.token, self.scopes, self.required_scope = app, token, scopes, required_scope

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.token is None:
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, credential = auth.partition(" ")
        status, reason = None, None
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, self.token):
            status, reason = 401, "invalid_bearer_token"
        elif self.required_scope not in self.scopes:
            status, reason = 403, "insufficient_scope"
        if status is None:
            return await self.app(scope, receive, send)
        request_id = str(uuid.uuid4())
        body = json.dumps({"request_id": request_id, "status": "failed", "stage": "authorization",
                           "error": {"reason": reason, "required_scope": self.required_scope}}).encode()
        response_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        response_headers.append((b"x-request-id", request_id.encode()))
        if status == 401:
            response_headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})
