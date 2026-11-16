"""Run the deterministic v14 baseline on SQLite and both PostgreSQL tenants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.ip4_v14_evaluate import case_score_vector, evaluate
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine


def _parity_view(report: dict) -> dict[str, tuple]:
    return case_score_vector(report)


def evaluate_all(database: Path, blind: Path, gold: Path, manifest: Path, app_dsn: str) -> dict:
    reports = []
    sqlite_engine = SQLiteQueryEngine(database)
    reports.append(evaluate(database, blind, gold, manifest, query_engine=sqlite_engine))
    for tenant_id in ("tenant-a", "tenant-b"):
        engine = PostgresQueryEngine(app_dsn, tenant_id=tenant_id)
        try:
            reports.append(evaluate(database, blind, gold, manifest,
                                    query_engine=engine, tenant_id=tenant_id))
        finally:
            engine.close()
    sqlite_view = _parity_view(reports[0])
    parity = []
    for report in reports[1:]:
        matched = sqlite_view == _parity_view(report)
        parity.append({"backend": report["backend"], "tenant_id": report["tenant_id"],
                       "matches_sqlite_case_scores": matched})
        if not matched:
            raise RuntimeError(f"v14 {report['tenant_id']} case scores differ from SQLite")
    return {"status": "passed", "model_calls": 0, "backends": reports,
            "parity": parity, "candidate_manifest_sha256": hashlib.sha256(
                manifest.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="private v14 candidate directory containing manifest.json and synthetic.sqlite")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app_dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not app_dsn:
        parser.error("set INSIGHTPILOT_IP4_APP_DSN for the isolated synthetic PostgreSQL fixture")
    report = evaluate_all(args.candidate / "synthetic.sqlite", args.candidate / "blind.jsonl",
                          args.candidate / "gold.jsonl", args.candidate / "manifest.json", app_dsn)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "model_calls": 0,
                      "parity": report["parity"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
