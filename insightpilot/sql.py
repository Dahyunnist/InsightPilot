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
}


def compile_query(plan: QueryPlan) -> CompiledQuery:
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

    if plan.metric_id == "approved_refund_order_rate":
        select_values = (
            "COUNT(r.order_id) AS numerator, "
            "COUNT(*) AS denominator, "
            "1.0 * COUNT(r.order_id) / NULLIF(COUNT(*), 0) AS value"
        )
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
