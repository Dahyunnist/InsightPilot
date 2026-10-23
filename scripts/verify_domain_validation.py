"""Verify domain-separated, independently gold-scored cases without model calls."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightpilot.agent import InsightPilot
from insightpilot.ip4_evaluate import compare
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.rule_baseline import RulePlanner


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/insightpilot-multidomain.sqlite"
SET_ROOT = ROOT / "evals/domain-validation-v1"
DOMAINS = ("refunds", "marketing_roi")
TENANTS = ("tenant-a", "tenant-b")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(domain: str) -> tuple[dict, list[dict], dict[str, dict]]:
    blind_path = SET_ROOT / f"{domain}-blind-v1.jsonl"
    gold_path = SET_ROOT / f"{domain}-gold-v1.jsonl"
    manifest_path = SET_ROOT / f"{domain}-manifest-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha(blind_path) != manifest["blind_sha256"] or _sha(gold_path) != manifest["gold_sha256"]:
        raise ValueError(f"{domain}: validation file hash mismatch")
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in
            (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    return manifest, cases, gold


def _run(backend: str, tenant_id: str | None, domain: str, cases: list[dict],
         gold: dict[str, dict], dsn: str | None) -> dict:
    engine = (SQLiteQueryEngine(DATABASE) if backend == "sqlite" else
              PostgresQueryEngine(dsn or "", tenant_id=tenant_id))
    core = InsightPilot(RulePlanner(), DATABASE, query_engine=engine)
    details = []
    for case in cases:
        result = core.analyze(case["question"], tenant_id=tenant_id)
        details.append(compare(case, gold[case["case_id"]], result))
    close_engine = getattr(engine, "close", None)
    if close_engine is not None:
        close_engine()
    answerable_ids = {case_id for case_id, row in gold.items() if row["expected_status"] == "answered"}
    guard_ids = set(gold) - answerable_ids
    return {
        "backend": backend, "tenant_id": tenant_id, "domain": domain,
        "passed": sum(row["passed"] for row in details), "total": len(details),
        "answerable_passed": sum(row["passed"] for row in details if row["case_id"] in answerable_ids),
        "answerable_total": len(answerable_ids),
        "guard_passed": sum(row["passed"] for row in details if row["case_id"] in guard_ids),
        "guard_total": len(guard_ids),
        "false_refusals": [row["case_id"] for row in details
            if row["case_id"] in answerable_ids and row["actual_status"] == "refused"],
        "false_accepts": [row["case_id"] for row in details
            if row["case_id"] in guard_ids and row["actual_status"] == "answered"],
        "numeric_or_plan_failures": [row["case_id"] for row in details
            if row["case_id"] in answerable_ids and not row["passed"]],
        "failures": [row for row in details if not row["passed"]],
        "model_calls": 0,
    }


def verify(dsn: str | None = None) -> dict:
    runs = []
    for domain in DOMAINS:
        manifest, cases, gold = _load(domain)
        sqlite = _run("sqlite", None, domain, cases, gold, None)
        runs.append({"manifest": manifest, "result": sqlite})
        if dsn:
            for tenant_id in TENANTS:
                result = _run("postgresql", tenant_id, domain, cases, gold, dsn)
                runs.append({"manifest": manifest, "result": result})
    return {"created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "passed" if all(run["result"]["passed"] == run["result"]["total"]
                                      for run in runs) else "failed",
            "blind_ip4_modified": False, "model_calls": 0, "runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/domain-validation-v1.json")
    args = parser.parse_args()
    result = verify(os.environ.get("INSIGHTPILOT_IP4_APP_DSN"))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
