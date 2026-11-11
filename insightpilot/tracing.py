"""Optional, privacy-safe OpenTelemetry tracing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import sys
from typing import Any
from contextlib import contextmanager


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
