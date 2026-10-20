"""Independent scalar checks for the fixed S0/S4 refund metrics."""

from __future__ import annotations

from threading import Event

from .planner import QueryPlan
from .runner import execute_readonly
from .sql import CompiledQuery


def _order_filters(plan: QueryPlan, alias: str = "o") -> tuple[str, list[str]]:
    filters = [f"{alias}.status = 'paid'", f"{alias}.ordered_at >= ?", f"{alias}.ordered_at < ?"]
    params = [plan.start_date + "T00:00:00Z", plan.end_date + "T00:00:00Z"]
    if plan.region:
        filters.append(f"{alias}.region = ?")
        params.append(plan.region)
    return " AND ".join(filters), params


def compile_verification_query(plan: QueryPlan) -> CompiledQuery:
    if plan.metric_id == "approved_refund_campaign_attribution":
        where, params = _order_filters(plan)
        sql = f"""WITH grouped AS (
 SELECT COALESCE(c.campaign_name,'未归因') AS group_value,
        SUM(COALESCE(r.amount_cents,0)*CASE WHEN a.campaign_id IS NULL THEN 1.0 ELSE a.weight END) AS numerator_cents
 FROM orders o LEFT JOIN refunds r ON r.order_id=o.order_id AND r.status='approved'
 LEFT JOIN campaign_attributions a ON a.order_id=o.order_id
 LEFT JOIN campaigns c ON c.campaign_id=a.campaign_id
 WHERE {where}
 GROUP BY COALESCE(c.campaign_name,'未归因')
)
SELECT group_value,numerator_cents,SUM(numerator_cents) OVER () AS denominator_cents
FROM grouped ORDER BY group_value LIMIT 100"""
        return CompiledQuery(sql, tuple(params))
    if plan.metric_id == "refund_amount_by_status" and plan.dimension == "refund_status":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.refunded_at>=?", "r.refunded_at<?", "o.status='paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region=?")
            params.append(plan.region)
        return CompiledQuery(f"SELECT r.status AS group_value,SUM(r.amount_cents) AS amount_cents FROM refunds r JOIN orders o ON o.order_id=r.order_id WHERE {' AND '.join(filters)} GROUP BY r.status ORDER BY group_value LIMIT 100", tuple(params))
    if plan.dimension is not None and plan.metric_id != "approved_refund_event_amount":
        expressions = {"category": "p.category", "channel": "o.channel", "region": "o.region",
                      "month_utc": "substr(o.ordered_at,1,7)"}
        expression = expressions.get(plan.dimension)
        if expression is None:
            raise ValueError("No grouped verifier exists for this dimension")
        where, params = _order_filters(plan)
        join_product = " LEFT JOIN products p ON p.product_id=o.product_id" if plan.dimension == "category" else ""
        if plan.metric_id == "approved_refund_order_rate":
            value_expr = "1.0*SUM(CASE WHEN r.order_id IS NULL THEN 0 ELSE 1 END)/NULLIF(COUNT(*),0)"
        else:
            value_expr = "SUM(COALESCE(r.refund_cents,0))*1.0/NULLIF(SUM(o.paid_amount_cents),0)"
        sql = f"""WITH refund_by_order AS (
 SELECT order_id,SUM(amount_cents) AS refund_cents FROM refunds WHERE status='approved' GROUP BY order_id
)
SELECT {expression} AS group_value,
       COUNT(*) AS paid_orders,
       SUM(CASE WHEN r.order_id IS NULL THEN 0 ELSE 1 END) AS refunded_orders,
       SUM(CASE WHEN r.order_id IS NULL THEN 0 ELSE 1 END) AS numerator,
       COUNT(*) AS denominator,
       SUM(COALESCE(r.refund_cents,0)) AS numerator_cents,
       SUM(o.paid_amount_cents) AS denominator_cents,
       {value_expr} AS value,
       SUM(COALESCE(r.refund_cents,0)) AS amount_cents
FROM orders o{join_product}
LEFT JOIN refund_by_order r ON r.order_id=o.order_id
WHERE {where}
GROUP BY {expression} ORDER BY group_value LIMIT 100"""
        return CompiledQuery(sql, tuple(params))
    if plan.metric_id == "approved_refund_event_amount" and plan.dimension == "refund_day_utc":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.status='approved'", "r.refunded_at>=?", "r.refunded_at<?", "o.status='paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region=?")
            params.append(plan.region)
        return CompiledQuery(f"SELECT substr(r.refunded_at,1,10) AS group_value,SUM(r.amount_cents) AS amount_cents FROM refunds r JOIN orders o ON o.order_id=r.order_id WHERE {' AND '.join(filters)} GROUP BY group_value ORDER BY group_value LIMIT 100", tuple(params))
    if plan.metric_id == "approved_refund_order_rate" and len(plan.periods) > 1:
        if len(plan.periods) != 2:
            raise ValueError("Period comparison requires two ranges")
        values = []
        params: list[str] = []
        for index, (start, end) in enumerate(plan.periods, start=1):
            values.append("(?, ?, ?)")
            params.extend([f"period_{index}", start + "T00:00:00Z", end + "T00:00:00Z"])
        region_filter = " AND o.region = ?" if plan.region else ""
        if plan.region:
            params.extend([plan.region, plan.region])
        numerator = (
            "SELECT COUNT(*) FROM orders o WHERE o.status = 'paid' "
            "AND o.ordered_at >= p.start_at AND o.ordered_at < p.end_at" + region_filter +
            " AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id AND r.status = 'approved')"
        )
        denominator = (
            "SELECT COUNT(*) FROM orders o WHERE o.status = 'paid' "
            "AND o.ordered_at >= p.start_at AND o.ordered_at < p.end_at" + region_filter
        )
        sql = f"""WITH periods(period_label, start_at, end_at) AS (VALUES {', '.join(values)})
SELECT p.period_label AS group_value, ({numerator}) AS numerator, ({denominator}) AS denominator,
       1.0 * ({numerator}) / NULLIF(({denominator}), 0) AS value
FROM periods p LIMIT 100"""
        # The ratio repeats both scalar subqueries.
        if plan.region:
            params.extend([plan.region, plan.region])
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id == "approved_refund_event_amount":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.status = 'approved'", "r.refunded_at >= ?", "r.refunded_at < ?", "o.status = 'paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region = ?")
            params.append(plan.region)
        return CompiledQuery(
            f"SELECT SUM(r.amount_cents) AS amount_cents FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE {' AND '.join(filters)} LIMIT 1",
            tuple(params),
        )

    if plan.metric_id == "refund_amount_by_status":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.refunded_at >= ?", "r.refunded_at < ?", "o.status = 'paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region = ?")
            params.append(plan.region)
        sql = f"SELECT r.status AS group_value, SUM(r.amount_cents) AS amount_cents FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE {' AND '.join(filters)} GROUP BY r.status ORDER BY r.status LIMIT 100"
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id in ("approved_refund_amount", "approved_refund_amount_contribution"):
        where, params = _order_filters(plan)
        amount = (
            "SELECT SUM(r.amount_cents) FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE "
            + where + " AND r.status = 'approved'"
        )
        if plan.metric_id == "approved_refund_amount_contribution":
            sql = f"SELECT ({amount}) AS numerator_cents, ({amount}) AS denominator_cents, 1.0 * ({amount}) / NULLIF(({amount}), 0) AS value LIMIT 1"
            params *= 4
        else:
            sql = f"SELECT ({amount}) AS amount_cents LIMIT 1"
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id == "approved_refund_order_counts":
        where, params = _order_filters(plan)
        refunded = where + " AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id AND r.status = 'approved')"
        sql = (
            f"SELECT (SELECT COUNT(*) FROM orders o WHERE {where}) AS paid_orders, "
            f"(SELECT COUNT(*) FROM orders o WHERE {refunded}) AS refunded_orders LIMIT 1"
        )
        return CompiledQuery(sql, tuple(params * 2))

    if plan.metric_id == "approved_refund_cohort_summary":
        where, params = _order_filters(plan)
        refunded = where + " AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id AND r.status = 'approved')"
        amount = (
            "SELECT SUM(r.amount_cents) FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE "
            + where + " AND r.status = 'approved'"
        )
        sql = (
            f"SELECT ({amount}) AS numerator_cents, "
            f"(SELECT COUNT(*) FROM orders o WHERE {refunded}) AS refunded_orders, "
            f"(SELECT COUNT(*) FROM orders o WHERE {where}) AS paid_orders LIMIT 1"
        )
        return CompiledQuery(sql, tuple(params * 3))

    where, params = _order_filters(plan)
    if plan.metric_id == "approved_refund_order_rate":
        numerator = (
            "SELECT COUNT(*) FROM orders o WHERE " + where +
            " AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id AND r.status = 'approved')"
        )
        denominator = "SELECT COUNT(*) FROM orders o WHERE " + where
        sql = f"SELECT ({numerator}) AS numerator, ({denominator}) AS denominator, 1.0 * ({numerator}) / NULLIF(({denominator}), 0) AS value LIMIT 1"
        return CompiledQuery(sql, tuple(params * 4))
    if plan.metric_id == "approved_refund_amount_rate":
        numerator = (
            "SELECT SUM(r.amount_cents) FROM refunds r JOIN orders o ON o.order_id = r.order_id "
            "WHERE " + where + " AND r.status = 'approved'"
        )
        denominator = "SELECT SUM(o.paid_amount_cents) FROM orders o WHERE " + where
        sql = f"SELECT ({numerator}) AS numerator_cents, ({denominator}) AS denominator_cents, 1.0 * ({numerator}) / NULLIF(({denominator}), 0) AS value LIMIT 1"
        return CompiledQuery(sql, tuple(params * 4))
    raise ValueError("No verifier exists for this metric")


