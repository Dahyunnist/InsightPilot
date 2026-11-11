"""Evaluate the frozen IP4 v3 suite with RulePlanner on SQLite and PostgreSQL tenants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.ip4_evaluate import compare
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.rule_baseline import RulePlanner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(backend: str, tenant_id: str | None, details: list[dict], gold: dict[str, dict]) -> dict:
    by_domain = {}
    for domain in sorted({row["domain"] for row in details}):
        subset = [row for row in details if row["domain"] == domain]
        by_domain[domain] = {"passed": sum(row["passed"] for row in subset), "total": len(subset),
                             "failures": [row["case_id"] for row in subset if not row["passed"]]}
    refusals = [row for row in details if gold[row["case_id"]]["expected_status"] == "refused"]
    return {"backend": backend, "tenant_id": tenant_id,
            "passed": sum(row["passed"] for row in details), "total": len(details),
            "by_domain": by_domain,
            "answerable": {"passed": sum(row["passed"] for row in details
                                          if gold[row["case_id"]]["expected_status"] == "answered"),
                           "total": sum(row["expected_status"] == "answered" for row in gold.values())},
            "refusals": {"passed": sum(row["passed"] for row in refusals), "total": len(refusals),
                         "false_accepts": [row["case_id"] for row in refusals
                                           if row["actual_status"] != "refused"]},
            "failures": [row for row in details if not row["passed"]], "cases": details}


def evaluate(database: Path, blind_path: Path, gold_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha(blind_path) != manifest["blind_sha256"] or _sha(gold_path) != manifest["gold_sha256"]:
        raise ValueError("v3 frozen blind/gold hash mismatch")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in
            (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    if len(cases) != manifest["case_count"] or set(gold) != {row["case_id"] for row in cases}:
        raise ValueError("v3 case count or blind/gold IDs do not match manifest")
    database = database.resolve()
    runs = []

    sqlite_engine = SQLiteQueryEngine(database)
    version, fingerprint = sqlite_engine.read_data_identity()
    if (version, fingerprint) != (manifest["data_version"], manifest["data_fingerprint"]):
        raise ValueError("SQLite fixture identity differs from v3 manifest")
    arms = [("sqlite", None, sqlite_engine)]
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise RuntimeError("Set test-only INSIGHTPILOT_IP4_APP_DSN for PostgreSQL tenant evaluation")
    arms.extend(("postgresql", tenant, PostgresQueryEngine(dsn, tenant_id=tenant))
                for tenant in ("tenant-a", "tenant-b"))

    for backend, tenant_id, engine in arms:
        try:
            engine_version, engine_fingerprint = engine.read_data_identity(tenant_id=tenant_id)
            if (engine_version, engine_fingerprint) != (manifest["data_version"], manifest["data_fingerprint"]):
                raise ValueError(f"{backend}/{tenant_id} fixture identity differs from v3 manifest")
            core = InsightPilot(RulePlanner(), database, query_engine=engine, tenant_id=tenant_id)
            details = []
            for case in cases:
                result = core.analyze(case["question"], tenant_id=tenant_id)
                score = compare(case, gold[case["case_id"]], result)
                details.append({**score, "terminal_stage": result.get("stage"),
                                "terminal_reason": result.get("reason"),
                                "failure_detail": result.get("failure_detail"),
                                "actual_plan": result.get("plan")})
            runs.append(_summary(backend, tenant_id, details, gold))
        finally:
            close = getattr(engine, "close", None)
            if close:
                close()
    return {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "completed",
            "suite_id": manifest["suite_id"], "mode": "offline_rule_baseline_no_model_calls",
            "planner": "RulePlanner", "model_calls": 0,
            "manifest_sha256": _sha(manifest_path), "blind_sha256": _sha(blind_path),
            "gold_sha256": _sha(gold_path), "data_version": manifest["data_version"],
            "data_fingerprint": manifest["data_fingerprint"], "case_count": len(cases), "runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data/insightpilot-multidomain.sqlite")
    parser.add_argument("--blind", type=Path, default=ROOT / "evals/ip4-v3-blind.jsonl")
    parser.add_argument("--gold", type=Path, default=ROOT / "evals/ip4-v3-gold.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evals/ip4-v3-manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/ip4-v3-offline-all-backends.json")
    args = parser.parse_args()
    report = evaluate(args.database, args.blind, args.gold, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "model_calls": 0,
                      "runs": [{"backend": run["backend"], "tenant_id": run["tenant_id"],
                                "passed": run["passed"], "total": run["total"],
                                "by_domain": run["by_domain"]} for run in report["runs"]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
