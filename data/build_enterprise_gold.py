"""Create question-blind holdout answers by aggregating seed rows in Python."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def build_gold(database: Path, output: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        orders = connection.execute("SELECT order_id,region,channel,ordered_at,status,paid_amount_cents FROM orders").fetchall()
        refunds = connection.execute("SELECT order_id,amount_cents,status FROM refunds").fetchall()
        attributions = connection.execute("SELECT order_id,campaign_id,weight FROM campaign_attributions").fetchall()
        campaigns = dict(connection.execute("SELECT campaign_id,campaign_name FROM campaigns"))
        meta = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()

    approved_by_order = defaultdict(int)
    approved_orders = set()
    for order_id, cents, status in refunds:
        if status == "approved":
            approved_by_order[order_id] += cents
            approved_orders.add(order_id)

    month_paid, month_refunded = defaultdict(int), defaultdict(int)
    region_paid, region_refunded = defaultdict(int), defaultdict(int)
    for order_id, region, _channel, ordered_at, status, cents in orders:
        if status != "paid":
            continue
        month, amount = ordered_at[:7], approved_by_order[order_id]
        month_paid[month] += cents
        month_refunded[month] += amount
        region_paid[region] += 1
        if order_id in approved_orders:
            region_refunded[region] += 1

    links = defaultdict(list)
    for order_id, campaign_id, weight in attributions:
        links[order_id].append((campaign_id, weight))
    attributed = defaultdict(float)
    denominator = sum(approved_by_order[o[0]] for o in orders if o[4] == "paid")
    for order_id, _region, _channel, _ordered, status, _paid in orders:
        if status != "paid":
            continue
        amount = approved_by_order[order_id]
        touches = links.get(order_id)
        if not touches:
            attributed["未归因"] += amount
        else:
            for campaign_id, weight in touches:
                attributed[campaigns[campaign_id]] += amount * weight

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
         "case_id": "B01", "metric": "approved_refund_amount_rate", "dimension": "month_utc",
         "expected": {month: {"numerator_cents": round(month_refunded[month], 6),
                              "denominator_cents": month_paid[month],
                              "value": month_refunded[month] / month_paid[month] if month_paid[month] else None}
                      for month in sorted(month_paid)}},
        {"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
         "case_id": "B02", "metric": "approved_refund_order_rate", "dimension": "region",
         "expected": {region: {"numerator": region_refunded[region], "denominator": region_paid[region],
                               "value": region_refunded[region] / region_paid[region] if region_paid[region] else None}
                      for region in sorted(region_paid)}},
        {"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
         "case_id": "B03", "metric": "approved_refund_campaign_attribution", "dimension": "campaign",
         "expected": {campaign: {"numerator_cents": round(amount, 6), "denominator_cents": denominator,
                                 "value": amount / denominator if denominator else None}
                      for campaign, amount in sorted(attributed.items())}},
    ]
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build_gold(Path("data/insightpilot-enterprise.sqlite"), Path("evals/enterprise-gold-v1.jsonl"))
