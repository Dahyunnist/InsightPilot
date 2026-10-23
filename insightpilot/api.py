"""Authenticated, durable FastAPI surface over the shared analysis core."""

from __future__ import annotations

import hmac
import os
import uuid
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import InsightPilot
from .config import load_settings
from .metrics import required_access_scopes
from .planner import OpenAICompatiblePlanner
from .run_store import RunStore


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def create_app(core: InsightPilot, *, run_store: RunStore | None = None, api_token: str | None = None,
               api_scopes: set[str] | None = None, authorized_region: str | None = None,
               max_concurrent: int = 8) -> FastAPI:
    app = FastAPI(title="InsightPilot", version="0.4.0", description="Read-only, evidence-backed refund analysis")
    store = run_store or RunStore(core.database_path.with_name("insightpilot-runs.sqlite"),
                                  retention_days=int(os.environ.get("INSIGHTPILOT_RUN_RETENTION_DAYS", "90")))
    active_events: dict[str, Event] = {}
    events_lock = Lock()
    capacity = BoundedSemaphore(max_concurrent)
    scopes = {"refund:read"} if api_scopes is None else api_scopes
    app.state.core = core
    app.state.run_store = store

    def require_scope(authorization: str | None, scope: str | set[str], *, any_scope: bool = False) -> None:
        required = {scope} if isinstance(scope, str) else scope
        if api_token is None:
            return
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, api_token):
            raise HTTPException(status_code=401, detail={"reason": "invalid_bearer_token"}, headers={"WWW-Authenticate": "Bearer"})
        permitted = bool(required & scopes) if any_scope else required.issubset(scopes)
        if not permitted:
            detail = ({"required_scope": sorted(required)[0]} if len(required) == 1
                      else {"required_scopes": sorted(required)})
            raise HTTPException(status_code=403, detail={"reason": "insufficient_scope", **detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc) -> JSONResponse:
        request_id = str(uuid.uuid4())
        return JSONResponse(status_code=422,
            content={"request_id": request_id, "status": "failed", "stage": "validation",
                     "message": "question must contain 1–2000 characters"},
            headers={"X-Request-ID": request_id})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"reason": str(exc.detail)}
        request_id = detail.get("request_id") or str(uuid.uuid4())
        stage = "authorization" if exc.status_code in (401, 403) else "request"
        return JSONResponse(status_code=exc.status_code,
            content={"request_id": request_id, "status": "failed", "stage": stage, **detail},
            headers={"X-Request-ID": request_id, **(exc.headers or {})})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/analysis")
    def analysis(request: AnalysisRequest, authorization: str | None = Header(default=None)) -> JSONResponse:
        require_scope(authorization, required_access_scopes(request.question))
        run_id = str(uuid.uuid4())
        if not capacity.acquire(blocking=False):
            return JSONResponse(status_code=429,
                content={"run_id": run_id, "status": "failed", "stage": "queue", "reason": "concurrency_limit"},
                headers={"X-Request-ID": run_id, "Retry-After": "1"})
        cancel_event = Event()
        try:
            store.start(run_id, request.question)
            with events_lock:
                active_events[run_id] = cancel_event
            try:
                result = core.analyze(request.question, run_id=run_id, cancel_event=cancel_event,
                                      authorized_region=authorized_region)
            except Exception:
                result = {"run_id": run_id, "status": "failed", "stage": "internal", "reason": "internal_error"}
            store.finish(run_id, result)
        finally:
            with events_lock:
                active_events.pop(run_id, None)
            capacity.release()
        status_code = {"answered": 200, "clarify": 422, "refused": 403, "cancelled": 409, "failed": 502}.get(result["status"], 500)
        return JSONResponse(content=result, status_code=status_code, headers={"X-Request-ID": run_id})

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, authorization: str | None = Header(default=None)) -> dict:
        require_scope(authorization, {"refund:read", "marketing:read"}, any_scope=True)
        record = store.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail={"request_id": run_id, "reason": "run_not_found"})
        return record

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
        require_scope(authorization, "refund:cancel")
        if not store.request_cancel(run_id):
            raise HTTPException(status_code=409, detail={"request_id": run_id, "reason": "run_not_active"})
        with events_lock:
            event = active_events.get(run_id)
        if event is not None:
            event.set()
        return JSONResponse(status_code=202, content={"run_id": run_id, "status": "cancellation_requested"},
                            headers={"X-Request-ID": run_id})

    return app


def build_default_app() -> FastAPI:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    core = InsightPilot(OpenAICompatiblePlanner(settings), settings.database_path)
    token = os.environ.get("INSIGHTPILOT_API_TOKEN") or None
    scopes = set(filter(None, os.environ.get(
        "INSIGHTPILOT_API_SCOPES", "refund:read,refund:cancel,marketing:read"
    ).split(",")))
    region = os.environ.get("INSIGHTPILOT_API_REGION") or None
    return create_app(core, api_token=token, api_scopes=scopes, authorized_region=region)


app = build_default_app()
