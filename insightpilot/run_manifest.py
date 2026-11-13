"""Validation for new offline evaluation run manifests."""
from __future__ import annotations

import re
import json
from datetime import datetime
from pathlib import Path

REQUIRED = ("schema_version", "project", "git_sha", "dataset_id", "dataset_sha256", "split",
            "model_id", "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
            "scorer_sha", "gold_source", "cost_usd_estimate", "run_id", "trace_id", "started_at",
            "ended_at", "status", "evaluation_id", "domain", "truth_sha256", "result_fingerprint_sha256")
HASH64 = re.compile(r"^[0-9a-f]{64}$")
HASH40 = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_MANIFEST_VERSION = 2
PUBLIC_EVENT_TYPES = ("timeout", "retry", "budget_exceeded", "cancel", "unsafe_action_blocked", "checkpoint_resume")
IP_TRACE_EVENT_TYPES = ("analysis.started", "analysis.completed", "analysis.failed")
PUBLIC_FIELD_STATES = {"recorded", "not_applicable", "not_recorded", "blocked"}
PUBLIC_REQUIRED = {
    "schema_version", "project", "run_id", "trace_id", "span_id", "evaluation_id", "environment",
    "git_sha", "dataset_id", "dataset_sha256", "data_fingerprint", "split", "model_id", "planned_model_id",
    "prompt_sha256", "tool_schema_sha256", "scorer_sha256", "runtime", "budget", "gold_source",
    "gold_sha256", "cost_usd", "model_calls", "model_input_received", "max_steps", "max_tokens", "status", "source_status",
    "evaluation_outcome", "started_at", "completed_at", "field_status", "blocked_fields",
    "trace_event_coverage", "native_event_coverage", "source_artifacts", "normalizer_sha256", "schema_sha256",
}
SENSITIVE_KEY = re.compile(r"(api.?key|access.?token|secret|password|private.?key|raw.?prompt|chain.?of.?thought|reasoning|^prompt$|^question$|sql|parameters)", re.I)


def validate_run_manifest(value: dict) -> None:
    missing = [key for key in REQUIRED if key not in value]
    if missing:
        raise ValueError(f"missing manifest fields: {','.join(missing)}")
    if value["schema_version"] != 1 or not HASH40.fullmatch(value["git_sha"]):
        raise ValueError("invalid schema_version or git_sha")
    for key in ("dataset_sha256", "scorer_sha", "truth_sha256", "result_fingerprint_sha256"):
        if not isinstance(value[key], str) or not HASH64.fullmatch(value[key]):
            raise ValueError(f"invalid {key}")
    if value["model_id"] != "none" or value["gold_source"] != "independent":
        raise ValueError("offline rule baseline must declare model_id=none and independent gold")
    if value["prompt_sha"] is not None or value["tool_schema_sha"] is not None or value["max_tokens"] is not None:
        raise ValueError("offline baseline model fields must be null")
    if value["runtime_digest"] is not None and not HASH64.fullmatch(str(value["runtime_digest"])):
        raise ValueError("runtime_digest must be a SHA-256 or null")
    if value["cost_usd_estimate"] != 0 or not isinstance(value["max_steps"], int) or value["max_steps"] < 1:
        raise ValueError("invalid offline cost or max_steps")
    if value["status"] != "completed":
        raise ValueError("run manifest is not completed")
    start, end = datetime.fromisoformat(value["started_at"]), datetime.fromisoformat(value["ended_at"])
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("invalid manifest time range")


