"""Freeze a small independent IP4 v2 contract set without model calls."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .build_enterprise_v5 import _answer, _rows
from .build_marketing_roi_holdout import _python_gold
from .build_ip4_holdout import _assert_same_rows
from insightpilot.freeze import sha256_text
from insightpilot.planner import QueryPlan
from insightpilot.runner import execute_readonly
from insightpilot.sql import compile_query

CASES = [
    {"case_id": "IP4V2-R1", "domain": "refunds",
     "question": "2026-10-01 至 2026-10-31 华东按销售渠道的批准退款金额率（退款金额占实付金额）分别是多少？",
     "metric": "approved_refund_amount_rate", "dimension": "channel", "region": "华东",
     "start": "2026-10-01", "end": "2026-11-01"},
    {"case_id": "IP4V2-M1", "domain": "marketing_roi",
     "question": "2026-02-01 至 2026-02-28 各营销活动净 ROI 分别是多少？",
     "metric": "marketing_net_roi", "dimension": "campaign", "region": None,
     "start": "2026-02-01", "end": "2026-03-01"},
    {"case_id": "IP4V2-R2", "domain": "refunds",
     "question": "2026-10-01 至 2026-10-31 华东退款率是多少？", "expected_status": "clarify"},
    {"case_id": "IP4V2-M2", "domain": "marketing_roi",
     "question": "2026-02-01 至 2026-02-28 华南地区营销净 ROI 是多少？",
     "expected_status": "clarify"},
]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")


def build(database: Path, output: Path) -> dict:
    connection = sqlite3.connect(database)
    try:
        meta = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()
    if meta.get("data_version") != "insightpilot-multidomain-v1":
        raise ValueError("IP4 v2 requires the fixed synthetic multidomain dataset")

    project_root = Path(__file__).resolve().parents[1]
    prior = list((project_root / "evals").rglob("*.jsonl"))
    prior.extend((project_root / "reports").rglob("*.jsonl"))
    normalized = {" ".join(case["question"].casefold().split()) for case in CASES}
    overlap = set()
    for path in prior:
        if path.name == "ip4-v2-blind.jsonl":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                question = json.loads(line).get("question")
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(question, str) and " ".join(question.casefold().split()) in normalized:
                overlap.add(question)
    if overlap:
        raise ValueError("IP4 v2 prompts overlap prior evaluation text")

    _meta, orders, refunds, products, campaigns, attributions = _rows(database)
    blind, gold = [], []
    for case in CASES:
        blind.append({"case_id": case["case_id"], "question": case["question"]})
        base = {"case_id": case["case_id"], "domain": case["domain"],
                "data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"]}
        if case.get("expected_status"):
            gold.append({**base, "expected_status": case["expected_status"]})
            continue
        plan = QueryPlan(case["metric"], case["start"], case["end"], case["region"], case["dimension"])
        sql_rows, _ = execute_readonly(database, compile_query(plan))
        if case["domain"] == "refunds":
            spec = (case["metric"], case["dimension"], case["region"], case["start"], case["end"])
            values = _answer(spec, orders, refunds, products, campaigns, attributions)
            python_rows = [{"group_value": key, **value} for key, value in sorted(values.items())]
        else:
            python_rows = _python_gold(database, case["start"], case["end"], case["dimension"])
        _assert_same_rows(case["case_id"], sql_rows, python_rows)
        gold.append({**base, "expected_status": "answered", "metric": case["metric"],
                     "dimension": case["dimension"], "region": case["region"],
                     "analysis_start": case["start"], "analysis_end_exclusive": case["end"],
                     "expected_rows": python_rows})

    output.mkdir(parents=True, exist_ok=True)
    blind_path, gold_path = output / "ip4-v2-blind.jsonl", output / "ip4-v2-gold.jsonl"
    _write(blind_path, blind)
    _write(gold_path, gold)
    manifest = {
        "suite_id": "insightpilot-ip4-independent-v2", "case_count": len(CASES),
        "domains": {"refunds": 2, "marketing_roi": 2}, "answerable_case_count": 2,
        "guard_case_count": 2, "data_version": meta["data_version"],
        "data_fingerprint": meta["data_fingerprint"], "seed": meta["seed"],
        "snapshot_at": meta["snapshot_at"], "blind_sha256": sha256_text(blind_path),
        "gold_sha256": sha256_text(gold_path), "synthetic_only": True,
        "gold_method": "independent Python retail row ledger and marketing event ledger, cross-checked against compiled read-only SQLite SQL",
        "exact_normalized_prompt_overlap_count": 0,
        "scope": "Small development holdout for contract regression; not a model or production accuracy estimate.",
    }
    (output / "ip4-v2-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    import argparse
    from .multidomain_seed import create_multidomain_database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("evals"))
    args = parser.parse_args()
    if not args.database.exists():
        create_multidomain_database(args.database)
    print(json.dumps(build(args.database, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
