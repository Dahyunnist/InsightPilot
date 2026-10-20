"""Compile validated plans into parameterized, read-only SQL templates."""

from __future__ import annotations

from dataclasses import dataclass

from .planner import QueryPlan


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: tuple[str, ...]


DIMENSIONS = {
    None: ("'全部'", None),
    "category": ("p.category", "category"),
    "channel": ("o.channel", "channel"),
    "region": ("o.region", "region"),
    "month_utc": ("substr(o.ordered_at, 1, 7)", "month_utc"),
}


def compile_query(plan: QueryPlan) -> CompiledQuery:
    if plan.metric_id == "approved_refund_campaign_attribution":
        if plan.dimension not in (None, "campaign"):
            raise ValueError("Campaign attribution only supports campaign grouping")
        filters = ["o.status = 'paid'", "o.ordered_at >= ?", "o.ordered_at < ?"]
        params = [plan.start_date + "T00:00:00Z", plan.end_date + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region = ?")
            params.append(plan.region)
        sql = f"""WITH eligible_orders AS (
    SELECT o.order_id
    FROM orders o WHERE {' AND '.join(filters)}
), approved_refunds AS (
    SELECT order_id, SUM(amount_cents) AS refund_cents
    FROM refunds WHERE status='approved' GROUP BY order_id
), base AS (
    SELECT e.order_id, COALESCE(r.refund_cents,0) AS refund_cents
    FROM eligible_orders e LEFT JOIN approved_refunds r ON r.order_id=e.order_id
), attributed_refunds AS (
    SELECT b.order_id, b.refund_cents,
           CASE WHEN a.campaign_id IS NULL THEN '未归因' ELSE c.campaign_name END AS group_value,
           CASE WHEN a.campaign_id IS NULL THEN 1.0 ELSE a.weight END AS attribution_weight
    FROM base b
    LEFT JOIN campaign_attributions a ON a.order_id=b.order_id
    LEFT JOIN campaigns c ON c.campaign_id=a.campaign_id
), campaign_totals AS (
    SELECT group_value, SUM(refund_cents*attribution_weight) AS numerator_cents
    FROM attributed_refunds GROUP BY group_value
)
SELECT group_value, numerator_cents, SUM(numerator_cents) OVER () AS denominator_cents,
       1.0*numerator_cents/NULLIF(SUM(numerator_cents) OVER (),0) AS value
FROM campaign_totals ORDER BY group_value LIMIT 100"""
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id == "approved_refund_event_amount":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.status = 'approved'", "r.refunded_at >= ?", "r.refunded_at < ?", "o.status = 'paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region = ?")
            params.append(plan.region)
        if plan.dimension not in (None, "refund_day_utc"):
            raise ValueError("Event amount only supports UTC refund-day grouping")
        group_expr = "substr(r.refunded_at, 1, 10)" if plan.dimension == "refund_day_utc" else "'全部'"
        group_select = "group_value, " if plan.dimension else ""
        grouped = "GROUP BY group_value" if plan.dimension else ""
        order_by = "ORDER BY group_value" if plan.dimension else ""
        sql = f"""SELECT {group_select}SUM(r.amount_cents) AS amount_cents
FROM refunds r JOIN orders o ON o.order_id = r.order_id
WHERE {' AND '.join(filters)}
{grouped}
{order_by}
LIMIT 100"""
        if plan.dimension:
            sql = sql.replace("SELECT group_value,", f"SELECT {group_expr} AS group_value,")
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id == "refund_amount_by_status":
        start, end = plan.periods[0] if plan.periods else (plan.start_date, plan.end_date)
        filters = ["r.refunded_at >= ?", "r.refunded_at < ?", "o.status = 'paid'"]
        params = [start + "T00:00:00Z", end + "T00:00:00Z"]
        if plan.region:
            filters.append("o.region = ?")
            params.append(plan.region)
        sql = f"""SELECT r.status AS group_value, SUM(r.amount_cents) AS amount_cents
FROM refunds r JOIN orders o ON o.order_id = r.order_id
WHERE {' AND '.join(filters)}
GROUP BY r.status
ORDER BY r.status
LIMIT 100"""
        return CompiledQuery(sql, tuple(params))

    if plan.metric_id == "approved_refund_order_rate" and len(plan.periods) > 1:
        if len(plan.periods) != 2 or plan.dimension is not None:
            raise ValueError("Period comparison supports exactly two periods without a dimension")
        values = []
        params = []
        for index, (start, end) in enumerate(plan.periods, start=1):
            values.append("(?, ?, ?)")
            params.extend([f"period_{index}", start + "T00:00:00Z", end + "T00:00:00Z"])
        region_filter = " AND o.region = ?" if plan.region else ""
        if plan.region:
            params.append(plan.region)
        sql = f"""WITH periods(period_label, start_at, end_at) AS (VALUES {', '.join(values)}),
eligible_orders AS (
    SELECT o.order_id, p.period_label AS group_value
    FROM orders o JOIN periods p ON o.ordered_at >= p.start_at AND o.ordered_at < p.end_at
    WHERE o.status = 'paid'{region_filter}
), approved_refunds AS (
    SELECT order_id FROM refunds WHERE status = 'approved' GROUP BY order_id
)
SELECT b.group_value, COUNT(r.order_id) AS numerator, COUNT(*) AS denominator,
       1.0 * COUNT(r.order_id) / NULLIF(COUNT(*), 0) AS value
FROM eligible_orders b LEFT JOIN approved_refunds r ON r.order_id = b.order_id
GROUP BY b.group_value ORDER BY b.group_value LIMIT 100"""
        return CompiledQuery(sql, tuple(params))

    if plan.dimension not in DIMENSIONS:
        raise ValueError("Unsupported dimension")
    dim_expr, dim_alias = DIMENSIONS[plan.dimension]
    selected_dim = f", {dim_expr} AS group_value" if dim_alias else ", '全部' AS group_value"
    grouped = "GROUP BY group_value" if dim_alias else ""
    order_by = "ORDER BY group_value" if dim_alias else ""
    filters = ["o.status = 'paid'", "o.ordered_at >= ?", "o.ordered_at < ?"]
    params = [plan.start_date + "T00:00:00Z", plan.end_date + "T00:00:00Z"]
    if plan.region:
        filters.append("o.region = ?")
        params.append(plan.region)
    join_product = " LEFT JOIN products p ON p.product_id = o.product_id" if dim_alias == "category" else ""
    group_select = "group_value, " if dim_alias else ""
    filter_sql = " AND ".join(filters)

    if plan.metric_id in ("approved_refund_order_rate", "approved_refund_order_counts"):
        if plan.metric_id == "approved_refund_order_counts":
            select_values = "COUNT(*) AS paid_orders, COUNT(r.order_id) AS refunded_orders"
        else:
            select_values = (
                "COUNT(r.order_id) AS numerator, "
                "COUNT(*) AS denominator, "
                "1.0 * COUNT(r.order_id) / NULLIF(COUNT(*), 0) AS value"
            )
    elif plan.metric_id == "approved_refund_cohort_summary":
        select_values = (
            "SUM(COALESCE(r.refund_cents, 0)) AS numerator_cents, "
            "COUNT(r.order_id) AS refunded_orders, COUNT(*) AS paid_orders"
        )
    elif plan.metric_id == "approved_refund_amount":
        select_values = "SUM(COALESCE(r.refund_cents, 0)) AS amount_cents"
    elif plan.metric_id == "approved_refund_amount_rate":
        select_values = (
            "SUM(COALESCE(r.refund_cents, 0)) AS numerator_cents, "
            "SUM(b.paid_amount_cents) AS denominator_cents, "
            "1.0 * SUM(COALESCE(r.refund_cents, 0)) / NULLIF(SUM(b.paid_amount_cents), 0) AS value"
        )
    elif plan.metric_id == "approved_refund_amount_contribution":
        select_values = (
            "SUM(COALESCE(r.refund_cents, 0)) AS numerator_cents, "
            "(SELECT SUM(COALESCE(r2.refund_cents, 0)) FROM eligible_orders b2 "
            "LEFT JOIN approved_refunds r2 ON r2.order_id = b2.order_id) AS denominator_cents, "
            "1.0 * SUM(COALESCE(r.refund_cents, 0)) / NULLIF((SELECT SUM(COALESCE(r2.refund_cents, 0)) "
            "FROM eligible_orders b2 LEFT JOIN approved_refunds r2 ON r2.order_id = b2.order_id), 0) AS value"
        )
    else:
        raise ValueError("Unsupported S1 metric")

    sql = f"""WITH eligible_orders AS (
    SELECT o.order_id, o.paid_amount_cents{selected_dim}
    FROM orders o{join_product}
    WHERE {filter_sql}
), approved_refunds AS (
    SELECT order_id, SUM(amount_cents) AS refund_cents
    FROM refunds
    WHERE status = 'approved'
    GROUP BY order_id
), base AS (
    SELECT b.order_id, b.paid_amount_cents, b.group_value
    FROM eligible_orders b
)
SELECT {group_select}{select_values}
FROM base b
LEFT JOIN approved_refunds r ON r.order_id = b.order_id
{grouped}
{order_by}
LIMIT 100"""
    return CompiledQuery(sql, tuple(params))
