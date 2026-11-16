"""Audit IP4 two-domain/backend evidence and model-run manifest gaps."""

from __future__ import annotations

import argparse
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPORT = ROOT / "reports/ip4-retrieval-ablation-model-2026-11-16-r2.json"
RUN_MANIFEST = ROOT / "reports/ip4-retrieval-ablation-run-manifest-2026-11-16-r2.json"
MODEL_AUDIT = ROOT / "reports/ip4-retrieval-ablation-audit-2026-11-16.json"
V13_AUDIT = ROOT / "reports/ip4-v13-acceptance-audit-2026-11-15.json"
OFFLINE_MANIFEST = ROOT / "benchmarks/ip4-offline-prep-v1/manifest.json"
DEFAULT_OUTPUT = ROOT / "reports/ip4-release-requirement-audit-2026-11-16.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_audit(parity_path: Path) -> dict[str, Any]:
    model = _read(MODEL_REPORT)
    manifest = _read(RUN_MANIFEST)
    model_audit = _read(MODEL_AUDIT)
    v13_audit = _read(V13_AUDIT)
    bundle = _read(OFFLINE_MANIFEST)
    parity = _read(parity_path)

    model_sha = _sha(MODEL_REPORT)
    manifest_sha = _sha(RUN_MANIFEST)
    parity_sha = _sha(parity_path)
    bundle_sha = _sha(OFFLINE_MANIFEST)
    if manifest["source_artifacts"]["report_sha256"] != model_sha:
        raise ValueError("model report hash does not match its run manifest source")
    if model_audit["run_manifest_sidecar"]["sha256"] != manifest_sha:
        raise ValueError("model audit hash does not match run manifest sidecar")
    if model.get("manifest_sha256") != bundle_sha or bundle_sha != manifest["source_artifacts"]["manifest_sha256"]:
        raise ValueError("model report, sidecar, and offline bundle manifest hashes differ")
    if bundle.get("source_v13") != v13_audit.get("hashes"):
        raise ValueError("offline bundle provenance does not match the v13 audit hashes")
    if parity.get("frozen_hashes") != v13_audit.get("hashes"):
        raise ValueError("PostgreSQL parity does not use the audited frozen v13 hashes")
    if (parity.get("offline_bundle_suite_id") != model.get("suite_id")
            or parity.get("data_fingerprint") != manifest.get("data_fingerprint")):
        raise ValueError("model run and frozen parity do not share the same offline bundle/data fingerprint")
    if (parity.get("status") != "completed" or parity.get("deterministic_parity_status") != "passed"
            or parity.get("case_count") != 22 or parity.get("model_calls") != 0
            or parity.get("model_requests_sent") != 0):
        raise ValueError("frozen deterministic PostgreSQL parity is incomplete")
    execution = parity.get("execution_environment", {})
    if (execution.get("env_file_autoload_disabled") is not True
            or execution.get("host_binding") != "127.0.0.1"
            or execution.get("postgres_storage") != "tmpfs"
            or execution.get("host_data_volume") is not False
            or execution.get("project_cleanup") != "passed_project_only"
            or execution.get("remaining_project_containers") != 0
            or execution.get("remaining_project_networks") != 0):
        raise ValueError("parity execution isolation/cleanup evidence is incomplete")
    tenants = parity.get("database_parity", {}).get("tenants", {})
    if (parity.get("database_parity", {}).get("status") != "passed"
            or set(tenants) != {"tenant-a", "tenant-b"}
            or any(item.get("passed") != 22 or item.get("total") != 22 or item.get("failures")
                   for item in tenants.values())):
        raise ValueError("PostgreSQL tenant parity did not pass for all frozen cases")

    field_status = manifest["field_status"]
    field_audit = {}
    for key in (
            "project", "git_sha", "run_id", "trace_id", "span_id", "evaluation_id", "environment",
            "dataset_id", "dataset_sha256", "data_fingerprint", "split", "model_id", "prompt_sha256",
            "tool_schema_sha256", "scorer_sha256", "gold_source", "gold_sha256", "max_steps", "max_tokens",
            "started_at", "completed_at",
    ):
        value = manifest.get(key)
        state = field_status.get(key, "recorded" if value is not None else "not_in_schema")
        field_audit[key] = {"value": value, "status": state}
    for key in ("digest_sha256", "python", "platform", "lock_sha256"):
        field_audit[f"runtime.{key}"] = {
            "value": manifest.get("runtime", {}).get(key),
            "status": field_status.get(f"runtime.{key}", "not_recorded"),
        }
    for key in ("cap_usd", "spent_usd", "reserved_usd"):
        field_audit[f"budget.{key}"] = {
            "value": manifest.get("budget", {}).get(key),
            "status": field_status.get(f"budget.{key}", "not_recorded"),
        }
    field_audit["cost_usd"] = {"value": manifest.get("cost_usd"), "status": manifest.get("cost_status")}
    for key in ("price_estimate_usd", "remaining_before_usd", "remaining_after_usd"):
        field_audit[f"budget.{key}"] = {
            "value": manifest.get("budget", {}).get(key),
            "status": "recorded_separately_from_actual_cost",
        }
    field_audit["request_outcome"] = {
        "value": manifest.get("request_outcome"),
        "status": "recorded",
    }
    field_audit["normalizer_sha256"] = {
        "value": manifest.get("normalizer_sha256"),
        "status": field_status.get("normalizer_sha256", "recorded"),
    }
    field_audit["schema_sha256"] = {
        "value": manifest.get("schema_sha256"),
        "status": field_status.get("schema_sha256", "recorded"),
    }
    field_audit["backend_execution"] = {
        "value": model.get("backend"),
        "status": "not_recorded" if "backend" not in model else "recorded",
        "run_manifest_status": "not_in_schema" if "backend_execution" not in manifest else "recorded",
    }

    domain_counts = v13_audit["domain_counts"]
    model_backend = field_audit["backend_execution"]
    return {
        "schema_version": 1,
        "audit_status": "deterministic_parity_passed_model_evidence_incomplete",
        "model_calls_for_this_audit": 0,
        "two_domain_requirement": {
            "status": "passed",
            "case_count": model["planned_case_runs"] // 2,
            "domain_counts": domain_counts,
            "model_ablation_suite_id": model["suite_id"],
        },
        "model_ablation_backend_scope": {
            "status": "not_recorded_in_run_evidence",
            "report_backend_field": model_backend["status"],
            "run_manifest_backend_field": model_backend["run_manifest_status"],
            "model_calls": model["observed_api_calls"],
            "configured_model": manifest.get("planned_model_id"),
            "actual_model_id_status": field_status.get("model_id"),
            "claim_boundary": "Deterministic PostgreSQL parity does not prove that model requests ran against PostgreSQL.",
        },
        "v13_model_execution_gate": {
            "status": "blocked_pending_code_lock_review",
            "execution_code_lock_matches": v13_audit["execution_code_lock"]["matches"],
            "model_evaluation_allowed": False,
            "model_calls_for_this_audit": 0,
        },
        "frozen_deterministic_two_backend_parity": {
            "status": "passed",
            "report_path": parity_path.relative_to(ROOT).as_posix(),
            "report_sha256": parity_sha,
            "run_id": parity["run_id"],
            "suite_id": parity["suite_id"],
            "case_count": parity["case_count"],
            "domains": domain_counts,
            "postgresql_server_version": parity["postgresql_server_version"],
            "tenant_results": {tenant: {"passed": item["passed"], "total": item["total"]}
                                for tenant, item in tenants.items()},
            "ruleplanner_score_vector": {
                name: {
                    "passed": arm["passed"],
                    "total": arm["total"],
                    "failure_case_ids": [item["case_id"] for item in arm["failures"]],
                }
                for name, arm in parity["arms"].items()
            },
            "model_calls": parity["model_calls"],
            "frozen_hashes": parity["frozen_hashes"],
            "execution_environment": execution,
            "scoring_method": parity["scoring_method"],
        },
        "superseded_intermediate_attempts": [
            {
                "path": "reports/ip4-v13-postgres-parity-bf75136a9a7e4d8294d51124f5a29a50.json",
                "recorded_sha256": "8ea43d534220870cfa46b0cf1f84a2c5921c711028d047d78da92d76b702966f",
                "wrapper_status": "failed_after_report_generation",
                "observed_parity": "passed_22_of_22_per_tenant",
                "artifact_recovery_status": "not_recoverable_in_current_workspace",
                "release_candidate_action": "exclude_superseded_intermediate",
            },
            {
                "path": "reports/ip4-v13-postgres-parity-ea44b24e4f9240c0b74883f6863b29c6.json",
                "recorded_sha256": "51df2a24ba01b72242c6766e5f823c9b5021538c1baf32d380d2b25ab4ce2477",
                "wrapper_status": "failed_after_report_generation",
                "observed_parity": "passed_22_of_22_per_tenant",
                "recorded_postgresql_version": "16.15",
                "artifact_recovery_status": "not_recoverable_in_current_workspace",
                "release_candidate_action": "exclude_superseded_intermediate",
            },
        ],
        "intermediate_attempt_recovery": {
            "exact_original_files_present": False,
            "recovery_attempts": {
                "workspace_and_temp_search": "not_found",
                "unreachable_git_blobs_checked": 8,
                "exact_sha256_matches": 0,
            },
            "reconstructed_json": False,
            "release_candidate_action": "exclude_missing_intermediate_artifacts; retain this loss record and final passing evidence",
        },
        "model_run_manifest": {
            "path": RUN_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": manifest_sha,
            "schema_version": manifest["schema_version"],
            "validator_status": model_audit["run_manifest_sidecar"]["validator_status"],
            "field_status": field_audit,
            "model_calls": manifest["model_calls"],
            "trace_evidence_status": manifest["trace_event_evidence"]["source_status"],
            "trace_events_recorded": manifest["trace_event_evidence"]["source_logs_recorded"],
            "provider_cost_actual_status": manifest["cost_status"],
            "report_sha256": model_sha,
            "offline_bundle_manifest_sha256": bundle_sha,
        },
        "interpretation": (
            "IP-4 frozen synthetic deterministic evaluation now covers refunds and marketing ROI on SQLite "
            "and both PostgreSQL tenants. The separate 36-call model ablation has no run-bound backend field; "
            "do not describe it as a PostgreSQL model run."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parity-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_audit(args.parity_report.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "audit_status": report["audit_status"],
        "two_domain_requirement": report["two_domain_requirement"]["status"],
        "frozen_deterministic_two_backend_parity": report["frozen_deterministic_two_backend_parity"]["status"],
        "model_ablation_backend_scope": report["model_ablation_backend_scope"]["status"],
        "model_calls_for_this_audit": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
