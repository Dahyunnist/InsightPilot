"""Offline projection of recorded IP-1..IP-4 run evidence."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any


MINIMUM_FIELDS = (
    "project", "git_sha", "dataset_id", "dataset_sha256", "split", "model_id",
    "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
    "scorer_sha", "gold_source", "cost_usd_estimate",
)

IP3_OTEL_SOURCE_FILES = {
    "compose": "docker-compose.ip3-otel-local.yml",
    "collector_config": "config/otel-collector-ip3-local.yaml",
    "dockerfile": "Dockerfile.ip3",
    "probe": "scripts/ip3_otel_probe_app.py",
    "runner": "scripts/verify_ip3_otel_collector.py",
    "seed": "data/seed.py",
    "requirements": "requirements.txt",
    "otel_requirements": "requirements-otel.txt",
    "api": "insightpilot/api.py",
    "planner": "insightpilot/planner.py",
}
IP3_OTEL_EVIDENCE_REQUIRED = {
    "project", "run_id", "trace_id", "span_id", "dataset_id", "dataset_sha256", "split",
    "model_id", "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
    "scorer_sha", "gold_source", "git_sha", "model_calls", "provider_requests_attempted",
    "provider_responses_received", "cost_usd_estimate", "cost_status", "delivery_status",
    "field_status", "missing_field_reasons",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_ip3_otel_run_evidence(value: dict[str, Any]) -> list[str]:
    """Validate the evidence envelope written by the future OTel runner."""
    if not isinstance(value, dict):
        return ["evidence must be an object"]
    errors = []
    missing = IP3_OTEL_EVIDENCE_REQUIRED - value.keys()
    if missing:
        errors.append("missing fields: " + ", ".join(sorted(missing)))
    statuses = value.get("field_status")
    statuses = statuses if isinstance(statuses, dict) else {}
    reasons = value.get("missing_field_reasons")
    reasons = reasons if isinstance(reasons, dict) else {}
    if not isinstance(value.get("missing_field_reasons"), dict):
        errors.append("missing_field_reasons must be an object")
    nullable_fields = ("git_sha", "run_id", "trace_id", "span_id", "dataset_sha256", "runtime_digest",
                       "prompt_sha", "tool_schema_sha", "scorer_sha", "max_tokens")
    for field in nullable_fields:
        item = value.get(field)
        if item is None:
            if statuses.get(field) not in {"not_recorded", "not_applicable"}:
                errors.append(f"null {field} requires explicit field status")
            if statuses.get(field) in {"not_recorded", "not_applicable"} and field not in reasons:
                errors.append(f"null {field} requires missing-field reason")
        elif not isinstance(item, str) or not item:
            errors.append(f"{field} must be a non-empty string or null")
    for field in ("dataset_sha256", "runtime_digest", "prompt_sha", "tool_schema_sha", "scorer_sha"):
        item = value.get(field)
        if item is not None and not re.fullmatch(r"[0-9a-f]{64}", item):
            errors.append(f"invalid {field}")
    if value.get("git_sha") is not None and not re.fullmatch(r"[0-9a-f]{40}", value["git_sha"]):
        errors.append("invalid git_sha")
    if value.get("trace_id") is not None and not re.fullmatch(r"[0-9a-f]{32}", value["trace_id"]):
        errors.append("invalid trace_id")
    if value.get("span_id") is not None and not re.fullmatch(r"[0-9a-f]{16}", value["span_id"]):
        errors.append("invalid span_id")
    counts = ("model_calls", "provider_requests_attempted", "provider_responses_received")
    for field in counts:
        count = value.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(f"{field} must be a non-negative integer")
    if (isinstance(value.get("provider_requests_attempted"), int) and
            isinstance(value.get("provider_responses_received"), int) and
            value["provider_responses_received"] > value["provider_requests_attempted"]):
        errors.append("provider responses cannot exceed attempted requests")
    cost = value.get("cost_usd_estimate")
    if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0):
        errors.append("cost_usd_estimate must be non-negative or null")
    if value.get("model_id") == "none":
        if any(value.get(field) != 0 for field in counts):
            errors.append("model_id=none requires zero calls and provider requests/responses")
        if value.get("cost_usd_estimate") != 0 or value.get("cost_status") != "not_applicable":
            errors.append("model_id=none requires zero cost and not_applicable status")
    raw_statuses = value.get("field_status")
    if not isinstance(raw_statuses, dict):
        errors.append("field_status must be an object")
    elif any(state not in {"recorded", "not_recorded", "not_applicable"}
             for state in raw_statuses.values()):
        errors.append("invalid field_status value")
    if value.get("cost_status") not in {"confirmed", "estimated", "unconfirmed", "not_applicable"}:
        errors.append("invalid cost_status")
    return errors


def build_ip3_otel_run_evidence(
    root: Path,
    *,
    git_sha: str | None,
    analysis_response: dict[str, Any] | None,
    trace_summary: dict[str, Any] | None,
    runtime_facts: dict[str, Any] | None,
    dataset_sha256: str | None,
) -> dict[str, Any]:
    """Build future IP-3 evidence from observed IDs, runtime and fixture bytes."""
    source_hashes = {}
    source_missing_reasons = {}
    for name, relative_path in IP3_OTEL_SOURCE_FILES.items():
        path = root / relative_path
        try:
            if path.is_file():
                source_hashes[name] = sha256_file(path)
            else:
                source_missing_reasons[name] = f"source file unavailable: {relative_path}"
        except OSError:
            source_missing_reasons[name] = f"source file unreadable: {relative_path}"
    source_hash_status = {
        name: "recorded" if name in source_hashes else "not_recorded"
        for name in IP3_OTEL_SOURCE_FILES
    }
    runtime = dict(runtime_facts or {})
    runtime["source_sha256"] = source_hashes
    runtime_complete = bool(runtime_facts) and all(
        runtime_facts.get(field) for field in ("python", "platform", "api_image_id", "collector_image_id"))
    runtime_digest = (hashlib.sha256(json.dumps(
        runtime, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if runtime_complete else None)
    body = (analysis_response or {}).get("body", {})
    spans = (trace_summary or {}).get("span_links", [])
    api_span = next((span for span in spans if span.get("name") == "insightpilot.api.analysis"), {})
    attempts = 0
    responses = 0
    evidence = {
        "project": "InsightPilot",
        "run_id": body.get("run_id"),
        "trace_id": api_span.get("trace_id"),
        "span_id": api_span.get("span_id"),
        "dataset_id": "ip3-otel-synthetic-fixture-v1",
        "dataset_sha256": dataset_sha256,
        "split": "synthetic_acceptance",
        "scorer_sha": None,
        "prompt_sha": None,
        "tool_schema_sha": None,
        "runtime_digest": runtime_digest,
        "max_steps": 1,
        "max_tokens": None,
        "gold_source": "not_applicable_to_acceptance",
        "git_sha": git_sha,
        "model_id": "none",
        "model_calls": 0,
        "provider_requests_attempted": attempts,
        "provider_responses_received": responses,
        "cost_usd_estimate": 0,
        "cost_status": "not_applicable",
        "delivery_status": "not_attempted",
        "source_sha256": source_hashes,
        "source_hash_status": source_hash_status,
        "source_missing_reasons": source_missing_reasons,
        "runtime_facts": runtime,
        "field_status": {
            "git_sha": "recorded" if git_sha else "not_recorded",
            "run_id": "recorded" if body.get("run_id") else "not_recorded",
            "trace_id": "recorded" if api_span.get("trace_id") else "not_recorded",
            "span_id": "recorded" if api_span.get("span_id") else "not_recorded",
            "dataset_sha256": "recorded" if dataset_sha256 else "not_recorded",
            "split": "recorded",
            "model_id": "recorded",
            "max_steps": "recorded",
            "max_tokens": "not_applicable",
            "gold_source": "not_applicable",
            "scorer_sha": "not_applicable",
            "prompt_sha": "not_applicable",
            "tool_schema_sha": "not_applicable",
            "runtime_digest": "recorded" if runtime_digest else "not_recorded",
        },
        "missing_field_reasons": {
            key: reason for key, reason in {
                "git_sha": "git commit unavailable at report time",
                "run_id": "analysis response did not include run_id",
                "trace_id": "collector did not yield API root trace ID",
                "span_id": "collector did not yield API root span ID",
                "dataset_sha256": "ephemeral fixture database hash unavailable",
                "runtime_digest": "container runtime facts unavailable",
                "scorer_sha": "acceptance run has no scorer",
                "prompt_sha": "fixed planner uses no model prompt",
                "tool_schema_sha": "fixed planner exposes no model tool schema",
                "max_tokens": "no model token budget applies to fixed planner",
                "gold_source": "acceptance run does not score against gold",
            }.items() if (key in {"scorer_sha", "prompt_sha", "tool_schema_sha", "max_tokens", "gold_source"} or
                          (key == "git_sha" and not git_sha) or
                          (key == "run_id" and not body.get("run_id")) or
                          (key == "trace_id" and not api_span.get("trace_id")) or
                          (key == "span_id" and not api_span.get("span_id")) or
                          (key == "dataset_sha256" and not dataset_sha256) or
                          (key == "runtime_digest" and not runtime_digest))
        },
    }
    errors = validate_ip3_otel_run_evidence(evidence)
    if errors:
        raise ValueError("invalid IP-3 run evidence: " + "; ".join(errors))
    return evidence


def _record(**values: Any) -> dict[str, Any]:
    record = {field: None for field in MINIMUM_FIELDS}
    record.update({
        "run_id": None, "trace_id": None, "span_id": None,
        "provider_requests_attempted": None, "provider_responses_received": None,
        "cost_status": "unconfirmed", "delivery_status": "unknown",
    })
    record.update(values)
    return record


def _project_ip3_otel_record(otel: dict[str, Any], record_id: str) -> dict[str, Any]:
    evidence = otel.get("run_evidence", {})
    spans = otel.get("collector", {}).get("span_links", [])
    api_span = next((span for span in spans if span.get("name") == "insightpilot.api.analysis"), {})
    agent_span = next((span for span in spans if span.get("name") == "insightpilot.agent.run"), {})
    states = evidence.get("field_status", {})
    missing = [field for field in MINIMUM_FIELDS
               if evidence.get(field) is None and states.get(field) != "not_applicable"]
    return _record(
        record_id=record_id, record_kind="completed_run", stages=["IP-3"],
        project=evidence.get("project", "InsightPilot"), git_sha=evidence.get("git_sha"),
        dataset_id=evidence.get("dataset_id"), dataset_sha256=evidence.get("dataset_sha256"),
        split=evidence.get("split", "synthetic_otel_acceptance"),
        model_id=evidence.get("model_id", "none"), prompt_sha=evidence.get("prompt_sha"),
        tool_schema_sha=evidence.get("tool_schema_sha"), runtime_digest=evidence.get("runtime_digest"),
        max_steps=evidence.get("max_steps", 1), max_tokens=evidence.get("max_tokens"),
        scorer_sha=evidence.get("scorer_sha"), gold_source=evidence.get("gold_source"),
        cost_usd_estimate=evidence.get("cost_usd_estimate", 0),
        run_id=evidence.get("run_id", otel.get("analysis_run_id")),
        trace_id=evidence.get("trace_id", api_span.get("trace_id")),
        span_id=evidence.get("span_id", api_span.get("span_id")), status=otel.get("status"),
        provider_requests_attempted=evidence.get("provider_requests_attempted", 0),
        provider_responses_received=evidence.get("provider_responses_received", 0),
        cost_status=evidence.get("cost_status", "not_applicable"),
        delivery_status=evidence.get("delivery_status", "not_attempted"),
        related_span_ids=[agent_span.get("span_id")] if agent_span.get("span_id") else [],
        field_status=states, missing_field_reasons=evidence.get("missing_field_reasons", {}),
        source_sha256=evidence.get("source_sha256", {}), missing_fields=missing,
    )


def project_record_evidence(root: Path) -> list[dict[str, Any]]:
    """Project source reports without guessing missing fields or writing files."""
    read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    host = read("reports/host-validation-ip1-ip3-2026-11-15/run-manifest.json")
    ip2 = read("reports/ip2-boundary-development-manifest-v1.json")
    otel = read("reports/ip3-otel-collector.json")
    rule = read("reports/ip4-v13-rule-baseline-manifest.json")
    r5 = read("reports/ip4-r5-transport-audit-2026-11-15.json")
    prereg = read("reports/ip4-v13-model-preregistration-20261114-r5.json")
    records = [
        _record(record_id="ip1-ip3-host-validation", record_kind="completed_run",
                stages=["IP-1", "IP-2", "IP-3"], project=host.get("project"),
                git_sha=host.get("git_sha"), dataset_id=host.get("dataset_id"),
                dataset_sha256=host.get("dataset_sha256"), split=host.get("split"),
                model_id="none", prompt_sha=host.get("prompt_sha"),
                tool_schema_sha=host.get("tool_schema_sha"), runtime_digest=None,
                scorer_sha=host.get("scorer_sha"), gold_source=host.get("gold_source"),
                cost_usd_estimate=host.get("cost_usd_estimate"), run_id=host.get("run_id"),
                status=host.get("status"), provider_requests_attempted=0,
                provider_responses_received=0, cost_status="not_applicable",
                delivery_status="not_attempted", source_runtime=host.get("runtime"),
                missing_fields=host.get("unrecorded_fields", [])),
        _record(record_id="ip2-boundary-development-v1", record_kind="development_manifest",
                stages=["IP-2"], project="InsightPilot", dataset_id=ip2.get("suite_id"),
                dataset_sha256=None, split="development_only", model_id="none",
                gold_source="independent_python_or_hand_calculated; source-specific",
                cost_usd_estimate=0, status="not_run", provider_requests_attempted=0,
                provider_responses_received=0, cost_status="not_applicable",
                delivery_status="not_attempted", source_model_calls=ip2.get("model_calls"),
                missing_fields=["git_sha", "dataset_sha256", "run_id", "trace_id", "span_id",
                                "runtime_digest", "prompt_sha", "tool_schema_sha", "scorer_sha"]),
        _project_ip3_otel_record(otel, "ip3-otel-acceptance-2026-11-11"),
        _record(record_id="ip4-v13-rule-baseline", record_kind="completed_run",
                stages=["IP-4"], project=rule.get("project"), git_sha=rule.get("git_sha"),
                dataset_id=rule.get("dataset_id"), dataset_sha256=rule.get("dataset_sha256"),
                split=rule.get("split"), model_id=rule.get("model_id"),
                prompt_sha=rule.get("prompt_sha"), tool_schema_sha=rule.get("tool_schema_sha"),
                runtime_digest=rule.get("runtime_digest"), max_steps=rule.get("max_steps"),
                max_tokens=rule.get("max_tokens"), scorer_sha=rule.get("scorer_sha"),
                gold_source=rule.get("gold_source"), cost_usd_estimate=rule.get("cost_usd_estimate"),
                run_id=rule.get("run_id"), trace_id=None, span_id=rule.get("span_id"),
                legacy_source_derived_trace_id=rule.get("trace_id"), status=rule.get("status"),
                provider_requests_attempted=0, provider_responses_received=0,
                cost_status="not_applicable", delivery_status="not_attempted",
                missing_fields=["actual_trace_id"]),
        _record(record_id="ip4-v13-r5-transport", record_kind="attempted_run",
                stages=["IP-4"], project=r5.get("project"), dataset_id=prereg.get("suite_id"),
                dataset_sha256=r5.get("hashes", {}).get("blind_sha256", {}).get("actual"),
                split="frozen_synthetic_development", model_id="none",
                planned_model_id=prereg.get("model"), max_steps=prereg.get("execution_attempts_per_case"),
                max_tokens=prereg.get("output_tokens_per_request"),
                scorer_sha=r5.get("hashes", {}).get("scorer_sha256", {}).get("actual"),
                gold_source="independent_sealed_gold", cost_usd_estimate=r5.get("actual_cost_usd"),
                status=r5.get("status"), provider_requests_attempted=r5.get("attempted_provider_requests"),
                provider_responses_received=r5.get("provider_responses_received"),
                cost_status="unconfirmed", delivery_status="unknown",
                peak_price_estimate_usd=r5.get("peak_price_estimate_usd"),
                missing_fields=["git_sha", "run_id", "trace_id", "span_id", "runtime_digest",
                                "prompt_sha", "tool_schema_sha"],
                source_semantics=r5.get("delivery")),
    ]
    report_directory = root / "reports"
    for report_path in sorted(report_directory.glob("ip3-otel-collector-run-*.json")):
        extra_otel = json.loads(report_path.read_text(encoding="utf-8"))
        records.append(_project_ip3_otel_record(extra_otel, report_path.stem))
    return records


def audit_record_evidence(root: Path) -> dict[str, Any]:
    records = project_record_evidence(root)
    return {
        "schema_version": 1,
        "audit_scope": ["IP-1", "IP-2", "IP-3", "IP-4"],
        "offline_only": True,
        "minimum_fields": list(MINIMUM_FIELDS),
        "records": records,
        "summary": {
            "record_count": len(records),
            "minimum_field_coverage": {
                field: sum(record.get(field) is not None for record in records)
                for field in MINIMUM_FIELDS
            },
            "real_trace_records": [record["record_id"] for record in records if record.get("trace_id")],
            "unconfirmed_cost_records": [record["record_id"] for record in records
                                          if record.get("cost_status") == "unconfirmed"],
        },
    }
