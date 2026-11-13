"""Validation for new offline evaluation run manifests."""
from __future__ import annotations

import re
from datetime import datetime

REQUIRED = ("schema_version", "project", "git_sha", "dataset_id", "dataset_sha256", "split",
            "model_id", "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
            "scorer_sha", "gold_source", "cost_usd_estimate", "run_id", "trace_id", "started_at",
            "ended_at", "status", "evaluation_id", "domain", "truth_sha256", "result_fingerprint_sha256")
HASH64 = re.compile(r"^[0-9a-f]{64}$")
HASH40 = re.compile(r"^[0-9a-f]{40}$")


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
    if value["prompt_sha"] is not None or value["tool_schema_sha"] is not None or value["runtime_digest"] is not None or value["max_tokens"] is not None:
        raise ValueError("offline baseline model fields must be null")
    if value["cost_usd_estimate"] != 0 or not isinstance(value["max_steps"], int) or value["max_steps"] < 1:
        raise ValueError("invalid offline cost or max_steps")
    if value["status"] != "completed":
        raise ValueError("run manifest is not completed")
    start, end = datetime.fromisoformat(value["started_at"]), datetime.fromisoformat(value["ended_at"])
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("invalid manifest time range")
