"""Create the fixed, synthetic InsightPilot S0 dataset."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DATA_VERSION = "insightpilot-s0-v1"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'cancelled')),
    paid_amount_cents INTEGER NOT NULL CHECK (paid_amount_cents >= 0)
);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    refunded_at TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ('approved', 'pending', 'rejected'))
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    category TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Stable fixture. Timestamps use UTC ISO-8601; all monetary values are integer cents.
PRODUCTS = [("p1", "家电"), ("p2", "服饰"), ("p3", "食品")]
ORDERS = [
    ("o01", "c1", "p1", "华东", "web", "2026-10-05T09:00:00Z", "paid", 10000),
    ("o02", "c2", "p1", "华东", "app", "2026-10-06T10:00:00Z", "paid", 20000),
    ("o03", "c3", "p2", "华东", "web", "2026-10-07T11:00:00Z", "paid", 10000),
    ("o04", "c4", "p2", "华东", "app", "2026-10-08T12:00:00Z", "paid", 30000),
    ("o05", "c5", "p3", "华东", "web", "2026-10-09T13:00:00Z", "paid", 10000),
    ("o06", "c6", "p3", "华东", "app", "2026-10-10T14:00:00Z", "paid", 10000),
    ("o07", "c7", "p1", "华东", "web", "2026-10-11T15:00:00Z", "cancelled", 5000),
    ("o08", "c8", "p2", "华东", "app", "2026-10-12T16:00:00Z", "paid", 0),
    ("o09", "c9", "p1", "华东", "web", "2026-10-12T23:30:00Z", "paid", 10000),
    ("o10", "c10", "p3", "华东", "app", "2026-10-13T00:30:00Z", "paid", 10000),
    ("o11", "c11", "p2", "华东", "web", "2026-10-13T08:00:00Z", "paid", 20000),
    ("o12", "c12", "p1", "华东", "app", "2026-10-14T08:00:00Z", "paid", 10000),
    ("o13", "c13", "p2", "华东", "web", "2026-10-15T08:00:00Z", "paid", 10000),
    ("o14", "c14", "p3", "华东", "app", "2026-10-16T08:00:00Z", "paid", 10000),
    ("o15", "c15", "p1", "华东", "web", "2026-10-17T08:00:00Z", "paid", 20000),
    ("o16", "c16", "p2", "华东", "app", "2026-10-18T08:00:00Z", "paid", 10000),
    ("o17", "c17", "p3", "华东", "web", "2026-10-19T08:00:00Z", "paid", 10000),
    ("o18", "c18", "p1", "华东", "app", "2026-10-20T08:00:00Z", "paid", 10000),
    ("o19", "c19", "p2", "华东", "web", "2026-10-21T08:00:00Z", "paid", 10000),
    ("o20", "c20", "p3", "华东", "app", "2026-10-22T08:00:00Z", "paid", 10000),
    ("o21", "c21", "p1", "华东", "web", "2026-10-23T08:00:00Z", "paid", 10000),
    ("o22", "c22", "p2", "华东", "app", "2026-10-24T08:00:00Z", "paid", 10000),
    ("o23", "c23", "p3", "华东", "web", "2026-10-25T08:00:00Z", "paid", 10000),
    ("o24", "c24", "p1", "华东", "app", "2026-10-26T08:00:00Z", "paid", 10000),
    ("o25", "c25", "p2", "华东", "web", "2026-10-27T08:00:00Z", "paid", 10000),
    ("o26", "c26", "p3", "华东", "app", "2026-10-28T08:00:00Z", "paid", 10000),
    ("o27", "c27", "p1", "华东", "web", "2026-10-29T08:00:00Z", "paid", 10000),
    ("o28", "c28", "p2", "华东", "app", "2026-10-30T08:00:00Z", "paid", 10000),
    ("o29", "c29", "p3", "华东", "web", "2026-10-31T08:00:00Z", "paid", 10000),
    ("o30", "c30", "p1", "华东", "app", "2026-11-01T08:00:00Z", "paid", 10000),
]

# Multiple refund rows on o02/o04 intentionally expose join fan-out and split refunds.
REFUNDS = [
    ("r01", "o01", "2026-10-06T10:00:00Z", 10000, "approved"),
    ("r02", "o02", "2026-10-07T10:00:00Z", 5000, "approved"),
    ("r03", "o02", "2026-10-08T10:00:00Z", 5000, "approved"),
    ("r04", "o03", "2026-10-09T10:00:00Z", 10000, "pending"),
    ("r05", "o04", "2026-10-10T10:00:00Z", 10000, "approved"),
    ("r06", "o04", "2026-10-11T10:00:00Z", 5000, "rejected"),
    ("r07", "o05", "2026-10-12T10:00:00Z", 10000, "approved"),
    ("r08", "o06", "2026-10-13T10:00:00Z", 10000, "approved"),
    ("r09", "o07", "2026-10-13T10:00:00Z", 5000, "approved"),
    ("r10", "o08", "2026-10-14T10:00:00Z", 0, "approved"),
    ("r11", "o09", "2026-10-13T00:10:00Z", 10000, "approved"),
    ("r12", "o10", "2026-10-13T00:20:00Z", 10000, "approved"),
    ("r13", "o11", "2026-10-14T10:00:00Z", 10000, "approved"),
    ("r14", "o12", "2026-10-15T10:00:00Z", 10000, "approved"),
    ("r15", "o13", "2026-10-16T10:00:00Z", 10000, "approved"),
    ("r16", "o14", "2026-10-17T10:00:00Z", 10000, "approved"),
    ("r17", "o15", "2026-10-18T10:00:00Z", 10000, "approved"),
    ("r18", "o16", "2026-10-19T10:00:00Z", 10000, "approved"),
    ("r19", "o17", "2026-10-20T10:00:00Z", 10000, "approved"),
    ("r20", "o18", "2026-10-21T10:00:00Z", 10000, "approved"),
    ("r21", "o19", "2026-10-22T10:00:00Z", 10000, "approved"),
    ("r22", "o20", "2026-10-23T10:00:00Z", 10000, "approved"),
    ("r23", "o21", "2026-10-24T10:00:00Z", 10000, "approved"),
    ("r24", "o22", "2026-10-25T10:00:00Z", 10000, "approved"),
    ("r25", "o23", "2026-10-26T10:00:00Z", 10000, "approved"),
    ("r26", "o24", "2026-10-27T10:00:00Z", 10000, "approved"),
    ("r27", "o25", "2026-10-28T10:00:00Z", 10000, "approved"),
    ("r28", "o26", "2026-10-29T10:00:00Z", 10000, "approved"),
    ("r29", "o27", "2026-10-30T10:00:00Z", 10000, "approved"),
    ("r30", "o28", "2026-10-31T10:00:00Z", 10000, "approved"),
    ("r31", "o29", "2026-11-01T10:00:00Z", 10000, "approved"),
    ("r32", "o30", "2026-11-02T10:00:00Z", 10000, "approved"),
]


def create_database(path: str | Path) -> Path:
    """Create or replace database at path with the versioned fixed fixture."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO products VALUES (?, ?)", PRODUCTS)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ORDERS)
        conn.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?)", REFUNDS)
        conn.executemany("INSERT INTO data_meta VALUES (?, ?)", [("data_version", DATA_VERSION), ("timezone", "UTC")])
        conn.commit()
    finally:
        conn.close()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/insightpilot-s0.sqlite")
    args = parser.parse_args()
    print(f"Created {create_database(args.output)} ({DATA_VERSION})")


if __name__ == "__main__":
    main()
