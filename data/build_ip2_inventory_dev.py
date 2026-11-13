"""Freeze the IP-2 inventory development set with independent Python/SQL gold."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from data.inventory_fixture import create_inventory_fixture
from insightpilot.freeze import sha256_text
from insightpilot.inventory_oracle import calculate_inventory_turnover
from insightpilot.planner import QueryPlan
from insightpilot.runner import execute_readonly, read_data_identity
from insightpilot.sql import compile_query


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    {"id": "IP2-INV-D01", "question": "2026-11-01 至 2026-11-03 库存周转率按商品类别是多少？",
     "start": "2026-11-01", "end_exclusive": "2026-11-04", "dimension": "inventory_category",
     "boundaries": ["Asia/Shanghai local dates", "inclusive local-day range", "NULL snapshot", "zero denominator"]},
    {"id": "IP2-INV-D02", "question": "2026-11-01 至 2026-11-03 整体库存周转率是多少？",
     "start": "2026-11-01", "end_exclusive": "2026-11-04", "dimension": None,
     "boundaries": ["daily inventory aggregation before average", "NULL snapshot", "zero denominator"]},
    {"id": "IP2-INV-D03", "question": "2026-11-01 当日库存周转率按商品类别是多少？",
     "start": "2026-11-01", "end_exclusive": "2026-11-02", "dimension": "inventory_category",
     "boundaries": ["local midnight maps to UTC 16:00", "left-closed right-open", "NULL snapshot", "zero denominator"]},
)


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")


def _assert_same(actual: list[dict], expected: list[dict]) -> None:
    if len(actual) != len(expected):
        raise ValueError("inventory SQL and Python gold row count differs")
    for left, right in zip(actual, expected):
        if left["group_value"] != right["group_value"] or left["numerator_cents"] != right["numerator_cents"]:
            raise ValueError("inventory SQL and Python gold dimensions or numerator differs")
        for field in ("denominator_cents", "value"):
            a, b = left[field], right[field]
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9):
                raise ValueError(f"inventory SQL and Python gold {field} differs")


def _data_identity(database: Path) -> tuple[str, str]:
    connection = sqlite3.connect(database)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()
    return metadata["data_version"], metadata["data_fingerprint"]


def build(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ip2-inventory-dev-", dir=ROOT / "data") as temp_dir:
        database = create_inventory_fixture(Path(temp_dir) / "inventory.sqlite")
        data_version, fingerprint = _data_identity(database)
        if read_data_identity(database) != (data_version, fingerprint):
            raise ValueError("inventory fixture identity mismatch")
        questions = []
        gold_rows = []
        for case in CASES:
            plan = QueryPlan("inventory_turnover", case["start"], case["end_exclusive"], None,
                             case["dimension"], ((case["start"], case["end_exclusive"]),), "1.0.0")
            python_rows = calculate_inventory_turnover(plan, database)
            sql_rows, actual_version = execute_readonly(database, compile_query(plan))
            if actual_version != data_version:
                raise ValueError("inventory SQL ran against a different data version")
            _assert_same(sql_rows, python_rows)
            questions.append({"case_id": case["id"], "question": case["question"]})
            gold_rows.append({"case_id": case["id"], "status": "answered", "metric_id": "inventory_turnover",
                              "metric_version": "1.0.0", "data_version": data_version,
                              "start_date": case["start"], "end_date_exclusive": case["end_exclusive"],
                              "dimension": case["dimension"], "boundaries": case["boundaries"],
                              "rows": python_rows, "sql_gold_crosscheck": "passed"})

    questions_path = output / "inventory-turnover-dev-v1.jsonl"
    gold_path = output / "inventory-turnover-dev-gold-v1.jsonl"
    _write(questions_path, questions)
    _write(gold_path, gold_rows)
    existing_prompts = set()
    for prior in (ROOT / "evals").rglob("*.jsonl"):
        if prior.resolve().is_relative_to(output.resolve()):
            continue
        for line in prior.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            question = row.get("question")
            if isinstance(question, str):
                existing_prompts.add(" ".join(question.split()))
    overlap = sorted(" ".join(case["question"].split()) for case in CASES
                     if " ".join(case["question"].split()) in existing_prompts)
    if overlap:
        raise ValueError("inventory development questions overlap an existing eval")
    manifest = {
        "suite_id": "ip2-inventory-turnover-dev-v1", "status": "frozen_development",
        "evaluation_role": "development_only", "is_blind": False, "model_calls": 0,
        "synthetic_only": True, "data_version": data_version, "data_fingerprint": fingerprint,
        "metric_id": "inventory_turnover", "metric_version": "1.0.0", "effective_from": "2026-11-01",
        "timezone": "Asia/Shanghai", "case_count": len(CASES),
        "gold_method": "independent Python inventory ledger; each case cross-checked against compiled read-only SQL",
        "questions_sha256": sha256_text(questions_path), "gold_sha256": sha256_text(gold_path),
        "exact_question_overlap_count": len(overlap), "exact_question_overlap": overlap,
        "boundaries": sorted({boundary for case in CASES for boundary in case["boundaries"]}),
        "questions_path": questions_path.name, "gold_path": gold_path.name,
    }
    (output / "inventory-turnover-dev-manifest-v1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/ip2-inventory-turnover-dev-v1")
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
