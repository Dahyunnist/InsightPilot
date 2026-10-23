"""Authenticated, durable FastAPI surface over the shared analysis core."""

from __future__ import annotations

import hmac
import os
import re
import uuid
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
from .run_store import IdempotencyConflict, RunStore


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def create_app(core: InsightPilot, *, run_store: RunStore | None = None, api_token: str | None = None,
               api_scopes: set[str] | None = None, authorized_region: str | None = None,
               max_concurrent: int = 8, max_requests_per_minute: int = 120,
               request_timeout_seconds: float = 125) -> FastAPI:
    if max_concurrent < 1 or max_requests_per_minute < 1 or request_timeout_seconds <= 0:
        raise ValueError("Concurrency, rate limit, and request timeout must be positive")
    app = FastAPI(title="InsightPilot", version="0.4.0", description="Read-only, evidence-backed refund analysis")
    store = run_store or RunStore(core.database_path.with_name("insightpilot-runs.sqlite"),
                                  retention_days=int(os.environ.get("INSIGHTPILOT_RUN_RETENTION_DAYS", "90")))
    active_events: dict[str, Event] = {}
    events_lock = Lock()
    capacity = BoundedSemaphore(max_concurrent)
    rate_lock = Lock()
    request_times: deque[float] = deque()
    executor = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="insightpilot-analysis")
    app.state.analysis_executor = executor
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

    def consume_rate_limit() -> float | None:
        now = time.monotonic()
        with rate_lock:
            while request_times and request_times[0] <= now - 60:
                request_times.popleft()
            if len(request_times) >= max_requests_per_minute:
                return max(1.0, 60.0 - (now - request_times[0]))
            request_times.append(now)
        return None

    def result_response(result: dict, *, replay: bool = False) -> JSONResponse:
        status_code = {"answered": 200, "clarify": 422, "refused": 403,
                       "cancelled": 409, "failed": 502}.get(result.get("status"), 500)
        headers = {"X-Request-ID": result["run_id"]}
        if replay:
            headers["Idempotency-Replay"] = "true"
        return JSONResponse(content=result, status_code=status_code, headers=headers)

    def replay_response(run_id: str) -> JSONResponse:
        record = store.get(run_id)
        if record is None:
            raise HTTPException(status_code=503, detail={"reason": "idempotency_record_unavailable"})
        if record["status"] == "running":
            return JSONResponse(status_code=202,
                content={"run_id": run_id, "status": "running", "idempotent_replay": True},
                headers={"X-Request-ID": run_id, "Idempotency-Replay": "true", "Retry-After": "1"})
        if record["result"] is None:
            return JSONResponse(status_code=409,
                content={"run_id": run_id, "status": record["status"], "reason": "result_unavailable"},
                headers={"X-Request-ID": run_id, "Idempotency-Replay": "true"})
        return result_response(record["result"], replay=True)

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

    @app.on_event("shutdown")
    def shutdown_executor() -> None:
        executor.shutdown(wait=False, cancel_futures=False)

    @app.post("/analysis")
    def analysis(request: AnalysisRequest, authorization: str | None = Header(default=None),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        require_scope(authorization, required_access_scopes(request.question))
        if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", idempotency_key):
            return JSONResponse(status_code=422,
                content={"status": "failed", "stage": "validation", "reason": "invalid_idempotency_key"})
        retry_after = consume_rate_limit()
        if retry_after is not None:
            run_id = str(uuid.uuid4())
            return JSONResponse(status_code=429,
                content={"run_id": run_id, "status": "failed", "stage": "rate_limit",
                         "reason": "requests_per_minute_limit"},
                headers={"X-Request-ID": run_id, "Retry-After": str(int(retry_after + 0.999))})
        if idempotency_key is not None:
            try:
                existing_id = store.find_idempotent(idempotency_key, request.question)
            except IdempotencyConflict:
                return JSONResponse(status_code=409,
                    content={"status": "failed", "stage": "idempotency",
                             "reason": "idempotency_key_question_conflict"})
            if existing_id is not None:
                return replay_response(existing_id)
        run_id = str(uuid.uuid4())
        if not capacity.acquire(blocking=False):
            return JSONResponse(status_code=429,
                content={"run_id": run_id, "status": "failed", "stage": "queue", "reason": "concurrency_limit"},
                headers={"X-Request-ID": run_id, "Retry-After": "1"})
        try:
            stored_run_id = store.start(run_id, request.question, idempotency_key)
        except IdempotencyConflict:
            capacity.release()
            return JSONResponse(status_code=409,
                content={"status": "failed", "stage": "idempotency",
                         "reason": "idempotency_key_question_conflict"})
        except Exception:
            capacity.release()
            return JSONResponse(status_code=503,
                content={"run_id": run_id, "status": "failed", "stage": "storage",
                         "reason": "run_store_unavailable"}, headers={"X-Request-ID": run_id})
        if stored_run_id != run_id:
            capacity.release()
            return replay_response(stored_run_id)

        cancel_event = Event()
        with events_lock:
            active_events[run_id] = cancel_event

        def execute_run() -> dict:
            try:
                result = core.analyze(request.question, run_id=run_id, cancel_event=cancel_event,
                                      authorized_region=authorized_region)
            except Exception:
                result = {"run_id": run_id, "status": "failed", "stage": "internal",
                          "reason": "internal_error"}
            store.finish(run_id, result)
            return result

        def release_capacity(_future: Future) -> None:
            with events_lock:
                active_events.pop(run_id, None)
            capacity.release()

        try:
            future = executor.submit(execute_run)
        except Exception:
            with events_lock:
                active_events.pop(run_id, None)
            capacity.release()
            store.finish(run_id, {"run_id": run_id, "status": "failed", "stage": "queue",
                                  "reason": "worker_unavailable"})
            return JSONResponse(status_code=503,
                content={"run_id": run_id, "status": "failed", "stage": "queue",
                         "reason": "worker_unavailable"}, headers={"X-Request-ID": run_id})
        future.add_done_callback(release_capacity)
        try:
            result = future.result(timeout=request_timeout_seconds)
        except FutureTimeoutError:
            cancel_event.set()
            return JSONResponse(status_code=504,
                content={"run_id": run_id, "status": "failed", "stage": "request",
                         "reason": "request_timeout"}, headers={"X-Request-ID": run_id})
        except Exception:
            return JSONResponse(status_code=503,
                content={"run_id": run_id, "status": "failed", "stage": "storage",
                         "reason": "run_result_unavailable"}, headers={"X-Request-ID": run_id})
        return result_response(result)

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
