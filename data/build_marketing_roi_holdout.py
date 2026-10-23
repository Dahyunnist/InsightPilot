"""Freeze a small offline development/test set for synthetic marketing net ROI."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from insightpilot.freeze import sha256_text
from insightpilot.planner import QueryPlan
from insightpilot.runner import execute_readonly
from insightpilot.sql import compile_query
from insightpilot.marketing_oracle import calculate_marketing_net_roi


DEV = [
    {"id": "MD-D01", "question": "2026-10-01 到 2026-10-31 各营销活动净投资回报率是多少？",
     "metric": "marketing_net_roi", "start": "2026-10-01", "end": "2026-11-01", "dimension": "campaign"},
    {"id": "MD-D02", "question": "2026-10-01 至 2026-10-31 营销 ROI 整体是多少？",
     "metric": "marketing_net_roi", "start": "2026-10-01", "end": "2026-11-01", "dimension": None},
    {"id": "MD-D03", "question": "2026-10-31 当日各活动 ROI 是多少？",
     "metric": "marketing_net_roi", "start": "2026-10-31", "end": "2026-11-01", "dimension": "campaign"},
]
TEST = [
    {"id": "MD-T01", "question": "2026-10-01 至 2026-11-01 按活动看营销投资回报率，扣除已撤销退款并去重费用。",
     "metric": "marketing_net_roi", "start": "2026-10-01", "end": "2026-11-02", "dimension": "campaign"},
    {"id": "MD-T02", "question": "2026-10-01 到 2026-11-01 营销净 ROI 是多少？",
     "metric": "marketing_net_roi", "start": "2026-10-01", "end": "2026-11-02", "dimension": None},
    {"id": "MD-T03", "question": "2026-10-01 到 2026-10-31 华东各营销活动投资回报率是多少？",
     "expected_status": "clarify", "reason": "region_marketing_spend_unsupported"},
    {"id": "MD-T04", "question": "在 2026-10-01 至 2026-10-31 期间，退款率的口径是什么？",
     "expected_status": "clarify", "reason": "ambiguous_refund_rate"},
]


def _python_gold(database: Path, start: str, end: str, dimension: str | None) -> list[dict]:
    """Independent event-ledger aggregation; does not import query templates."""
    connection = sqlite3.connect(database)
    try:
        snapshot = connection.execute("SELECT value FROM data_meta WHERE key='snapshot_at'").fetchone()[0]
        eligible = {row[0]: row[1] for row in connection.execute(
            "SELECT order_id,paid_amount_cents FROM orders "
            "WHERE status='paid' AND ordered_at>=? AND ordered_at<?",
            (start + "T00:00:00Z", end + "T00:00:00Z"),
        )}
        names = dict(connection.execute("SELECT campaign_id,campaign_name FROM campaigns"))
        refund_events: dict[str, int] = defaultdict(int)
        refund_ids_by_order: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for refund_id, order_id, cents in connection.execute(
            "SELECT refund_id,order_id,amount_cents FROM refunds "
            "WHERE status='approved' AND refunded_at<? AND recorded_at<=?", (snapshot, snapshot),
        ):
            if order_id in eligible:
                refund_ids_by_order[order_id].append((refund_id, cents))
        for refund_id, cents in connection.execute(
            "SELECT refund_id,amount_cents FROM refund_reversals WHERE recorded_at<=?", (snapshot,),
        ):
            refund_events[refund_id] += cents
        weights: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for order_id, campaign_id, weight in connection.execute(
            "SELECT order_id,campaign_id,weight FROM campaign_attributions"
        ):
            if order_id in eligible:
                weights[order_id].append((campaign_id, weight))
        revenue: dict[str, float] = defaultdict(float)
        for order_id, paid_cents in eligible.items():
            net_cents = paid_cents - sum(amount - refund_events[refund_id]
                                         for refund_id, amount in refund_ids_by_order[order_id])
            assigned = weights.get(order_id)
            if not assigned:
                revenue["未归因"] += net_cents
            else:
                for campaign_id, weight in assigned:
                    revenue[names[campaign_id]] += net_cents * weight

        latest: dict[str, tuple] = {}
        for row in connection.execute(
            "SELECT business_event_id,revision,recorded_at,event_id,campaign_id,spend_cents,event_status "
            "FROM campaign_spend_events WHERE spent_at>=? AND spent_at<? AND recorded_at<=?",
            (start + "T00:00:00Z", end + "T00:00:00Z", snapshot),
        ):
            old = latest.get(row[0])
            if old is None or row[1:4] > old[1:4]:
                latest[row[0]] = row
        spend: dict[str, int] = defaultdict(int)
        for row in latest.values():
            if row[6] == "active":
                spend[names[row[4]]] += row[5]
        if dimension is None:
            net_revenue, spend_cents = sum(revenue.values()), sum(spend.values())
            return [{"group_value": "全部", "net_revenue_cents": net_revenue,
                     "spend_cents": spend_cents,
                     "value": (net_revenue - spend_cents) / spend_cents if spend_cents else None}]
        return [{
            "group_value": name, "net_revenue_cents": revenue.get(name, 0),
            "spend_cents": spend.get(name, 0),
            "value": ((revenue.get(name, 0) - spend[name]) / spend[name] if spend.get(name) else None),
        } for name in sorted(set(revenue) | set(spend))]
    finally:
        connection.close()


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")


def build(database: Path, output: Path) -> dict:
    metadata_connection = sqlite3.connect(database)
    try:
        meta = dict(metadata_connection.execute("SELECT key,value FROM data_meta"))
    finally:
        metadata_connection.close()
    if meta.get("data_version") != "insightpilot-multidomain-v1":
        raise ValueError("expected multidomain-v1 synthetic database")
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    new_questions = {case["question"].strip() for case in DEV + TEST}
    historical_questions = set()
    historical_root = Path(__file__).resolve().parents[1] / "evals"
    for prior in historical_root.glob("*.jsonl"):
        if prior.name.startswith("marketing-roi-"):
            continue
        for line in prior.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("question"), str):
                historical_questions.add(row["question"].strip())
    overlap = sorted(new_questions & historical_questions)
    if overlap:
        raise ValueError("marketing ROI prompts overlap exact text with a prior eval")
    for split, cases in (("dev", DEV), ("test", TEST)):
        blind_rows, gold_rows = [], []
        for case in cases:
            blind_rows.append({"id": case["id"], "question": case["question"],
                               "expected_status": case.get("expected_status", "answered")})
            if "expected_status" in case:
                gold_rows.append({"id": case["id"], "status": case["expected_status"], "reason": case["reason"]})
                continue
            plan = QueryPlan(case["metric"], case["start"], case["end"], None, case["dimension"])
            python_rows = _python_gold(database, case["start"], case["end"], case["dimension"])
            sql_rows, _ = execute_readonly(database, compile_query(plan))
            oracle_rows = calculate_marketing_net_roi(plan, database)
            if len(python_rows) != len(oracle_rows) or any(
                abs((a.get(key) or 0) - (b.get(key) or 0)) > 1e-7
                for a, b in zip(python_rows, oracle_rows)
                for key in ("net_revenue_cents", "spend_cents")
            ):
                raise ValueError(f"independent Python oracles disagree for {case['id']}")
            if len(sql_rows) != len(python_rows) or any(
                a["group_value"] != b["group_value"]
                or abs((a.get("net_revenue_cents") or 0) - (b.get("net_revenue_cents") or 0)) > 1e-7
                or a.get("spend_cents") != b.get("spend_cents")
                or (a.get("value") is None) != (b.get("value") is None)
                or (a.get("value") is not None and abs(a["value"] - b["value"]) > 1e-9)
                for a, b in zip(sql_rows, python_rows)
            ):
                raise ValueError(f"SQL and independent Python gold disagree for {case['id']}")
            gold_rows.append({"id": case["id"], "status": "answered", "metric_id": case["metric"],
                              "start_date": case["start"], "end_date_exclusive": case["end"],
                              "dimension": case["dimension"], "rows": python_rows})
        blind_path, gold_path = output / f"marketing-roi-{split}-v1.jsonl", output / f"marketing-roi-{split}-gold-v1.jsonl"
        _write(blind_path, blind_rows)
        _write(gold_path, gold_rows)
        hashes[split] = {"case_count": len(cases), "blind_sha256": sha256_text(blind_path),
                         "gold_sha256": sha256_text(gold_path)}
    manifest = {
        "suite_id": "marketing-roi-v1", "data_version": meta["data_version"],
        "data_fingerprint": meta["data_fingerprint"], "seed": meta["seed"],
        "snapshot_at": meta["snapshot_at"], "gold_method": "independent Python ledger aggregation, cross-checked against a second Python oracle and compiled SQL execution",
        "scoring": "strict plan and per-group net revenue, spend, nullable ROI equality; guard status/reason equality",
        "synthetic_only": True, "splits": hashes,
        "exact_prompt_overlap_count": len(overlap), "exact_prompt_overlap": overlap,
    }
    manifest_path = output / "marketing-roi-manifest-v1.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import argparse
    from .multidomain_seed import create_multidomain_database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-multidomain.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("evals"))
    args = parser.parse_args()
    if not args.database.exists():
        create_multidomain_database(args.database)
    print(json.dumps(build(args.database, args.output), ensure_ascii=False, indent=2))
