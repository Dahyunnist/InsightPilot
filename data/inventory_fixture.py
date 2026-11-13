"""Create a small deterministic synthetic fixture for inventory turnover tests."""

from __future__ import annotations

import sqlite3
import hashlib
import json
from pathlib import Path

from . import seed as base_seed


DATA_VERSION = "insightpilot-inventory-v1"
SCHEMA = """
CREATE TABLE inventory_daily_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    inventory_value_cents INTEGER CHECK (inventory_value_cents IS NULL OR inventory_value_cents >= 0),
    UNIQUE(category, local_date)
);
CREATE TABLE inventory_cogs_events (
    event_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    sold_at TEXT NOT NULL,
    cogs_cents INTEGER NOT NULL CHECK (cogs_cents >= 0)
);
CREATE INDEX idx_inventory_snapshot_time ON inventory_daily_snapshots(snapshot_at);
CREATE INDEX idx_inventory_cogs_time ON inventory_cogs_events(sold_at);
"""

SNAPSHOTS = [
    (f"a-{day}", "家电", f"2026-11-{day:02d}T15:59:59Z", f"2026-11-{day:02d}", value)
    for day, value in ((1, 10_000), (2, 20_000), (3, 30_000))
] + [
    (f"b-{day}", "服饰", f"2026-11-{day:02d}T15:59:59Z", f"2026-11-{day:02d}", 0)
    for day in (1, 2, 3)
] + [
    (f"c-{day}", "食品", f"2026-11-{day:02d}T15:59:59Z", f"2026-11-{day:02d}", None)
    for day in (1, 2, 3)
]

COGS_EVENTS = [
    ("a-start", "家电", "2026-10-31T16:00:00Z", 2_000),
    ("a-mid", "家电", "2026-11-02T02:00:00Z", 3_000),
    ("a-last", "家电", "2026-11-03T15:59:59Z", 7_000),
    ("a-end-excluded", "家电", "2026-11-03T16:00:00Z", 99_999),
    ("b-period", "服饰", "2026-11-02T02:00:00Z", 500),
    ("c-period", "食品", "2026-11-02T02:00:00Z", 250),
]


def create_inventory_fixture(path: str | Path) -> Path:
    path = base_seed.create_database(path)
    connection = sqlite3.connect(path)
    try:
        metadata = dict(connection.execute(
            "SELECT key,value FROM data_meta WHERE key IN ('data_version','data_fingerprint')"
        ))
        base_version = metadata["data_version"]
        base_fingerprint = metadata.get("data_fingerprint")
        if base_fingerprint is None:
            base_rows = {
                "products": connection.execute("SELECT * FROM products ORDER BY product_id").fetchall(),
                "orders": connection.execute("SELECT * FROM orders ORDER BY order_id").fetchall(),
                "refunds": connection.execute("SELECT * FROM refunds ORDER BY refund_id").fetchall(),
            }
            base_fingerprint = hashlib.sha256(json.dumps(
                {"schema": base_seed.SCHEMA, "data_version": base_version, "rows": base_rows},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
        connection.executescript(SCHEMA)
        connection.executemany("INSERT INTO inventory_daily_snapshots VALUES (?, ?, ?, ?, ?)", SNAPSHOTS)
        connection.executemany("INSERT INTO inventory_cogs_events VALUES (?, ?, ?, ?)", COGS_EVENTS)
        additive_sha = hashlib.sha256(json.dumps(
            {"schema": SCHEMA, "snapshots": SNAPSHOTS, "cogs_events": COGS_EVENTS},
            ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        fingerprint = hashlib.sha256(
            f"{base_version}:{base_fingerprint}:{DATA_VERSION}:{additive_sha}".encode("utf-8")
        ).hexdigest()
        connection.execute("UPDATE data_meta SET value=? WHERE key='data_version'", (DATA_VERSION,))
        connection.execute("INSERT OR REPLACE INTO data_meta(key,value) VALUES ('data_fingerprint',?)", (fingerprint,))
        connection.execute("INSERT OR REPLACE INTO data_meta(key,value) VALUES ('inventory_snapshot_count',?)",
                           (str(len(SNAPSHOTS)),))
        connection.execute("INSERT OR REPLACE INTO data_meta(key,value) VALUES ('inventory_cogs_event_count',?)",
                           (str(len(COGS_EVENTS)),))
        connection.commit()
    finally:
        connection.close()
    return path
