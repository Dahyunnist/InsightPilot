from __future__ import annotations

from pathlib import Path

from scripts.audit_ip4_release_requirements import build_audit


ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "reports/ip4-v13-postgres-parity-a2ea8ab495c14543afab476f0496ae65.json"


def test_ip4_release_audit_proves_frozen_two_backend_parity_and_keeps_scope_boundary():
    audit = build_audit(PARITY)

    assert audit["two_domain_requirement"]["status"] == "passed"
    assert audit["two_domain_requirement"]["domain_counts"] == {"marketing_roi": 10, "refunds": 12}
    assert audit["frozen_deterministic_two_backend_parity"]["status"] == "passed"
    assert audit["frozen_deterministic_two_backend_parity"]["tenant_results"] == {
        "tenant-a": {"passed": 22, "total": 22},
        "tenant-b": {"passed": 22, "total": 22},
    }
    assert audit["model_ablation_backend_scope"]["status"] == "not_recorded_in_run_evidence"
    assert audit["v13_model_execution_gate"]["status"] == "blocked_pending_code_lock_review"
    fields = audit["model_run_manifest"]["field_status"]
    assert fields["model_id"] == {"value": None, "status": "not_recorded"}
    assert fields["dataset_sha256"] == {"value": None, "status": "not_recorded"}
    assert fields["cost_usd"] == {"value": None, "status": "unconfirmed"}
    assert fields["backend_execution"]["status"] == "not_recorded"
    assert fields["budget.price_estimate_usd"]["status"] == "recorded_separately_from_actual_cost"
    assert audit["model_run_manifest"]["trace_evidence_status"] == "unavailable"


def test_ip4_parity_report_records_safe_isolation_and_cleanup():
    audit = build_audit(PARITY)
    execution = audit["frozen_deterministic_two_backend_parity"]["execution_environment"]

    assert execution["env_file_autoload_disabled"] is True
    assert execution["postgres_storage"] == "tmpfs"
    assert execution["host_data_volume"] is False
    assert execution["project_cleanup"] == "passed_project_only"
    assert execution["remaining_project_containers"] == 0
    assert execution["remaining_project_networks"] == 0


def test_missing_intermediate_reports_are_disclosed_without_reconstruction():
    audit = build_audit(PARITY)

    assert audit["intermediate_attempt_recovery"]["exact_original_files_present"] is False
    assert audit["intermediate_attempt_recovery"]["recovery_attempts"]["unreachable_git_blobs_checked"] == 8
    assert audit["intermediate_attempt_recovery"]["recovery_attempts"]["exact_sha256_matches"] == 0
    assert audit["intermediate_attempt_recovery"]["reconstructed_json"] is False
    assert len(audit["superseded_intermediate_attempts"]) == 2
