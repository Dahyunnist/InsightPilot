"""Audit IP-2 metric metadata and freeze an explicitly non-blind boundary bundle."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.metrics import METRICS, catalog_document
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner


KNOWN_DATA_VERSIONS = {
    "insightpilot-s0-v1", "insightpilot-enterprise-v1", "insightpilot-multidomain-v1",
    "insightpilot-inventory-v1",
}
INVENTORY_DIR = ROOT / "evals/ip2-inventory-turnover-dev-v1"


def _sha(path: Path) -> str:
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_sha(value: object) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _catalog_errors() -> list[str]:
    errors = []
    semver = re.compile(r"^\d+\.\d+\.\d+$")
    for metric_id, metric in METRICS.items():
        prefix = f"{metric_id}: "
        if not semver.fullmatch(metric.definition_version):
            errors.append(prefix + "invalid definition version")
        try:
            date.fromisoformat(metric.effective_from)
        except ValueError:
            errors.append(prefix + "invalid effective date")
        try:
            ZoneInfo(metric.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(prefix + "invalid IANA timezone")
        for field, value in (("source_tables", metric.source_tables), ("lineage", metric.lineage),
                             ("freshness", metric.freshness), ("example_question", metric.example_question),
                             ("access_scopes", metric.access_scopes),
                             ("required_data_versions", metric.required_data_versions)):
            if not value:
                errors.append(prefix + f"missing {field}")
        source_path = ROOT / metric.definition_source.split("#", 1)[0]
        if "#" not in metric.definition_source or not source_path.is_file():
            errors.append(prefix + "definition source file or anchor missing")
        unsupported = set(metric.required_data_versions) - KNOWN_DATA_VERSIONS
        if unsupported:
            errors.append(prefix + "unsupported data versions: " + ",".join(sorted(unsupported)))
    return errors


def _boundary_manifest() -> dict:
    refund_path = ROOT / "evals/cases.jsonl"
    refund_cases = [json.loads(line) for line in refund_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    refund_ids = {row["id"] for row in refund_cases}
    required_refund = {"N01", "N02", "N08", "N11", "N12", "A02"}
    if not required_refund <= refund_ids:
        raise ValueError("S0 development cases miss required refund boundaries")

    marketing_manifest_path = ROOT / "evals/marketing-roi-manifest-v1.json"
    marketing = json.loads(marketing_manifest_path.read_text(encoding="utf-8"))
    inventory_manifest_path = INVENTORY_DIR / "inventory-turnover-dev-manifest-v1.json"
    inventory = json.loads(inventory_manifest_path.read_text(encoding="utf-8"))
    if inventory.get("evaluation_role") != "development_only" or inventory.get("is_blind") is not False:
        raise ValueError("IP-2 inventory suite must remain explicitly non-blind development data")
    sources = {
        "refunds": {
            "suite_id": "insightpilot-s0-fixed-development-cases", "evaluation_role": "development_only",
            "case_ids": sorted(required_refund), "cases_path": "evals/cases.jsonl",
            "cases_sha256": _sha(refund_path),
            "boundaries": ["multiple partial refunds", "UTC half-open periods", "NULL zero denominator",
                           "order cohort vs refund time", "ambiguous refund-rate clarification"],
            "gold_basis": "fixed hand-calculated contract rows; SQL semantics asserted by regression suite",
        },
        "marketing_roi": {
            "suite_id": marketing["suite_id"], "evaluation_role": "development_only", "is_blind": False,
            "manifest_sha256": _sha(marketing_manifest_path), "data_fingerprint": marketing["data_fingerprint"],
            "dev_questions_sha256": marketing["splits"]["dev"]["blind_sha256"],
            "dev_gold_sha256": marketing["splits"]["dev"]["gold_sha256"],
            "gold_basis": marketing["gold_method"],
            "boundaries": ["duplicate business events", "revision and void", "UTC month edge",
                           "refund reversal snapshot", "NULL ROI when spend is zero"],
        },
        "inventory_turnover": {
            "suite_id": inventory["suite_id"], "evaluation_role": inventory["evaluation_role"],
            "is_blind": inventory["is_blind"], "manifest_sha256": _sha(inventory_manifest_path),
            "questions_sha256": inventory["questions_sha256"], "gold_sha256": inventory["gold_sha256"],
            "data_fingerprint": inventory["data_fingerprint"], "gold_basis": inventory["gold_method"],
            "boundaries": inventory["boundaries"],
        },
    }
    return {
        "suite_id": "ip2-cross-domain-boundary-development-v1", "status": "frozen_development",
        "evaluation_role": "development_only", "is_blind": False, "model_calls": 0,
        "synthetic_only": True, "sources": sources,
    }


def _ambiguity_probe() -> dict:
    planner = OpenAICompatiblePlanner.__new__(OpenAICompatiblePlanner)
    planner.use_metric_retrieval = True
    calls = []

    def no_model(*_args):
        calls.append(True)
        raise AssertionError("catalog audit must not call a model")

    planner._complete = no_model
    try:
        planner.plan("2026-10-01 至 2026-10-31 退款率是多少？")
    except NeedsClarification as exc:
        return {"status": "clarified", "choices": exc.choices, "model_calls": len(calls)}
    raise ValueError("ambiguous refund rate was not clarified")


def build_reports() -> tuple[dict, dict]:
    catalog = catalog_document()
    errors = _catalog_errors()
    ambiguity = _ambiguity_probe()
    if ambiguity["model_calls"] != 0 or ambiguity["status"] != "clarified":
        errors.append("ambiguous refund-rate probe did not fail closed")
    boundaries = _boundary_manifest()
    catalog_report = {
        "report_version": 1, "status": "passed" if not errors else "failed",
        "catalog_version": catalog["catalog_version"], "catalog_sha256": _canonical_sha(catalog),
        "known_data_versions": sorted(KNOWN_DATA_VERSIONS), "metric_count": len(METRICS),
        "errors": errors, "ambiguous_refund_rate_probe": ambiguity,
        "model_calls": 0,
        "metrics": [{
            "metric_id": metric.metric_id, "definition_version": metric.definition_version,
            "effective_from": metric.effective_from, "business_domain": metric.business_domain,
            "definition_source": metric.definition_source, "source_tables": list(metric.source_tables),
            "lineage": list(metric.lineage), "timezone": metric.timezone, "freshness": metric.freshness,
            "required_data_versions": list(metric.required_data_versions),
        } for metric in sorted(METRICS.values(), key=lambda item: item.metric_id)],
        "boundary_manifest_sha256": _canonical_sha(boundaries),
    }
    return boundaries, catalog_report


def main() -> None:
    boundaries, catalog_report = build_reports()
    if catalog_report["status"] != "passed":
        raise SystemExit("IP-2 catalog audit failed: " + "; ".join(catalog_report["errors"]))
    boundary_path = ROOT / "reports/ip2-boundary-development-manifest-v1.json"
    catalog_path = ROOT / "reports/ip2-metric-catalog-audit-v1.json"
    boundary_path.write_text(json.dumps(boundaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": catalog_report["status"], "metrics": catalog_report["metric_count"],
                      "development_sources": len(boundaries["sources"]), "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
