"""Independent scalar checks for grouped refund results."""

from __future__ import annotations

from .planner import QueryPlan
from .runner import execute_readonly
from .sql import CompiledQuery


def compile_verification_query(plan: QueryPlan) -> CompiledQuery:
    filters = ["o.status = 'paid'", "o.ordered_at >= ?", "o.ordered_at < ?"]
    params = [plan.start_date + "T00:00:00Z", plan.end_date + "T00:00:00Z"]
    if plan.region:
        filters.append("o.region = ?")
        params.append(plan.region)
    where = " AND ".join(filters)
    if plan.metric_id == "approved_refund_order_rate":
        numerator = (
            "SELECT COUNT(*) FROM orders o WHERE " + where +
            " AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id AND r.status = 'approved')"
        )
        denominator = "SELECT COUNT(*) FROM orders o WHERE " + where
        sql = f"SELECT ({numerator}) AS numerator, ({denominator}) AS denominator, " \
              f"1.0 * ({numerator}) / NULLIF(({denominator}), 0) AS value LIMIT 1"
        return CompiledQuery(sql, tuple(params * 4))
    if plan.metric_id == "approved_refund_amount_rate":
        numerator = (
            "SELECT SUM(r.amount_cents) FROM refunds r JOIN orders o ON o.order_id = r.order_id "
            "WHERE " + where + " AND r.status = 'approved'"
        )
        denominator = "SELECT SUM(o.paid_amount_cents) FROM orders o WHERE " + where
        sql = f"SELECT ({numerator}) AS numerator_cents, ({denominator}) AS denominator_cents, " \
              f"1.0 * ({numerator}) / NULLIF(({denominator}), 0) AS value LIMIT 1"
        return CompiledQuery(sql, tuple(params * 4))
    if plan.metric_id == "approved_refund_amount_contribution":
        numerator = (
            "SELECT SUM(r.amount_cents) FROM refunds r JOIN orders o ON o.order_id = r.order_id "
            "WHERE " + where + " AND r.status = 'approved'"
        )
        sql = f"SELECT ({numerator}) AS numerator_cents, ({numerator}) AS denominator_cents, " \
              f"1.0 * ({numerator}) / NULLIF(({numerator}), 0) AS value LIMIT 1"
        return CompiledQuery(sql, tuple(params * 4))
    raise ValueError("No S2 verifier exists for this metric")


def verify_result(plan: QueryPlan, rows: list[dict], database_path) -> dict:
    control_rows, _ = execute_readonly(database_path, compile_verification_query(plan))
    if not control_rows:
        return {"passed": False, "reason": "independent_check_returned_no_row"}
    control = control_rows[0]
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