def verify_result(plan: QueryPlan, rows: list[dict], database_path, cancel_event: Event | None = None) -> dict:
    control_rows, _ = execute_readonly(database_path, compile_verification_query(plan), cancel_event)
    if not control_rows:
        empty_match = not rows
        return {"passed": empty_match, "method": "independent_empty_group_set",
                "checks": {"empty_results_match": empty_match}}

    if plan.dimension is not None and plan.metric_id != "approved_refund_campaign_attribution":
        actual = {row.get("group_value"): row for row in rows}
        expected = {row.get("group_value"): row for row in control_rows}
        checks = {"groups_match": actual.keys() == expected.keys()}
        if checks["groups_match"]:
            if plan.metric_id == "approved_refund_amount_contribution":
                total = sum(row.get("numerator_cents") or 0 for row in control_rows)
                checks["group_values_match"] = all(
                    actual[key].get("numerator_cents") == expected[key].get("numerator_cents")
                    and actual[key].get("denominator_cents") == total
                    and (total == 0 and actual[key].get("value") is None
                         or total != 0 and abs(actual[key].get("value") - expected[key].get("numerator_cents") / total) <= 1e-9)
                    for key in actual
                )
                passed = all(checks.values())
                return {"passed": passed, "method": "independent_grouped_aggregation", "checks": checks, "control": control_rows}
            fields = {
                "approved_refund_order_rate": ("numerator", "denominator", "value"),
                "approved_refund_amount_rate": ("numerator_cents", "denominator_cents", "value"),
                "approved_refund_amount_contribution": ("numerator_cents", "denominator_cents", "value"),
                "approved_refund_amount": ("amount_cents",),
                "approved_refund_order_counts": ("paid_orders", "refunded_orders"),
                "approved_refund_cohort_summary": ("numerator_cents", "refunded_orders", "paid_orders"),
                "approved_refund_event_amount": ("amount_cents",),
                "refund_amount_by_status": ("amount_cents",),
            }.get(plan.metric_id, ())
            checks["group_values_match"] = bool(fields) and all(
                (actual[key].get(field) == expected[key].get(field)
                 or field == "value" and actual[key].get(field) is not None and expected[key].get(field) is not None
                 and abs(actual[key][field] - expected[key][field]) <= 1e-9)
                for key in actual for field in fields
            )
        passed = all(checks.values())
        return {"passed": passed, "method": "independent_grouped_aggregation", "checks": checks, "control": control_rows}

    if plan.metric_id == "approved_refund_campaign_attribution":
        actual = {row["group_value"]: row.get("numerator_cents") for row in rows}
        expected = {row["group_value"]: row.get("numerator_cents") for row in control_rows}
        passed = actual.keys() == expected.keys() and all(abs((actual[key] or 0) - (expected[key] or 0)) < 1e-7 for key in actual)
        denominator = control_rows[0].get("denominator_cents") or 0
        ratios_sum = sum(row.get("value") or 0 for row in rows)
        passed = passed and abs(sum(actual.values()) - denominator) < 1e-7 and (denominator == 0 or abs(ratios_sum - 1.0) < 1e-9)
        return {"passed": passed, "method": "independent_weighted_campaign_aggregation",
                "checks": {"campaign_amounts_match": actual.keys() == expected.keys() and all(abs((actual[key] or 0) - (expected[key] or 0)) < 1e-7 for key in actual),
                           "attributed_total_matches": abs(sum(actual.values()) - denominator) < 1e-7,
                           "contribution_sums_to_one": denominator == 0 or abs(ratios_sum - 1.0) < 1e-9},
                "control": control_rows}

    if plan.metric_id == "refund_amount_by_status" or (
        plan.metric_id == "approved_refund_order_rate" and len(plan.periods) > 1
    ):
        key_fields = (
            ("group_value", "amount_cents") if plan.metric_id == "refund_amount_by_status"
            else ("group_value", "numerator", "denominator", "value")
        )
        actual = {row["group_value"]: tuple(row.get(key) for key in key_fields[1:]) for row in rows}
        expected = {row["group_value"]: tuple(row.get(key) for key in key_fields[1:]) for row in control_rows}
        passed = actual.keys() == expected.keys()
        if passed:
            for key in actual:
                left, right = actual[key], expected[key]
                if len(left) == 1:
                    passed = passed and left == right
                else:
                    passed = passed and left[:2] == right[:2]
                    passed = passed and (
                        left[2] is None and right[2] is None
                        or left[2] is not None and right[2] is not None and abs(left[2] - right[2]) <= 1e-9
                    )
        return {"passed": passed, "method": "independent_group_comparison", "checks": {"groups_match": passed}, "control": control_rows}

    control = control_rows[0]
    if plan.metric_id in ("approved_refund_amount", "approved_refund_event_amount"):
        actual_amount = sum(row.get("amount_cents") or 0 for row in rows)
        expected_amount = control.get("amount_cents") or 0
        passed = actual_amount == expected_amount
        return {"passed": passed, "method": "independent_scalar_sum", "checks": {"amount_matches": passed}, "control": control}

    if plan.metric_id == "approved_refund_order_counts":
        fields = ("paid_orders", "refunded_orders")
        passed = all(rows and rows[0].get(field) == control.get(field) for field in fields)
        return {"passed": passed, "method": "independent_exists_counts", "checks": {field: rows and rows[0].get(field) == control.get(field) for field in fields}, "control": control}

    if plan.metric_id == "approved_refund_cohort_summary":
        fields = ("numerator_cents", "refunded_orders", "paid_orders")
        passed = all(rows and rows[0].get(field) == control.get(field) for field in fields)
        return {"passed": passed, "method": "independent_cohort_aggregation", "checks": {field: rows and rows[0].get(field) == control.get(field) for field in fields}, "control": control}

    numerator_key = "numerator" if plan.metric_id == "approved_refund_order_rate" else "numerator_cents"
    denominator_key = "denominator" if plan.metric_id == "approved_refund_order_rate" else "denominator_cents"
    grouped_numerator = sum((row.get(numerator_key) or 0) for row in rows)
    grouped_denominator = sum((row.get(denominator_key) or 0) for row in rows)
    control_numerator = control.get(numerator_key) or 0
    control_denominator = control.get(denominator_key) or 0
    denominator_match = (
        all((row.get(denominator_key) or 0) == control_denominator for row in rows)
        if plan.metric_id == "approved_refund_amount_contribution"
        else grouped_denominator == control_denominator
    )
    totals_match = grouped_numerator == control_numerator and denominator_match
    values_match = True
    if plan.metric_id == "approved_refund_amount_contribution" and control_denominator:
        values = [row.get("value") for row in rows]
        values_match = all(value is not None for value in values) and abs(sum(values) - 1.0) <= 1e-9
    elif plan.metric_id != "approved_refund_amount_contribution" and control_denominator:
        calculated = grouped_numerator / grouped_denominator
        expected = control.get("value")
        values_match = expected is not None and abs(calculated - expected) <= 1e-9
    return {
        "passed": totals_match and values_match,
        "method": "independent_scalar_aggregation",
        "checks": {
            "numerator_total_matches": grouped_numerator == control_numerator,
            "denominator_total_matches": denominator_match,
            "ratio_or_contribution_matches": values_match,
        },
        "control": control,
    }
