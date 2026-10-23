"""Build separate, model-free validation sets for refunds and marketing ROI."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from data.build_marketing_roi_holdout import _python_gold as marketing_oracle
from insightpilot.freeze import sha256_text
from insightpilot.planner import QueryPlan
from insightpilot.runner import execute_readonly, read_data_identity
from insightpilot.sql import compile_query


CASES = {
    "refunds": [
        {"case_id": "DV-R01", "question": "2026-10-01 至 2026-10-14 批准退款订单率是多少？",
         "metric": "approved_refund_order_rate", "dimension": None, "region": None,
         "start": "2026-10-01", "end": "2026-10-15"},
        {"case_id": "DV-R02", "question": "2026-10-01 至 2026-10-31 按渠道统计批准退款金额率。",
         "metric": "approved_refund_amount_rate", "dimension": "channel", "region": None,
         "start": "2026-10-01", "end": "2026-11-01"},
        {"case_id": "DV-R03", "question": "2026-11-01 至 2026-11-30 按地区分组的批准退款订单率。",
         "metric": "approved_refund_order_rate", "dimension": "region", "region": None,
         "start": "2026-11-01", "end": "2026-12-01"},
        {"case_id": "DV-R04", "question": "2026-10-01 至 2026-10-31 按月趋势展示批准退款订单率。",
         "metric": "approved_refund_order_rate", "dimension": "month_utc", "region": None,
         "start": "2026-10-01", "end": "2026-11-01"},
        {"case_id": "DV-R05", "question": "2026-10-01 至 2026-10-31 同时给批准退款订单率和金额率。",
         "expected_status": "clarify"},
        {"case_id": "DV-R06", "question": "2026-10-01 至 2026-10-31 按华北和华东地区拆分退款订单率。",
         "expected_status": "clarify"},
    ],
    "marketing_roi": [
        {"case_id": "DV-M01", "question": "2026-01-01 至 2026-01-31 各营销活动的净ROI是多少？",
         "metric": "marketing_net_roi", "dimension": "campaign", "region": None,
         "start": "2026-01-01", "end": "2026-02-01"},
        {"case_id": "DV-M02", "question": "2026-10-01 至 2026-10-31 整体营销ROI是多少？",
         "metric": "marketing_net_roi", "dimension": None, "region": None,
         "start": "2026-10-01", "end": "2026-11-01"},
        {"case_id": "DV-M03", "question": "2026-10-31当天各营销活动净投资回报率是多少？",
         "metric": "marketing_net_roi", "dimension": "campaign", "region": None,
         "start": "2026-10-31", "end": "2026-11-01"},
        {"case_id": "DV-M04", "question": "2026-01-15 至 2026-02-14 每个营销活动的净投资回报率是多少？",
         "metric": "marketing_net_roi", "dimension": "campaign", "region": None,
         "start": "2026-01-15", "end": "2026-02-15"},
        {"case_id": "DV-M05", "question": "各营销活动ROI是多少？", "expected_status": "clarify"},
        {"case_id": "DV-M06", "question": "2026-10-01 至 2026-10-31 华东各营销活动ROI是多少？",
         "expected_status": "clarify"},
    ],
}


def _write_once(path: Path, rows: list[dict]) -> None:
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError(f"Refusing to overwrite changed validation file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")


def _assert_no_prompt_overlap(project_root: Path) -> None:
    new_questions = {" ".join(case["question"].casefold().split())
                     for cases in CASES.values() for case in cases}
    existing = set()
    for path in (project_root / "evals").rglob("*.jsonl"):
        if "domain-validation-v1" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            question = row.get("question")
            if isinstance(question, str):
                existing.add(" ".join(question.casefold().split()))
    overlap = sorted(new_questions & existing)
    if overlap:
        raise ValueError(f"Validation prompts overlap an existing set: {overlap}")


def _check_rows(case_id: str, expected: list[dict], actual: list[dict]) -> None:
    def by_group(rows):
        return {row.get("group_value", "全部"): row for row in rows}
    left, right = by_group(expected), by_group(actual)
    if set(left) != set(right):
        raise ValueError(f"Independent Python/SQLite gold groups disagree: {case_id}")
    for group, row in left.items():
        for key, value in row.items():
            if key == "group_value":
                continue
            got = right[group].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not isinstance(got, (int, float)) or not math.isclose(value, got, rel_tol=0, abs_tol=1e-6):
                    raise ValueError(f"Independent Python/SQLite gold mismatch: {case_id}/{group}/{key}")
            elif got != value:
                raise ValueError(f"Independent Python/SQLite gold mismatch: {case_id}/{group}/{key}")


def _refund_python_gold(database: Path, case: dict) -> list[dict]:
    connection = sqlite3.connect(database)
    try:
        products = dict(connection.execute("SELECT product_id,category FROM products"))
        orders = connection.execute(
            "SELECT order_id,region,channel,ordered_at,paid_amount_cents,product_id FROM orders "
            "WHERE status='paid' AND ordered_at>=? AND ordered_at<?",
            (case["start"] + "T00:00:00Z", case["end"] + "T00:00:00Z"),
        ).fetchall()
        approved: dict[str, int] = {}
        for order_id, cents in connection.execute(
                "SELECT order_id,amount_cents FROM refunds WHERE status='approved'"):
            approved[order_id] = approved.get(order_id, 0) + cents
    finally:
        connection.close()
    dimension = case["dimension"]
    grouped: dict[str, list[tuple]] = {}
    for order in orders:
        group = {None: "全部", "category": products[order[5]], "channel": order[2],
                 "region": order[1], "month_utc": order[3][:7]}[dimension]
        grouped.setdefault(group, []).append(order)
    rows = []
    for group, members in sorted(grouped.items()):
        refunded_orders = sum(order[0] in approved for order in members)
        refund_cents = sum(approved.get(order[0], 0) for order in members)
        if case["metric"] == "approved_refund_order_rate":
            denominator = len(members)
            rows.append({"group_value": group, "numerator": refunded_orders,
                         "denominator": denominator,
                         "value": refunded_orders / denominator if denominator else None})
        else:
            denominator = sum(order[4] for order in members)
            rows.append({"group_value": group, "numerator_cents": refund_cents,
                         "denominator_cents": denominator,
                         "value": refund_cents / denominator if denominator else None})
    return rows


def build(database: Path, output: Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[1]
    output = output or root / "evals/domain-validation-v1"
    _assert_no_prompt_overlap(root)
    connection = sqlite3.connect(database)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()
    if metadata.get("data_version") != "insightpilot-multidomain-v1":
        raise ValueError("Domain validation requires the multidomain-v1 synthetic data snapshot")
    manifests = {}
    for domain, cases in CASES.items():
        blind, gold = [], []
        for case in cases:
            blind.append({"case_id": case["case_id"], "question": case["question"]})
            common = {"case_id": case["case_id"], "domain": domain,
                      "data_version": metadata["data_version"],
                      "data_fingerprint": metadata["data_fingerprint"]}
            if case.get("expected_status"):
                gold.append({**common, "expected_status": case["expected_status"]})
                continue
            plan = QueryPlan(case["metric"], case["start"], case["end"], case["region"], case["dimension"])
            if domain == "refunds":
                expected = _refund_python_gold(database, case)
            else:
                expected = marketing_oracle(database, case["start"], case["end"], case["dimension"])
            sqlite_rows, _ = execute_readonly(database, compile_query(plan))
            _check_rows(case["case_id"], expected, sqlite_rows)
            if case["dimension"] is None and domain == "refunds":
                for row in expected:
                    row.pop("group_value", None)
            gold.append({**common, "expected_status": "answered", "metric": case["metric"],
                         "dimension": case["dimension"], "region": case["region"],
                         "analysis_start": case["start"], "analysis_end_exclusive": case["end"],
                         "expected_rows": expected})
        blind_path = output / f"{domain}-blind-v1.jsonl"
        gold_path = output / f"{domain}-gold-v1.jsonl"
        _write_once(blind_path, blind)
        _write_once(gold_path, gold)
        manifests[domain] = {
            "suite_id": f"insightpilot-{domain}-validation-v1", "domain": domain,
            "case_count": len(cases), "answerable_count": sum("metric" in case for case in cases),
            "guard_count": sum("expected_status" in case for case in cases),
            "data_version": metadata["data_version"], "data_fingerprint": metadata["data_fingerprint"],
            "blind_sha256": sha256_text(blind_path), "gold_sha256": sha256_text(gold_path),
            "gold_method": "independent Python row/event ledger, cross-checked against compiled read-only SQLite query",
            "model_calls": 0,
        }
        manifest_path = output / f"{domain}-manifest-v1.json"
        manifest_text = json.dumps(manifests[domain], ensure_ascii=False, indent=2) + "\n"
        if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
            raise ValueError(f"Refusing to overwrite changed validation manifest: {manifest_path}")
        if not manifest_path.exists():
            manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")
    return {"data_version": metadata["data_version"], "data_fingerprint": metadata["data_fingerprint"],
            "domains": manifests}


if __name__ == "__main__":
    print(json.dumps(build(Path("data/insightpilot-multidomain.sqlite")), ensure_ascii=False, indent=2))
