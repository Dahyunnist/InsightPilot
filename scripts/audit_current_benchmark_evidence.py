"""Audit evidence-bundle completeness for the frozen current model benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from insightpilot.sql_policy import ALLOWED_COLUMNS, ALLOWED_TABLES, SQLPolicyError, validate_sql

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "reports" / "current-benchmark" / "model-raw.jsonl"
REPORT = ROOT / "reports" / "current-benchmark" / "evidence-audit.json"
CATALOG_SNAPSHOT = ROOT / "reports" / "current-benchmark" / "metric-catalog-snapshot.json"


def _source_heading_exists(source: str) -> bool:
    file_name, separator, heading = source.partition("#")
    if not separator or not heading:
        return False
    path = (ROOT / file_name).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        return False
    return any(line.startswith("#") and line.lstrip("# ").strip() == heading
               for line in path.read_text(encoding="utf-8").splitlines())


def _lineage_is_allowed(lineage: Any) -> bool:
    if not isinstance(lineage, list) or not lineage:
        return False
    for item in lineage:
        if not isinstance(item, str) or item.count(".") != 1:
            return False
        table, column = item.split(".", 1)
        if table not in ALLOWED_TABLES or column not in ALLOWED_COLUMNS:
            return False
    return True


def audit(raw_path: Path = RAW) -> dict[str, Any]:
    source_bytes = raw_path.read_bytes()
    rows = [json.loads(line) for line in source_bytes.decode("utf-8").splitlines() if line.strip()]
    catalog_snapshot = json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8"))
    frozen_metrics = catalog_snapshot["metrics"]
    results: list[dict[str, Any]] = []
    for row in rows:
        result = row.get("result")
        if not isinstance(result, dict) or result.get("status") != "answered":
            continue
        payload = result.get("metric") if isinstance(result.get("metric"), dict) else {}
        metric = frozen_metrics.get(payload.get("id"))
        checks = {
            "metric_registered": metric is not None,
            "definition_version_matches": metric is not None and payload.get("definition_version") == metric["definition_version"],
            "formula_matches": metric is not None and payload.get("formula") == metric["formula"],
            "time_basis_matches": metric is not None and payload.get("time_basis") == metric["time_basis"],
            "refund_status_matches": metric is not None and payload.get("refund_status") == metric["refund_status"],
            "definition_source_matches": metric is not None and payload.get("definition_source") == metric["definition_source"],
            "source_heading_exists": metric is not None and _source_heading_exists(payload.get("definition_source", "")),
            "lineage_matches_catalog": metric is not None and payload.get("lineage") == metric["lineage"],
            "lineage_columns_allowlisted": _lineage_is_allowed(payload.get("lineage")),
            "sql_passes_ast_policy": False,
            "runtime_verification_passed": result.get("verification", {}).get("passed") is True,
        }
        try:
            validate_sql(result.get("sql"))
            checks["sql_passes_ast_policy"] = True
        except (SQLPolicyError, TypeError):
            pass
        results.append({
            "case_id": row.get("case_id"),
            "complete": all(checks.values()),
            "checks": checks,
        })

    counts = {key: sum(item["checks"][key] for item in results)
              for key in (results[0]["checks"] if results else [])}
    return {
        "suite_id": "insightpilot-current-benchmark-evidence-audit-v1",
        "status": "completed" if results and all(item["complete"] for item in results) else "partial",
        "raw_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "catalog_source_commit": catalog_snapshot["catalog_source_commit"],
        "catalog_snapshot_sha256": hashlib.sha256(CATALOG_SNAPSHOT.read_bytes()).hexdigest(),
        "answered_cases": len(results),
        "complete_evidence_bundles": sum(item["complete"] for item in results),
        "checks_passed_by_case_count": counts,
        "model_calls": 0,
        "definition": "catalog and source-link consistency against the catalog frozen at evaluation time, allowlisted lineage, SQL AST validity, and recorded runtime verification; does not score claim-level source support",
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "status", "answered_cases", "complete_evidence_bundles", "model_calls", "raw_sha256",
    )}, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
