"""FastAPI surface over the same guarded analysis service used by CLI/MCP."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uuid

from .agent import InsightPilot
from .config import load_settings
from .planner import OpenAICompatiblePlanner


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def create_app(core: InsightPilot) -> FastAPI:
    app = FastAPI(title="InsightPilot", version="0.3.0", description="Read-only, evidence-backed refund analysis")
    results: OrderedDict[str, dict] = OrderedDict()
    results_lock = Lock()
    app.state.core = core

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc) -> JSONResponse:
        request_id = str(uuid.uuid4())
        return JSONResponse(
            status_code=422,
            content={"request_id": request_id, "status": "failed", "stage": "validation",
                     "message": "question must contain 1–2000 characters"},
            headers={"X-Request-ID": request_id},
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/analysis")
    def analysis(request: AnalysisRequest) -> JSONResponse:
        result = core.analyze(request.question)
        run_id = result["run_id"]
        with results_lock:
            results[run_id] = result
            results.move_to_end(run_id)
            while len(results) > 100:
                results.popitem(last=False)
        status_code = {"answered": 200, "clarify": 422, "refused": 403, "failed": 502}.get(result["status"], 500)
        return JSONResponse(content=result, status_code=status_code, headers={"X-Request-ID": run_id})

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        with results_lock:
            result = results.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail={"request_id": run_id, "reason": "run_not_found"})
        return result

    return app


def build_default_app() -> FastAPI:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    core = InsightPilot(OpenAICompatiblePlanner(settings), settings.database_path)
    return create_app(core)


app = build_default_app()
