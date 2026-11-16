"""Offline IP4 v14 candidate scorer for SQLite and PostgreSQL query engines."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from .agent import InsightPilot
from .ip4_evaluate import compare as compare_ip4
from .query_engine import QueryEngine, SQLiteQueryEngine
from .rule_baseline import RulePlanner


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_result_sha256(result: dict) -> str:
    metric = result.get("metric")
    projection = {
        "status": result.get("status"), "reason": result.get("reason"),
        "stage": result.get("stage"), "failure_detail": result.get("failure_detail"),
        "metric_id": metric.get("id") if isinstance(metric, dict) else None,
        "plan": result.get("plan"), "rows": result.get("rows"),
        "verification_passed": (result.get("verification", {}).get("passed")
                                 if isinstance(result.get("verification"), dict) else None),
    }
    payload = json.dumps(projection, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def case_score_vector(report: dict) -> dict[str, tuple]:
    fields = ("passed", "actual_status", "actual_reason", "metric_ok", "dimension_ok",
              "region_ok", "date_window_ok", "numeric_ok", "independent_verifier_ok",
              "actual_result_sha256")
    return {row["case_id"]: tuple(row.get(field) for field in fields) for row in report["cases"]}


def adapt_gold(gold: dict) -> dict:
    """Map immutable v14 gold fields to the shared IP4 comparison contract."""
    adapted = dict(gold)
    if gold.get("expected_status") == "answered":
        required = ("metric", "dimension", "start", "end_exclusive", "rows")
        missing = [key for key in required if key not in gold]
        if missing:
            raise ValueError(f"v14 answered gold is missing fields: {', '.join(missing)}")
        adapted["analysis_start"] = gold["start"]
        adapted["analysis_end_exclusive"] = gold["end_exclusive"]
        adapted.setdefault("region", None)
        adapted["expected_rows"] = gold["rows"]
    return adapted


def compare(case: dict, gold: dict, result: dict) -> dict:
    compared = compare_ip4(case, adapt_gold(gold), result)
    compared["actual_result_sha256"] = _semantic_result_sha256(result)
    return compared


def evaluate(database: Path, blind_path: Path, gold_path: Path, manifest_path: Path, *,
             query_engine: QueryEngine | None = None, tenant_id: str | None = None) -> dict:
    """Score a candidate using RulePlanner only; never creates a model planner."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for path, field in ((blind_path, "blind_sha256"), (gold_path, "gold_sha256"),
                        (database, "database_sha256")):
        expected = manifest.get(field)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError(f"v14 manifest has invalid {field}")
        if _sha256_file(path) != expected:
            artifact = field.removesuffix("_sha256")
            raise ValueError(f"v14 {artifact} file hash does not match the candidate manifest")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    if (len(cases) != manifest["case_count"] or len(gold_by_id) != len(gold_rows) or
            {row["case_id"] for row in cases} != set(gold_by_id)):
        raise ValueError("v14 blind/gold case IDs or counts do not match the manifest")
    engine = query_engine or SQLiteQueryEngine(database)
    if engine.requires_tenant_scope and not tenant_id:
        raise ValueError("PostgreSQL candidate scoring requires an explicit tenant_id")
    if not engine.requires_tenant_scope and tenant_id is not None:
        raise ValueError("SQLite candidate scoring does not accept tenant_id")
    version, fingerprint = engine.read_data_identity(tenant_id=tenant_id)
    if (version, fingerprint) != (manifest["data_version"], manifest["data_fingerprint"]):
        raise ValueError("query-engine data identity differs from the v14 manifest")

    core = InsightPilot(RulePlanner(), database, query_engine=engine,
                        tenant_id=tenant_id, tracer=None)
    details = []
    for case in cases:
        result = core.analyze(case["question"], tenant_id=tenant_id)
        details.append(compare(case, gold_by_id[case["case_id"]], result))
    by_domain = {}
    for domain in sorted({row["domain"] for row in details}):
        scoped = [row for row in details if row["domain"] == domain]
        by_domain[domain] = {"passed": sum(row["passed"] for row in scoped),
                             "total": len(scoped),
                             "failures": [row["case_id"] for row in scoped if not row["passed"]]}
    return {"status": "completed", "suite_id": manifest["suite_id"],
            "backend": engine.backend_name, "tenant_id": tenant_id,
            "mode": "offline_rule_baseline_no_model_calls", "model_calls": 0,
            "blind_sha256": manifest["blind_sha256"], "gold_sha256": manifest["gold_sha256"],
            "data_version": version, "data_fingerprint": fingerprint,
            "case_count": len(cases), "passed": sum(row["passed"] for row in details),
            "by_domain": by_domain, "cases": details}
