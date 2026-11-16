"""Create the empty, contract-shaped SQLite fixture for the IP-3 PG matrix."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


DATA_VERSION = "insightpilot-enterprise-v1"
FIXTURE_ROWS = {
    "orders": [("ip3-order-1", "ip3-product-1", "华东", "direct", "2026-10-06T10:00:00Z",
                "paid", 1000)],
    "refunds": [("ip3-refund-1", "ip3-order-1", "2026-10-07T10:00:00Z",
                 "2026-10-08T10:00:00Z", 100, "approved")],
    "data_meta": [("data_version", DATA_VERSION), ("snapshot_at", "2026-10-12T00:00:00Z")],
}
DATA_FINGERPRINT = hashlib.sha256(json.dumps(
    {"fixture_version": "insightpilot-ip3-reliability-v3", "data_version": DATA_VERSION,
     "fixture_rows": FIXTURE_ROWS},
    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def create_fixture(path: Path) -> Path:
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing reliability fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE orders (
                order_id TEXT NOT NULL, product_id TEXT NOT NULL, region TEXT NOT NULL,
                channel TEXT NOT NULL, ordered_at TEXT NOT NULL, status TEXT NOT NULL,
                paid_amount_cents INTEGER NOT NULL
            );
            CREATE TABLE refunds (
                refund_id TEXT NOT NULL, order_id TEXT NOT NULL, refunded_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE refund_reversals (
                reversal_id TEXT NOT NULL, refund_id TEXT NOT NULL, reversed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL, amount_cents INTEGER NOT NULL
            );
            CREATE TABLE products (product_id TEXT NOT NULL, category TEXT NOT NULL);
            CREATE TABLE campaign_attributions (
                order_id TEXT NOT NULL, campaign_id TEXT NOT NULL, weight REAL NOT NULL
            );
            CREATE TABLE campaign_spend_events (
                event_id TEXT NOT NULL, business_event_id TEXT NOT NULL, revision INTEGER NOT NULL,
                campaign_id TEXT NOT NULL, spent_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
                spend_cents INTEGER NOT NULL, event_status TEXT NOT NULL
            );
            CREATE TABLE data_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        connection.executemany("INSERT INTO orders VALUES(?,?,?,?,?,?,?)", FIXTURE_ROWS["orders"])
        connection.executemany("INSERT INTO refunds VALUES(?,?,?,?,?,?)", FIXTURE_ROWS["refunds"])
        connection.executemany("INSERT INTO data_meta(key,value) VALUES(?,?)", (
            *FIXTURE_ROWS["data_meta"], ("data_fingerprint", DATA_FINGERPRINT)))
        connection.commit()
    finally:
        connection.close()
    return path
