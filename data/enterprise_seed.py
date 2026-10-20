"""Build a deterministic enterprise-style synthetic retail benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DATA_VERSION = "insightpilot-enterprise-v1"
SEED = 20261020
ORDER_COUNT = 50_000
SNAPSHOT_AT = "2027-03-01T00:00:00Z"
CAMPAIGNS = [(f"cmp{i:02}", f"Campaign {i:02}") for i in range(1, 21)]
REGIONS = ("华东", "华南", "华北", "西南")
CHANNELS = ("web", "app", "store", "partner")
CATEGORIES = ("家电", "服饰", "食品", "美妆", "数码", "家居")

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE products(product_id TEXT PRIMARY KEY, category TEXT NOT NULL);
CREATE TABLE customers(customer_id TEXT PRIMARY KEY, segment TEXT NOT NULL, email TEXT, phone TEXT);
CREATE TABLE campaigns(campaign_id TEXT PRIMARY KEY, campaign_name TEXT NOT NULL);
CREATE TABLE orders(
 order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, product_id TEXT NOT NULL,
 region TEXT NOT NULL, channel TEXT NOT NULL, ordered_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('paid','cancelled')),
 paid_amount_cents INTEGER NOT NULL CHECK(paid_amount_cents >= 0)
);
CREATE TABLE refunds(
 refund_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, refunded_at TEXT NOT NULL,
 recorded_at TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
 status TEXT NOT NULL CHECK(status IN ('approved','pending','rejected'))
);
CREATE TABLE campaign_attributions(
 order_id TEXT NOT NULL, campaign_id TEXT NOT NULL, weight REAL NOT NULL CHECK(weight > 0 AND weight <= 1),
 PRIMARY KEY(order_id,campaign_id)
);
CREATE TABLE data_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE INDEX idx_orders_time_region ON orders(ordered_at,region);
CREATE INDEX idx_refunds_event ON refunds(refunded_at,status,order_id);
CREATE INDEX idx_refunds_order ON refunds(order_id,status);
CREATE INDEX idx_attribution_order ON campaign_attributions(order_id);
"""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_enterprise_database(path: str | Path, order_count: int = ORDER_COUNT) -> Path:
    if order_count < 1000:
        raise ValueError("enterprise benchmark needs at least 1,000 orders")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(SEED)
    products = [(f"p{i:04}", CATEGORIES[i % len(CATEGORIES)]) for i in range(1, 601)]
    customers = [(f"c{i:06}", ("new", "returning", "vip")[i % 3], f"synthetic-{i}@example.invalid", f"139{i:08d}")
                 for i in range(1, 20_001)]
    orders = []
    attributions = []
    refunds = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(1, order_count + 1):
        order_id = f"o{i:07}"
        customer = f"c{rng.randint(1, min(20_000, order_count)):06}"
        product = f"p{rng.randint(1, 600):04}"
        region, channel = rng.choice(REGIONS), rng.choice(CHANNELS)
        ordered = base + timedelta(seconds=rng.randrange(365 * 24 * 3600))
        status = "cancelled" if rng.random() < 0.035 else "paid"
        paid = rng.choice((0, 990, 1990, 4990, 9990, 19990, 49990, 99990))
        orders.append((order_id, customer, product, region, channel, _iso(ordered), status, paid))

        # One percent intentionally lacks attribution; query layer reports it as an explicit bucket.
        if rng.random() >= 0.01:
            touch_count = 1 if rng.random() < 0.78 else 2
            chosen = rng.sample(CAMPAIGNS, touch_count)
            if touch_count == 1:
                attributions.append((order_id, chosen[0][0], 1.0))
            else:
                first_weight = rng.choice((0.25, 0.4, 0.5, 0.6, 0.75))
                attributions.extend(((order_id, chosen[0][0], first_weight),
                                     (order_id, chosen[1][0], 1.0 - first_weight)))

        if status == "paid" and paid and rng.random() < 0.22:
            refund_total = 0
            for part in range(1, 1 + (2 if rng.random() < 0.12 else 1)):
                remaining = paid - refund_total
                if remaining <= 0:
                    break
                amount = rng.randint(1, remaining)
                refund_total += amount
                event = ordered + timedelta(days=rng.randrange(0, 45), seconds=rng.randrange(86400))
                recorded = event + timedelta(days=rng.randrange(0, 15))
                state = rng.choices(("approved", "pending", "rejected"), (0.79, 0.13, 0.08))[0]
                refunds.append((f"r{len(refunds)+1:08}", order_id, _iso(event), _iso(recorded), amount, state))

    fingerprint_payload = {"data_version": DATA_VERSION, "seed": SEED, "snapshot_at": SNAPSHOT_AT,
                           "products": products, "customers": customers, "campaigns": CAMPAIGNS,
                           "orders": orders, "refunds": refunds, "campaign_attributions": attributions}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO products VALUES (?,?)", products)
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
        conn.executemany("INSERT INTO campaigns VALUES (?,?)", CAMPAIGNS)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", orders)
        conn.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", refunds)
        conn.executemany("INSERT INTO campaign_attributions VALUES (?,?,?)", attributions)
        meta = [("data_version", DATA_VERSION), ("data_fingerprint", fingerprint), ("timezone", "UTC"), ("seed", str(SEED)),
                ("order_count", str(order_count)), ("refund_count", str(len(refunds))),
                ("snapshot_at", SNAPSHOT_AT)]
        conn.executemany("INSERT INTO data_meta VALUES (?,?)", meta)
        conn.commit()
    finally:
        conn.close()
    return path


