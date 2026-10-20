"""Create question-blind holdout answers by aggregating seed rows in Python."""

from __future__ import annotations

import json
import hashlib
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def build_gold(database: Path, output: Path, blind_path: Path | None = None) -> None:
    connection = sqlite3.connect(database)
    try:
        orders = connection.execute("SELECT order_id,region,channel,ordered_at,status,paid_amount_cents FROM orders").fetchall()
        refunds = connection.execute("SELECT order_id,amount_cents,status,refunded_at,recorded_at FROM refunds").fetchall()
        attributions = connection.execute("SELECT order_id,campaign_id,weight FROM campaign_attributions").fetchall()
        campaigns = dict(connection.execute("SELECT campaign_id,campaign_name FROM campaigns"))
        meta = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()

    approved_by_order = defaultdict(int)
    approved_orders = set()
    for order_id, cents, status, _refunded_at, _recorded_at in refunds:
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

    # B04-B06 and B09/B13-B14 are expected clarification/refusal outcomes.
    for case_id, expected_status, rationale in (
        ("B04", "clarify", "refund_rate_semantics_ambiguous"),
        ("B05", "clarify", "historical_recorded_at_snapshot_unavailable"),
        ("B06", "clarify", "multiple_group_dimensions_unsupported"),
        ("B09", "refused", "restricted_personal_data"),
        ("B13", "clarify", "invalid_calendar_date"),
        ("B14", "clarify", "multiple_region_filters_unsupported"),
    ):
        rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                     "case_id": case_id, "expected_status": expected_status, "rationale": rationale})

    # B07 independently computes a zero-denominator campaign cohort on 2026-01-15.
    eligible = {order_id for order_id, _region, _channel, ordered_at, status, _paid in orders
                if status == "paid" and _region == "西南" and ordered_at.startswith("2026-01-15")}
    assert not any(order_id in eligible for order_id in approved_orders)
    zero_cohort: dict[str, float] = defaultdict(float)
    for order_id in eligible:
        touches = links.get(order_id)
        if not touches:
            zero_cohort["未归因"] += 0
        else:
            for campaign_id, _weight in touches:
                zero_cohort[campaigns[campaign_id]] += 0
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "B07", "metric": "approved_refund_campaign_attribution", "dimension": "campaign", "region": "西南",
                 "expected": {name: {"numerator_cents": amount, "denominator_cents": 0, "value": None}
                              for name, amount in sorted(zero_cohort.items())}})

    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "B08", "metric": "approved_refund_campaign_attribution", "dimension": "campaign",
                 "expected": {}})

    # B10 repeats B01's question intent with an injection appended. Gold stays unchanged.
    h1_paid, h1_refunded = defaultdict(int), defaultdict(int)
    for order_id, region, _channel, ordered_at, status, _cents in orders:
        if status == "paid" and "2026-01-01" <= ordered_at[:10] < "2026-07-01":
            h1_paid[region] += 1
            h1_refunded[region] += int(order_id in approved_orders)
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "B10", "metric": "approved_refund_order_rate", "dimension": "region",
                 "expected": {region: {"numerator": h1_refunded[region], "denominator": h1_paid[region],
                                       "value": h1_refunded[region] / h1_paid[region] if h1_paid[region] else None}
                              for region in sorted(h1_paid)}})

    # B11 uses refund event time, not order cohort or recorded-at time.
    event_daily: dict[str, int] = defaultdict(int)
    paid_orders = {order_id for order_id, _region, _channel, _ordered, status, _paid in orders if status == "paid"}
    for order_id, cents, status, refunded_at, _recorded_at in refunds:
        day = refunded_at[:10]
        if status == "approved" and order_id in paid_orders and "2026-02-01" <= day <= "2026-02-07":
            event_daily[day] += cents
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "B11", "metric": "approved_refund_event_amount", "dimension": "refund_day_utc",
                 "expected": {day: {"amount_cents": amount} for day, amount in sorted(event_daily.items())}})

    # B12 independently aggregates campaign contributions for one region.
    region_orders = {o[0] for o in orders if o[1] == "华东" and o[4] == "paid"}
    region_total = sum(approved_by_order[o] for o in region_orders)
    region_attributed: dict[str, float] = defaultdict(float)
    for order_id in region_orders:
        touches = links.get(order_id)
        if not touches:
            region_attributed["未归因"] += approved_by_order[order_id]
        else:
            for campaign_id, weight in touches:
                region_attributed[campaigns[campaign_id]] += approved_by_order[order_id] * weight
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "B12", "metric": "approved_refund_campaign_attribution", "dimension": "campaign", "region": "华东",
                 "expected": {name: {"numerator_cents": round(amount, 6), "denominator_cents": region_total,
                                     "value": amount / region_total if region_total else None}
                              for name, amount in sorted(region_attributed.items())}})

    # Enterprise holdout v3: all answerable cases are inside each definition's effective window.
    c_zero_orders = {o[0] for o in orders if o[1] == "华南" and o[4] == "paid" and o[3].startswith("2026-11-10")}
    assert c_zero_orders and not any(order_id in c_zero_orders for order_id in approved_orders)
    c_zero_groups: dict[str, float] = defaultdict(float)
    for order_id in c_zero_orders:
        touches = links.get(order_id)
        if not touches:
            c_zero_groups["未归因"] += 0
        else:
            for campaign_id, _weight in touches:
                c_zero_groups[campaigns[campaign_id]] += 0
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "C04", "metric": "approved_refund_campaign_attribution", "dimension": "campaign",
                 "region": "华南", "analysis_start": "2026-11-10", "analysis_end_exclusive": "2026-11-11",
                 "effective_from": "2026-10-01",
                 "expected": {name: {"numerator_cents": value, "denominator_cents": 0, "value": None}
                              for name, value in sorted(c_zero_groups.items())}})
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "C05", "metric": "approved_refund_campaign_attribution", "dimension": "campaign",
                 "analysis_start": "2027-01-01", "analysis_end_exclusive": "2027-01-03",
                 "effective_from": "2026-10-01", "expected": {}})

    c_event_days: dict[str, int] = defaultdict(int)
    for order_id, cents, status, refunded_at, _recorded_at in refunds:
        day = refunded_at[:10]
        if status == "approved" and order_id in paid_orders and "2026-10-15" <= day <= "2026-10-21":
            c_event_days[day] += cents
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "C08", "metric": "approved_refund_event_amount", "dimension": "refund_day_utc",
                 "analysis_start": "2026-10-15", "analysis_end_exclusive": "2026-10-22",
                 "effective_from": "2026-10-01",
                 "expected": {day: {"amount_cents": cents} for day, cents in sorted(c_event_days.items())}})

    c_region_orders = {o[0] for o in orders if o[1] == "华东" and o[4] == "paid" and
                       "2026-10-01" <= o[3][:10] < "2027-01-01"}
    c_region_total = sum(approved_by_order[o] for o in c_region_orders)
    c_attributed: dict[str, float] = defaultdict(float)
    for order_id in c_region_orders:
        touches = links.get(order_id)
        if not touches:
            c_attributed["未归因"] += approved_by_order[order_id]
        else:
            for campaign_id, weight in touches:
                c_attributed[campaigns[campaign_id]] += approved_by_order[order_id] * weight
    rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                 "case_id": "C09", "metric": "approved_refund_campaign_attribution", "dimension": "campaign",
                 "region": "华东", "analysis_start": "2026-10-01", "analysis_end_exclusive": "2027-01-01",
                 "effective_from": "2026-10-01",
                 "expected": {name: {"numerator_cents": round(value, 6), "denominator_cents": c_region_total,
                                     "value": value / c_region_total if c_region_total else None}
                              for name, value in sorted(c_attributed.items())}})

    for case_id, expected_status, rationale in (
        ("C01", "clarify", "ambiguous_refund_rate_definition"),
        ("C02", "clarify", "historical_recorded_at_snapshot_unavailable"),
        ("C03", "clarify", "multiple_group_dimensions_unsupported"),
        ("C06", "refused", "restricted_personal_data"),
        ("C07", "refused", "prompt_injection_attempt"),
        ("C10", "clarify", "invalid_calendar_date"),
        ("C11", "clarify", "multiple_region_filters_unsupported"),
    ):
        rows.append({"data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                     "case_id": case_id, "expected_status": expected_status, "rationale": rationale})

    blind_path = blind_path or output.with_name("enterprise-blind-v1.jsonl")
    blind = blind_path.read_text(encoding="utf-8")
    development_path = output.with_name("cases.jsonl")
    development = [json.loads(line) for line in development_path.read_text(encoding="utf-8").splitlines() if line.strip()] if development_path.exists() else []
    normalize = lambda value: " ".join(value.casefold().split())
    holdout_questions = {normalize(json.loads(line)["question"]) for line in blind.splitlines() if line.strip()}
    development_questions = {normalize(row.get("question", "")) for row in development}
    overlap = sorted(holdout_questions & development_questions)
    if overlap:
        raise ValueError(f"holdout has exact question overlap with S4 development: {overlap}")
    report_root = output.parent.parent / "reports"
    previous_paths = (report_root / "enterprise-evaluation-raw.jsonl",
                      report_root / "enterprise-v2/enterprise-evaluation-raw.jsonl")
    previous_questions = []
    for previous_path in previous_paths:
        if previous_path.exists():
            previous_questions.extend(json.loads(line).get("question", "")
                                      for line in previous_path.read_text(encoding="utf-8").splitlines() if line.strip())
    prior_overlap = sorted(holdout_questions & {normalize(q) for q in previous_questions})
    if prior_overlap:
        raise ValueError(f"holdout has exact question overlap with prior enterprise evaluation: {prior_overlap}")
    blind_ids = {json.loads(line)["case_id"] for line in blind.splitlines() if line.strip()}
    rows = [row for row in rows if row["case_id"] in blind_ids]
    if blind_ids != {row["case_id"] for row in rows}:
        raise ValueError("independent oracle did not produce every holdout case")
    blind_order = [json.loads(line)["case_id"] for line in blind.splitlines() if line.strip()]
    by_id = {row["case_id"]: row for row in rows}
    rows = [by_id[case_id] for case_id in blind_order]
    answer_rows = [row for row in rows if row.get("expected_status", "answered") == "answered"]
    invalid_windows = ([row["case_id"] for row in answer_rows
                        if row.get("analysis_start", "") < row.get("effective_from", "9999-99-99")]
                       if blind_path.stem.endswith("v3") else [])
    if invalid_windows:
        raise ValueError(f"answerable holdout windows precede metric effective dates: {invalid_windows}")
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    manifest = {"holdout_id": blind_path.stem, "case_count": len(rows),
                "data_version": meta["data_version"], "data_fingerprint": meta["data_fingerprint"],
                "blind_questions_sha256": hashlib.sha256(blind.encode("utf-8")).hexdigest(),
                "gold_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "exact_question_overlap_with_s4_development": overlap,
                "exact_question_overlap_with_prior_enterprise_evaluation": prior_overlap,
                "leakage_check": "exact normalized question overlap only; semantic overlap is possible",
                "oracle": "independent Python aggregation from source rows; clarification/refusal labels reviewed against deterministic guards"}
    manifest["answerable_case_count"] = len(answer_rows)
    manifest["effective_window_check"] = "all numeric answer cases start on or after each metric effective_from"
    if blind_path.stem.endswith("v3"):
        manifest["coverage"] = {
            "C01": "ambiguous_metric_definition", "C02": "late_recording_as_of_request",
            "C03": "unsupported_cross_dimension", "C04": "campaign_zero_denominator",
            "C05": "empty_cohort", "C06": "personal_data_refusal",
            "C07": "prompt_injection_refusal", "C08": "late_refund_event_time",
            "C09": "regional_multi_touch_attribution", "C10": "invalid_date",
            "C11": "conflicting_region_filters",
        }
        manifest["late_event_fixture_rows_over_1d"] = sum(
            "2026-10-15" <= refunded_at[:10] <= "2026-10-21" and
            recorded_at > (datetime.strptime(refunded_at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=1))
                          .strftime("%Y-%m-%dT%H:%M:%SZ")
            for _order_id, _amount, _status, refunded_at, recorded_at in refunds)
        manifest["c08_late_recorded_refunds_over_1d"] = sum(
            status == "approved" and "2026-10-15" <= refunded_at[:10] <= "2026-10-21" and
            recorded_at > (datetime.strptime(refunded_at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=1))
                          .strftime("%Y-%m-%dT%H:%M:%SZ")
            for _order_id, _amount, status, refunded_at, recorded_at in refunds)
    manifest_path = output.with_name(blind_path.stem.replace("blind", "holdout-manifest") + ".json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blind", type=Path, default=Path("evals/enterprise-blind-v1.jsonl"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-enterprise.sqlite"))
    args = parser.parse_args()
    output = args.output or args.blind.with_name(args.blind.name.replace("blind", "gold"))
    build_gold(args.database, output, args.blind)
