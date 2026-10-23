"""Extend the synthetic retail fixture with a versioned marketing ROI domain."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .enterprise_seed import DATA_VERSION as RETAIL_VERSION
from .enterprise_seed import SEED as RETAIL_SEED
from .enterprise_seed import SNAPSHOT_AT, create_enterprise_database


DATA_VERSION = "insightpilot-multidomain-v1"
SEED = 20261023
SCHEMA = """
CREATE TABLE campaign_spend_events(
 event_id TEXT PRIMARY KEY, business_event_id TEXT NOT NULL, revision INTEGER NOT NULL,
 campaign_id TEXT NOT NULL, spent_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
 spend_cents INTEGER NOT NULL CHECK(spend_cents >= 0), event_status TEXT NOT NULL
 CHECK(event_status IN ('active','void'))
);
CREATE TABLE refund_reversals(
 reversal_id TEXT PRIMARY KEY, refund_id TEXT NOT NULL, reversed_at TEXT NOT NULL,
 recorded_at TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0)
);
CREATE INDEX idx_spend_time_campaign ON campaign_spend_events(spent_at,campaign_id);
CREATE INDEX idx_spend_business_revision ON campaign_spend_events(business_event_id,revision,recorded_at);
CREATE INDEX idx_reversal_refund ON refund_reversals(refund_id,recorded_at);
"""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_multidomain_database(path: str | Path, *, seed: int = SEED,
                                order_count: int = 50_000) -> Path:
    path = Path(path)
    base = create_enterprise_database(path, order_count=order_count, seed=RETAIL_SEED)
    rng = random.Random(seed)
    conn = sqlite3.connect(base)
    try:
        conn.executescript(SCHEMA)
        spend_rows = []
        event_number = 0
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for campaign_no in range(1, 21):
            campaign_id = f"cmp{campaign_no:02}"
            for month in range(12):
                # Include explicit UTC day-edge events in the reproducible fixture.
                day = rng.choice((1, 15, 28))
                hour = rng.choice((0, 12, 23))
                if campaign_no == 20 and month == 9:
                    day, hour = 31, 23
                elif campaign_no == 19 and month == 10:
                    day, hour = 1, 0
                spent_at = start.replace(month=month + 1, day=day, hour=hour,
                                         minute=59 if hour == 23 else 0, second=59 if hour == 23 else 0)
                business_id = f"spend-{campaign_no:02}-{month + 1:02}"
                amount = rng.randint(5_000, 250_000)
                recorded = spent_at + timedelta(days=rng.randint(0, 15))
                event_number += 1
                spend_rows.append((f"se{event_number:06}", business_id, 1, campaign_id,
                                   _iso(spent_at), _iso(recorded), amount, "active"))

                if rng.random() < 0.08:
                    # An ingestion retry repeats the same business event and value.
                    event_number += 1
                    spend_rows.append((f"se{event_number:06}", business_id, 1, campaign_id,
                                       _iso(spent_at), _iso(recorded), amount, "active"))
                if rng.random() < 0.04:
                    # A later correction either changes amount or voids the event.
                    event_number += 1
                    revision_status = "void" if rng.random() < 0.25 else "active"
                    corrected_amount = 0 if revision_status == "void" else rng.randint(1_000, 50_000)
                    spend_rows.append((f"se{event_number:06}", business_id, 2, campaign_id,
                                       _iso(spent_at), _iso(recorded + timedelta(days=1)),
                                       corrected_amount, revision_status))

        approved = conn.execute(
            "SELECT refund_id, refunded_at, recorded_at, amount_cents FROM refunds WHERE status='approved' ORDER BY refund_id"
        ).fetchall()
        reversal_rows = []
        for index, (refund_id, refunded_at, recorded_at, amount) in enumerate(approved, start=1):
            if rng.random() < 0.025 and amount > 1:
                reversed = rng.randint(1, max(1, amount // 2))
                event_at = datetime.fromisoformat(refunded_at.replace("Z", "+00:00")) + timedelta(days=rng.randint(1, 20))
                recorded_time = max(event_at, datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))) + timedelta(days=rng.randint(0, 10))
                reversal_rows.append((f"rv{index:08}", refund_id, _iso(event_at), _iso(recorded_time), reversed))

        conn.executemany("INSERT INTO campaign_spend_events VALUES (?,?,?,?,?,?,?,?)", spend_rows)
        conn.executemany("INSERT INTO refund_reversals VALUES (?,?,?,?,?)", reversal_rows)
        original_fingerprint = conn.execute(
            "SELECT value FROM data_meta WHERE key='data_fingerprint'"
        ).fetchone()[0]
        additive_fingerprint = hashlib.sha256(json.dumps(
            {"seed": seed, "snapshot_at": SNAPSHOT_AT, "spend_events": spend_rows,
             "refund_reversals": reversal_rows}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        fingerprint = hashlib.sha256(f"{RETAIL_VERSION}:{original_fingerprint}:{DATA_VERSION}:{additive_fingerprint}".encode()).hexdigest()
        conn.execute("UPDATE data_meta SET value=? WHERE key='data_version'", (DATA_VERSION,))
        conn.execute("UPDATE data_meta SET value=? WHERE key='seed'", (str(seed),))
        conn.execute("UPDATE data_meta SET value=? WHERE key='data_fingerprint'", (fingerprint,))
        conn.execute("INSERT OR REPLACE INTO data_meta(key,value) VALUES ('marketing_spend_event_count',?)",
                     (str(len(spend_rows)),))
        conn.execute("INSERT OR REPLACE INTO data_meta(key,value) VALUES ('refund_reversal_count',?)",
                     (str(len(reversal_rows)),))
        conn.commit()
    finally:
        conn.close()
    return path


def quality_report(path: str | Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {
            "data_version": conn.execute("SELECT value FROM data_meta WHERE key='data_version'").fetchone()[0],
            "data_fingerprint": conn.execute("SELECT value FROM data_meta WHERE key='data_fingerprint'").fetchone()[0],
            "spend_event_rows": conn.execute("SELECT COUNT(*) FROM campaign_spend_events").fetchone()[0],
            "duplicate_business_events": conn.execute("""SELECT COUNT(*) FROM (
                SELECT business_event_id FROM campaign_spend_events GROUP BY business_event_id HAVING COUNT(*)>1
            )""").fetchone()[0],
            "late_spend_rows_over_1d": conn.execute("""SELECT COUNT(*) FROM campaign_spend_events
                WHERE recorded_at > datetime(spent_at,'+1 day')""").fetchone()[0],
            "refund_reversal_rows": conn.execute("SELECT COUNT(*) FROM refund_reversals").fetchone()[0],
            "reversals_exceeding_approved_refund": conn.execute("""SELECT COUNT(*) FROM (
                SELECT r.refund_id FROM refunds r JOIN refund_reversals rv USING(refund_id)
                WHERE r.status='approved' GROUP BY r.refund_id
                HAVING SUM(rv.amount_cents)>MAX(r.amount_cents)
            )""").fetchone()[0],
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/insightpilot-multidomain.sqlite")
    parser.add_argument("--orders", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    output = create_multidomain_database(args.output, seed=args.seed, order_count=args.orders)
    print(json.dumps({"database": str(output), **quality_report(output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
