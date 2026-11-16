"""Compare frozen IP4 v13 deterministic RulePlanner policies; never calls a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from scripts.run_ip4_offline_audit import BUNDLE, DEFAULT_DATABASE, run as run_offline_audit

DEFAULT_OUTPUT = ROOT / "reports/ip4-v13-deterministic-comparison-2026-11-15.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arm_summary(rows: list[dict], gold_by_id: dict[str, dict], *, verifier: bool) -> dict:
    failures = []
    answerable_passed = answerable_total = guard_passed = guard_total = 0
    false_refusals, false_accepts = [], []
    for row in rows:
        expected_status = gold_by_id[row["case_id"]].get("expected_status", "answered")
        answerable = expected_status == "answered"
        if answerable:
            answerable_total += 1
            answerable_passed += int(row["passed"])
            if row.get("actual_status") in {"clarify", "refused"}:
                false_refusals.append(row["case_id"])
        else:
            guard_total += 1
            guard_passed += int(row["passed"])
            if row.get("actual_status") == "answered":
                false_accepts.append(row["case_id"])
        if not row["passed"]:
            failures.append({
                "case_id": row["case_id"],
                "failed_checks": [key for key in (
                    "status_ok", "reason_ok", "metric_ok", "dimension_ok", "region_ok",
                    "numeric_ok", "date_window_ok", "sql_verified",
                ) if row.get(key) is False and (verifier or key != "sql_verified")],
                "actual_status": row.get("actual_status"),
            })
    return {
        "passed": sum(bool(row["passed"]) for row in rows),
        "total": len(rows),
        "answerable_passed": answerable_passed,
        "answerable_total": answerable_total,
        "guard_passed": guard_passed,
        "guard_total": guard_total,
        "independent_verifier_enabled": verifier,
        "false_refusals": false_refusals,
        "false_accepts": false_accepts,
        "failures": failures,
    }


def _database_parity(raw: dict, postgres_requested: bool) -> dict:
    parity = raw.get("postgresql_sqlite_result_parity", {})
    if not postgres_requested:
        return {"status": "not_run", "tenants": {}}
    tenants = {
        tenant: {
            "passed": result.get("passed", 0),
            "total": result.get("total", 0),
            "failures": result.get("failures", []),
        }
        for tenant, result in parity.items()
    }
    passed = len(tenants) == 2 and all(item["passed"] == item["total"] == 22 for item in tenants.values())
    return {"status": "passed" if passed else "failed", "tenants": tenants}


def run(database: Path = DEFAULT_DATABASE, output: Path | None = DEFAULT_OUTPUT,
        postgres_dsn: str | None = None, run_id: str | None = None) -> dict:
    source_manifest_path = ROOT / "evals/ip4-v13-manifest.json"
    source_blind_path = ROOT / "evals/ip4-v13-blind.jsonl"
    source_gold_path = ROOT / "evals/ip4-v13-gold.jsonl"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))

    bundle_manifest_path = BUNDLE / "manifest.json"
    bundle_manifest = json.loads(bundle_manifest_path.read_text(encoding="utf-8"))
    source_v13 = bundle_manifest.get("source_v13", {})
    frozen_hashes = {
        "manifest_sha256": _sha(source_manifest_path),
        "blind_sha256": _sha(source_blind_path),
        "gold_sha256": _sha(source_gold_path),
    }
    if (source_v13.get("manifest_sha256") != frozen_hashes["manifest_sha256"]
            or source_v13.get("blind_sha256") != frozen_hashes["blind_sha256"]
            or source_v13.get("gold_sha256") != frozen_hashes["gold_sha256"]):
        raise ValueError("Frozen v13 manifest/blind/gold hashes differ from offline bundle provenance")

    raw = run_offline_audit(bundle=BUNDLE, database=database, output=None, postgres_dsn=postgres_dsn,
                            include_model_accounting=False)
    postgres_version = None
    if postgres_dsn:
        import psycopg
        with psycopg.connect(postgres_dsn) as connection:
            postgres_version = connection.execute("SHOW server_version").fetchone()[0]
    gold_by_id = {}
    for domain, files in bundle_manifest["domains"].items():
        rows = [json.loads(line) for line in (BUNDLE / files["gold_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()]
        gold_by_id.update({row["case_id"]: row for row in rows})

    verifier_rows = raw["ruleplanner_sqlite"]["case_results"]
    no_verifier_rows = raw["ruleplanner_without_independent_verifier_sqlite"]["case_results"]
    database_parity = _database_parity(raw, bool(postgres_dsn))
    created_at = datetime.now(timezone.utc).isoformat()
    report = {
        "report_id": "insightpilot-ip4-v13-deterministic-comparison-" +
                     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": raw["status"],
        "deterministic_parity_status": database_parity["status"],
        "run_id": run_id,
        "created_at_utc": created_at,
        "evaluation_type": "deterministic_rule_policy_comparison_not_llm",
        "suite_id": source_manifest["suite_id"],
        "offline_bundle_suite_id": raw["suite_id"],
        "backend": "SQLite + PostgreSQL" if postgres_dsn else "SQLite",
        "postgresql_server_version": postgres_version,
        "backend_scope": {
            "sqlite": "ruleplanner_with_independent_verifier",
            "postgresql_tenants": ["tenant-a", "tenant-b"] if postgres_dsn else [],
            "parity_basis": "same frozen question set and data fingerprint; deterministic result equality",
        },
        "synthetic_only": True,
        "model_calls": 0,
        "model_requests_sent": 0,
        "estimated_model_cost_usd": 0.0,
        "case_count": source_manifest["case_count"],
        "data_fingerprint": source_manifest["data_fingerprint"],
        "frozen_hashes": frozen_hashes,
        "arms": {
            "ruleplanner_with_independent_verifier": _arm_summary(verifier_rows, gold_by_id, verifier=True),
            "ruleplanner_without_independent_verifier": _arm_summary(no_verifier_rows, gold_by_id, verifier=False),
        },
        "database_parity": database_parity,
        "metric_retrieval_candidate_coverage": raw["metric_retrieval_candidate_coverage"],
        "comparison_note": (
            "Both arms use the deterministic RulePlanner and the same frozen synthetic questions/database. "
            "The no-verifier score intentionally omits verifier success; equal totals do not prove verifier equivalence. "
            "This is not a live-model result. PostgreSQL parity, when requested, compares deterministic outputs only."
        ),
        "payload_note": "No questions, gold values, SQL, parameters, query rows, or private data are emitted.",
        "scoring_method": "Independent local frozen-gold scorer; gold was never passed to a model.",
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--postgres-dsn", default=os.environ.get("INSIGHTPILOT_IP4_APP_DSN"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    report = run(database=args.database, output=args.output, postgres_dsn=args.postgres_dsn,
                 run_id=args.run_id)
    print(json.dumps({
        "report_id": report["report_id"],
        "model_calls": report["model_calls"],
        "with_verifier": {key: report["arms"]["ruleplanner_with_independent_verifier"][key]
                          for key in ("passed", "total", "false_refusals", "false_accepts")},
        "without_verifier": {key: report["arms"]["ruleplanner_without_independent_verifier"][key]
                             for key in ("passed", "total", "false_refusals", "false_accepts")},
        "database_parity": report["database_parity"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
