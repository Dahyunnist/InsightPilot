"""Independent source-row oracle for synthetic marketing net ROI."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .planner import QueryPlan


def calculate_marketing_net_roi(plan: QueryPlan, database_path: Path) -> list[dict]:
    uri = "file:" + quote(Path(database_path).resolve().as_posix(), safe="/:\\") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = connection.execute("SELECT value FROM data_meta WHERE key='snapshot_at'").fetchone()[0]
        start_at, end_at = plan.start_date + "T00:00:00Z", plan.end_date + "T00:00:00Z"
        params: list[str] = [start_at, end_at]
        region_sql = ""
        if plan.region:
            region_sql = " AND region=?"
            params.append(plan.region)
        orders = connection.execute(
            "SELECT order_id,paid_amount_cents FROM orders WHERE status='paid' "
            "AND ordered_at>=? AND ordered_at<?" + region_sql, params,
        ).fetchall()
        order_amounts = {row["order_id"]: row["paid_amount_cents"] for row in orders}
        order_ids = set(order_amounts)
        refunds: dict[str, list[tuple[str, int]]] = {}
        for row in connection.execute(
            "SELECT refund_id,order_id,amount_cents FROM refunds "
            "WHERE status='approved' AND refunded_at<? AND recorded_at<=?", (snapshot, snapshot),
        ):
            if row["order_id"] in order_ids:
                refunds.setdefault(row["order_id"], []).append((row["refund_id"], row["amount_cents"]))
        reversed_amounts: dict[str, int] = {}
        for row in connection.execute(
            "SELECT refund_id,amount_cents FROM refund_reversals WHERE recorded_at<=?", (snapshot,),
        ):
            reversed_amounts[row["refund_id"]] = reversed_amounts.get(row["refund_id"], 0) + row["amount_cents"]

        names = dict(connection.execute("SELECT campaign_id,campaign_name FROM campaigns"))
        touches: dict[str, list[tuple[str, float]]] = {}
        for row in connection.execute("SELECT order_id,campaign_id,weight FROM campaign_attributions"):
            if row["order_id"] in order_ids:
                touches.setdefault(row["order_id"], []).append((row["campaign_id"], row["weight"]))
        revenue_parts: dict[str, list[float]] = {}
        for order_id, paid_cents in order_amounts.items():
            refund_cents = sum(amount - reversed_amounts.get(refund_id, 0)
                               for refund_id, amount in refunds.get(order_id, []))
            order_touches = touches.get(order_id)
            if not order_touches:
                revenue_parts.setdefault("未归因", []).append(float(paid_cents - refund_cents))
            else:
                for campaign_id, weight in order_touches:
                    revenue_parts.setdefault(names[campaign_id], []).append((paid_cents - refund_cents) * weight)

        latest: dict[str, sqlite3.Row] = {}
        for row in connection.execute(
            "SELECT event_id,business_event_id,revision,campaign_id,recorded_at,spend_cents,event_status "
            "FROM campaign_spend_events WHERE spent_at>=? AND spent_at<? AND recorded_at<=?",
            (start_at, end_at, snapshot),
        ):
            old = latest.get(row["business_event_id"])
            rank = (row["revision"], row["recorded_at"], row["event_id"])
            if old is None or rank > (old["revision"], old["recorded_at"], old["event_id"]):
                latest[row["business_event_id"]] = row
        spend: dict[str, int] = {}
        for row in latest.values():
            if row["event_status"] == "active":
                name = names[row["campaign_id"]]
                spend[name] = spend.get(name, 0) + row["spend_cents"]

        revenue = {name: math.fsum(parts) for name, parts in revenue_parts.items()}
        groups = sorted(set(revenue) | set(spend))
        if plan.dimension is None:
            revenue_total = math.fsum(revenue.values())
            spend_total = sum(spend.values())
            return [{"group_value": "全部", "net_revenue_cents": revenue_total,
                     "spend_cents": spend_total,
                     "value": (revenue_total - spend_total) / spend_total if spend_total else None}]
        return [{
            "group_value": name,
            "net_revenue_cents": revenue.get(name, 0.0),
            "spend_cents": spend.get(name, 0),
            "value": (revenue.get(name, 0.0) - spend[name]) / spend[name] if spend.get(name, 0) else None,
        } for name in groups]
    finally:
        connection.close()