def quality_report(path: str | Path) -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("orders", "refunds", "campaign_attributions", "customers", "products", "campaigns")}
        orphan_refunds = conn.execute("SELECT COUNT(*) FROM refunds r LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL").fetchone()[0]
        late_refunds = conn.execute("SELECT COUNT(*) FROM refunds WHERE recorded_at > datetime(refunded_at, '+1 day')").fetchone()[0]
        attribution = conn.execute("""SELECT COUNT(*) FROM (
          SELECT o.order_id, COALESCE(SUM(a.weight),0) AS weight_sum
          FROM orders o LEFT JOIN campaign_attributions a USING(order_id)
          GROUP BY o.order_id HAVING weight_sum>0 AND ABS(weight_sum-1.0)>0.000001
        )""").fetchone()[0]
        weights_outside = conn.execute("SELECT COUNT(*) FROM campaign_attributions WHERE weight<=0 OR weight>1").fetchone()[0]
        over_refund = conn.execute("""SELECT COUNT(*) FROM (
          SELECT o.order_id FROM orders o JOIN refunds r USING(order_id)
          WHERE o.status='paid' AND r.status='approved' GROUP BY o.order_id
          HAVING SUM(r.amount_cents)>MAX(o.paid_amount_cents)
        )""").fetchone()[0]
        missing_attribution = conn.execute("""SELECT COUNT(*) FROM orders o WHERE NOT EXISTS
          (SELECT 1 FROM campaign_attributions a WHERE a.order_id=o.order_id)""").fetchone()[0]
        meta = dict(conn.execute("SELECT key,value FROM data_meta"))
        return {"data_version": DATA_VERSION, "data_fingerprint": meta.get("data_fingerprint"),
                "snapshot_at": meta.get("snapshot_at"), "row_counts": counts,
                "quality": {"orphan_refunds": orphan_refunds, "late_recorded_refunds_over_1d": late_refunds,
                            "attribution_weight_sum_mismatches": attribution,
                            "weights_outside_0_1": weights_outside, "approved_over_refund_orders": over_refund,
                            "orders_without_campaign_attribution": missing_attribution,
                            "campaign_attribution_coverage": round(1 - missing_attribution / counts["orders"], 6)}}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/insightpilot-enterprise.sqlite")
    parser.add_argument("--orders", type=int, default=ORDER_COUNT)
    args = parser.parse_args()
    database = create_enterprise_database(args.output, args.orders)
    print(json.dumps({"database": str(database), **quality_report(database)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
