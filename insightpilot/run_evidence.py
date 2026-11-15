"""Offline projection of recorded IP-1..IP-4 run evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MINIMUM_FIELDS = (
    "project", "git_sha", "dataset_id", "dataset_sha256", "split", "model_id",
    "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
    "scorer_sha", "gold_source", "cost_usd_estimate",
)


def _record(**values: Any) -> dict[str, Any]:
    record = {field: None for field in MINIMUM_FIELDS}
    record.update({
        "run_id": None, "trace_id": None, "span_id": None,
        "provider_requests_attempted": None, "provider_responses_received": None,
        "cost_status": "unconfirmed", "delivery_status": "unknown",
    })
    record.update(values)
    return record


def project_record_evidence(root: Path) -> list[dict[str, Any]]:
    """Project source reports without guessing missing fields or writing files."""
    read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    host = read("reports/host-validation-ip1-ip3-2026-11-15/run-manifest.json")
    ip2 = read("reports/ip2-boundary-development-manifest-v1.json")
    otel = read("reports/ip3-otel-collector.json")
    rule = read("reports/ip4-v13-rule-baseline-manifest.json")
    r5 = read("reports/ip4-r5-transport-audit-2026-11-15.json")
    prereg = read("reports/ip4-v13-model-preregistration-20261114-r5.json")
    spans = otel.get("collector", {}).get("span_links", [])
    api_span = next((span for span in spans if span.get("name") == "insightpilot.api.analysis"), {})
    agent_span = next((span for span in spans if span.get("name") == "insightpilot.agent.run"), {})

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
        _record(record_id="ip3-otel-acceptance-2026-11-11", record_kind="completed_run",
                stages=["IP-3"], project="InsightPilot", split="synthetic_otel_acceptance",
                model_id="none", run_id=otel.get("analysis_run_id"), trace_id=api_span.get("trace_id"),
                span_id=api_span.get("span_id"), status=otel.get("status"),
                provider_requests_attempted=0, provider_responses_received=0,
                cost_usd_estimate=0, cost_status="not_applicable", delivery_status="not_attempted",
                related_span_ids=[agent_span.get("span_id")] if agent_span.get("span_id") else [],
                missing_fields=["git_sha", "dataset_id", "dataset_sha256", "run_id", "runtime_digest",
                                "prompt_sha", "tool_schema_sha", "scorer_sha", "gold_source"]),
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
