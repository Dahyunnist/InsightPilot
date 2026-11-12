"""Optional, privacy-safe OpenTelemetry tracing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from contextlib import contextmanager


_JSONL_EVENTS = frozenset({"analysis.started", "analysis.completed", "analysis.failed"})
_TRACE_STAGES = frozenset({"preflight", "planning", "dataset", "compilation", "query",
                           "verification", "answer", "unknown"})
_TRACE_STATUSES = frozenset({"answered", "clarify", "refused", "failed", "cancelled", "error"})
_TRACE_FAILURES = frozenset({"data_identity_mismatch", "database_execution", "independent_reconciliation",
                             "invalid_region", "invalid_region_filter", "plan_compilation_error",
                             "query_execution", "result_limit", "sql_policy", "statement_timeout",
                             "tenant_scope_missing", "unknown_metric_version", "unspecified"})


def _trace_ids(run_id: str | None) -> tuple[str | None, str | None]:
    trace_id = run_id.replace("-", "") if run_id else None
    span_id = None
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            trace_id = f"{context.trace_id:032x}"
            span_id = f"{context.span_id:016x}"
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return trace_id, span_id


def write_jsonl_trace(event: str, *, run_id: str, stage: str | None = None,
                      status: str | None = None, failure_category: str | None = None) -> bool:
    """Append fixed-schema trace metadata; never serialize prompts, SQL, or results."""
    path = os.environ.get("INSIGHTPILOT_TRACE_JSONL")
    if not path or event not in _JSONL_EVENTS:
        return False
    try:
        normalized_run_id = str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError):
        normalized_run_id = None

    trace_id, span_id = _trace_ids(normalized_run_id)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "run_id": normalized_run_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "stage": stage if stage in _TRACE_STAGES else None,
        "status": status if status in _TRACE_STATUSES else None,
        "failure_category": failure_category if failure_category in _TRACE_FAILURES else (
            "other" if failure_category else None),
    }
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return False
    return True


def configure_otlp_tracing(endpoint: str | None = None, *, provider_factory=None,
                           exporter_factory=None, batch_processor_factory=None,
                           resource_factory=None):
    """Build an optional OTLP/HTTP batch pipeline; no endpoint means no tracing."""
    endpoint = endpoint if endpoint is not None else os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return None, None

    if any(factory is None for factory in
           (provider_factory, exporter_factory, batch_processor_factory, resource_factory)):
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError as exc:
            raise RuntimeError(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set; install optional dependencies from requirements-otel.txt"
            ) from exc
        provider_factory = provider_factory or TracerProvider
        exporter_factory = exporter_factory or OTLPSpanExporter
        batch_processor_factory = batch_processor_factory or BatchSpanProcessor
        resource_factory = resource_factory or Resource.create

    trace_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not trace_endpoint:
        base = endpoint.rstrip("/")
        trace_endpoint = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
    exporter = exporter_factory(endpoint=trace_endpoint)
    processor = batch_processor_factory(exporter)
    provider = provider_factory(resource=resource_factory({"service.name": "insightpilot"}))
    provider.add_span_processor(processor)
    return provider.get_tracer("insightpilot"), provider.shutdown


def get_tracer(name: str):
    """Return an OpenTelemetry tracer, or None when the optional API is absent."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer(name)


@contextmanager
def start_span(tracer: Any, name: str):
    """Start an optional span without allowing exporter faults to fail a request."""
    if tracer is None:
        yield None
        return
    try:
        manager = tracer.start_as_current_span(name)
        span = manager.__enter__()
    except Exception:
        yield None
        return
    try:
        yield span
    except BaseException:
        try:
            manager.__exit__(*sys.exc_info())
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


def set_span_attribute(span: Any, name: str, value: str) -> None:
    if span is None:
        return
    try:
        span.set_attribute(name, value)
    except Exception:
        pass


def finish_analysis_span(span: Any, result: dict) -> None:
    """Attach only fixed-schema outcome fields; never export request or query data."""
    if span is None:
        return
    status = result.get("status", "failed")
    stage = result.get("stage")
    failure = result.get("failure_detail") or {}
    category = failure.get("category")
    try:
        set_span_attribute(span, "insightpilot.run.status", str(status))
        if isinstance(stage, str):
            set_span_attribute(span, "insightpilot.run.stage", stage)
        if isinstance(category, str):
            set_span_attribute(span, "insightpilot.failure.category", category)
        if isinstance(failure.get("layer"), str):
            set_span_attribute(span, "insightpilot.failure.layer", failure["layer"])
        if status == "failed":
            description = category or stage or "failed"
            code_name = "ERROR"
        else:
            description = None
            code_name = "OK"
        try:
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(getattr(StatusCode, code_name), description=description))
        except ImportError:
            # Keep injected offline tracers usable when the optional OTel API is absent.
            @dataclass(frozen=True)
            class _Code:
                name: str

            @dataclass(frozen=True)
            class _Status:
                status_code: _Code
                description: str | None

            span.set_status(_Status(_Code(code_name), description))
    except (ImportError, AttributeError, TypeError, ValueError):
        # Tracing must not alter analysis behavior if an optional tracer is incomplete.
        pass
