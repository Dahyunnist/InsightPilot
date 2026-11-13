"""Run the frozen IP4 v13 baseline with no model requests."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.enterprise_evaluate import _compare
from insightpilot.rule_baseline import RulePlanner
from insightpilot.run_manifest import validate_run_manifest


def main() -> None:
    blind_path = ROOT / "evals/ip4-v13-blind.jsonl"
    gold_path = ROOT / "evals/ip4-v13-gold.jsonl"
    manifest_path = ROOT / "evals/ip4-v13-manifest.json"
    database = ROOT / "data/insightpilot-ip4-v13.sqlite"
    cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {row["case_id"]: row for row in (
        json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(gold) != {case["case_id"] for case in cases}:
        raise SystemExit("v13 blind and gold case IDs differ")
    core = InsightPilot(RulePlanner(), database, tracer=None)
    evaluated = [(case, core.analyze(case["question"])) for case in cases]
    rows = [_compare(case, gold[case["case_id"]], result) for case, result in evaluated]
    answerable = {case_id for case_id, row in gold.items() if row.get("expected_status", "answered") == "answered"}
    report = {
        "suite_id": manifest["suite_id"], "planner": "RulePlanner", "model_calls": 0,
        "data_fingerprint": manifest["data_fingerprint"],
        "blind_sha256": manifest["blind_sha256"], "gold_sha256": manifest["gold_sha256"],
        "passed": sum(row["passed"] for row in rows), "total": len(rows),
        "answerable_passed": sum(row["passed"] for row in rows if row["case_id"] in answerable),
        "answerable_total": len(answerable),
        "guard_passed": sum(row["passed"] for row in rows if row["case_id"] not in answerable),
        "guard_total": len(rows) - len(answerable),
        "false_refusals": [case["case_id"] for case, result in evaluated
                           if case["case_id"] in answerable and result.get("status") != "answered"],
        "false_accepts": [case["case_id"] for case, result in evaluated
                          if case["case_id"] not in answerable and result.get("status") == "answered"],
        "cases": rows,
    }
    output = ROOT / "reports/ip4-v13-rule-baseline.json"
    report_bytes = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": 1, "project": "InsightPilot", "git_sha": git_sha,
        "dataset_id": manifest["suite_id"], "dataset_sha256": hashlib.sha256(blind_path.read_bytes()).hexdigest(),
        "split": "frozen_synthetic", "model_id": "none", "prompt_sha": None,
        "tool_schema_sha": None, "runtime_digest": None, "max_steps": 1, "max_tokens": None,
        "scorer_sha": hashlib.sha256((ROOT / "insightpilot/enterprise_evaluate.py").read_bytes()).hexdigest(),
        "gold_source": "independent", "cost_usd_estimate": 0.0, "run_id": hashlib.sha256(report_bytes).hexdigest()[:32],
        "trace_id": hashlib.sha256((git_sha + now).encode()).hexdigest()[:32], "started_at": now,
        "ended_at": now, "status": "completed", "evaluation_id": "ip4-v13-rule-baseline",
        "domain": "refunds+marketing_roi", "truth_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "result_fingerprint_sha256": hashlib.sha256(report_bytes).hexdigest(),
    }
    validate_run_manifest(manifest)
    (ROOT / "reports/ip4-v13-rule-baseline-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