def validate_public_run_manifest(value: dict) -> list[str]:
    """Validate normalized v2 view; unavailable provenance must be explicit."""
    if not isinstance(value, dict):
        return ["public manifest must be an object"]
    errors: list[str] = []
    missing = PUBLIC_REQUIRED - value.keys()
    if missing:
        errors.append("missing public fields: " + ", ".join(sorted(missing)))
    if value.get("schema_version") != PUBLIC_MANIFEST_VERSION:
        errors.append("unsupported public schema_version")

    def inspect(item, key=""):
        if isinstance(item, dict):
            for child_key, child in item.items():
                if child_key != "max_tokens" and SENSITIVE_KEY.search(str(child_key)):
                    errors.append(f"sensitive field is forbidden: {child_key}")
                inspect(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                inspect(child, key)
        elif isinstance(item, str) and item.strip().casefold() == "unknown":
            errors.append(f"unknown placeholder is forbidden: {key}")
    inspect(value)

    field_status = value.get("field_status")
    if not isinstance(field_status, dict):
        errors.append("field_status must be an object")
        field_status = {}
    for field in ("git_sha", "run_id", "trace_id", "span_id", "evaluation_id", "environment", "dataset_id",
                  "dataset_sha256", "data_fingerprint", "split", "prompt_sha256", "tool_schema_sha256", "scorer_sha256",
                  "runtime.digest_sha256", "runtime.python", "runtime.platform", "runtime.lock_sha256",
                  "budget.cap_usd", "budget.spent_usd", "gold_sha256", "started_at", "completed_at"):
        state = field_status.get(field)
        if state not in PUBLIC_FIELD_STATES:
            errors.append(f"invalid or missing field status: {field}")
    nullable = ("git_sha", "run_id", "trace_id", "span_id", "evaluation_id", "environment", "dataset_id",
                "dataset_sha256", "data_fingerprint", "split", "prompt_sha256", "tool_schema_sha256", "scorer_sha256",
                "gold_sha256", "started_at", "completed_at")
    for field in nullable:
        item = value.get(field)
        if item is None:
            if field_status.get(field) not in PUBLIC_FIELD_STATES:
                errors.append(f"null {field} requires explicit status")
        elif not isinstance(item, str) or not item.strip():
            errors.append(f"{field} must be non-empty string or null")
    for field in ("dataset_sha256", "data_fingerprint", "prompt_sha256", "tool_schema_sha256", "scorer_sha256", "gold_sha256",
                  "normalizer_sha256", "schema_sha256"):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not HASH64.fullmatch(item)):
            errors.append(f"invalid {field}")
    if value.get("git_sha") is not None and not HASH40.fullmatch(value["git_sha"]):
        errors.append("invalid git_sha")
    for field in ("project", "model_id", "gold_source", "source_status", "status"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{field} must be non-empty string")
    if value.get("model_id") == "none" and value.get("cost_usd") != 0:
        errors.append("no-model run must have zero cost")
    calls = value.get("model_calls")
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
        errors.append("model_calls must be a non-negative integer")
    elif calls == 0 and value.get("model_id") != "none":
        errors.append("planned model cannot be presented as actual model without calls")
    if not isinstance(value.get("model_input_received"), bool):
        errors.append("model_input_received must be boolean")
    elif calls == 0 and value["model_input_received"]:
        errors.append("model input cannot be received when model_calls is zero")
    if value.get("model_id") == "none" and value.get("planned_model_id") not in (None, "none"):
        if field_status.get("model_id") != "not_applicable":
            errors.append("planned model cannot be presented as actual model")
    if value.get("status") not in {"completed", "failed", "blocked"}:
        errors.append("invalid run status")
    for field in ("started_at", "completed_at"):
        item = value.get(field)
        if item is not None:
            try:
                parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(f"invalid {field}")
    start, end = value.get("started_at"), value.get("completed_at")
    if start and end:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if b < a:
            errors.append("completed_at precedes started_at")

    runtime = value.get("runtime")
    if not isinstance(runtime, dict) or not {"digest_sha256", "python", "platform", "lock_sha256"} <= runtime.keys():
        errors.append("runtime fields missing")
    else:
        for key in ("digest_sha256", "lock_sha256"):
            item = runtime[key]
            if item is None and field_status.get(f"runtime.{key}") not in PUBLIC_FIELD_STATES:
                errors.append(f"null runtime.{key} requires explicit status")
            elif item is not None and (not isinstance(item, str) or not HASH64.fullmatch(item)):
                errors.append(f"invalid runtime.{key}")
    budget = value.get("budget")
    if not isinstance(budget, dict) or not {"cap_usd", "spent_usd", "reserved_usd", "status"} <= budget.keys():
        errors.append("budget fields missing")
    else:
        if budget.get("spent_usd") != value.get("cost_usd"):
            errors.append("budget spent must equal cost")
        for key in ("cap_usd", "spent_usd", "reserved_usd"):
            item = budget.get(key)
            if item is not None and (not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0):
                errors.append(f"invalid budget.{key}")
        if budget.get("cap_usd") is None and field_status.get("budget.cap_usd") not in PUBLIC_FIELD_STATES:
            errors.append("null budget cap requires explicit status")
    for key, expected in (("trace_event_coverage", PUBLIC_EVENT_TYPES),
                          ("native_event_coverage", IP_TRACE_EVENT_TYPES)):
        coverage = value.get(key)
        if not isinstance(coverage, dict) or set(coverage) != set(expected):
            errors.append(f"{key} must match declared event vocabulary")
            continue
        for event, entry in coverage.items():
            if (not isinstance(entry, dict) or not isinstance(entry.get("count"), int)
                    or isinstance(entry.get("count"), bool) or entry["count"] < 0
                    or entry.get("coverage") != ("covered" if entry["count"] > 0 else "not_covered")):
                errors.append(f"invalid event coverage: {event}")
    if value.get("blocked_fields") != sorted(path for path, state in field_status.items() if state == "blocked"):
        errors.append("blocked_fields mismatch")
    return errors


def write_validated_reports(manifest: dict, reports: dict[Path, object]) -> None:
    """Validate first; create no report files when manifest is invalid."""
    validate_run_manifest(manifest)
    for path, payload in reports.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
