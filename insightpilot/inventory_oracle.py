"""Independent Python gold calculation for the synthetic inventory fixture."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .planner import QueryPlan


_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("inventory timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def calculate_inventory_turnover(plan: QueryPlan, database_path: Path) -> list[dict]:
    start = datetime.combine(date.fromisoformat(plan.start_date), time.min, tzinfo=_LOCAL_TZ).astimezone(timezone.utc)
    end = datetime.combine(date.fromisoformat(plan.end_date), time.min, tzinfo=_LOCAL_TZ).astimezone(timezone.utc)
    grouped = plan.dimension == "inventory_category"
    inventory_by_day: dict[tuple[str, str], int] = defaultdict(int)
    known_inventory_days: set[tuple[str, str]] = set()
    observed_inventory_groups: set[str] = set()
    cogs_by_group: dict[str, int] = defaultdict(int)

    uri = "file:" + quote(database_path.resolve().as_posix(), safe="/:\\") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        for category, stamp, local_day, cents in connection.execute(
            "SELECT category, snapshot_at, local_date, inventory_value_cents FROM inventory_daily_snapshots"
        ):
            timestamp = _parse_utc(stamp)
            if not start <= timestamp < end or cents is None:
                if start <= timestamp < end:
                    observed_inventory_groups.add(category if grouped else "全部")
                continue
            if timestamp.astimezone(_LOCAL_TZ).date().isoformat() != local_day:
                raise ValueError("inventory local_date does not match Asia/Shanghai timestamp")
            group = category if grouped else "全部"
            observed_inventory_groups.add(group)
            key = (group, local_day)
            inventory_by_day[key] += cents
            known_inventory_days.add(key)

        for category, stamp, cents in connection.execute(
            "SELECT category, sold_at, cogs_cents FROM inventory_cogs_events"
        ):
            timestamp = _parse_utc(stamp)
            if start <= timestamp < end:
                group = category if grouped else "全部"
                cogs_by_group[group] += cents
    finally:
        connection.close()

    daily_values: dict[str, list[int]] = defaultdict(list)
    for group, local_day in known_inventory_days:
        daily_values[group].append(inventory_by_day[(group, local_day)])
    groups = set(daily_values) | set(cogs_by_group) | observed_inventory_groups
    results = []
    for group in sorted(groups):
        daily = daily_values.get(group, [])
        denominator = sum(daily) / len(daily) if daily else None
        numerator = cogs_by_group.get(group, 0)
        results.append({
            "group_value": group,
            "numerator_cents": numerator,
            "denominator_cents": denominator,
            "value": numerator / denominator if denominator else None,
        })
    return results
