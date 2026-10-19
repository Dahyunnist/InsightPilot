"""SQLGlot AST policy for the S2 read-only query boundary."""

from __future__ import annotations

import sqlglot
from sqlglot import exp


ALLOWED_TABLES = {"orders", "refunds", "products"}
ALLOWED_CTES = {"eligible_orders", "approved_refunds", "base", "periods"}
ALLOWED_COLUMNS = {
    "order_id", "paid_amount_cents", "product_id", "category", "channel", "region",
    "ordered_at", "status", "amount_cents", "refund_cents", "group_value",
    "numerator", "denominator", "value", "numerator_cents", "denominator_cents",
    "period_label", "start_at", "end_at",
    "refunded_at",
}
ALLOWED_FUNCTIONS = {"COUNT", "SUM", "COALESCE", "NULLIF", "SUBSTR", "SUBSTRING"}
MAX_SQL_CHARS = 20_000
MAX_AST_NODES = 500
MAX_ROWS = 100


class SQLPolicyError(ValueError):
    """Query violates the S2 allowlist."""


def validate_sql(sql: str) -> None:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise SQLPolicyError("SQL is empty or exceeds the size limit")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError:
        raise SQLPolicyError("SQL could not be parsed") from None
    if len(statements) != 1 or statements[0] is None:
        raise SQLPolicyError("Exactly one SQL statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SQLPolicyError("Only SELECT queries are allowed")
    nodes = list(tree.walk())
    if len(nodes) > MAX_AST_NODES:
        raise SQLPolicyError("SQL AST exceeds the node limit")
    if any(isinstance(node, (exp.DML, exp.DDL, exp.Command)) for node in nodes):
        raise SQLPolicyError("DDL and DML are not allowed")

    with_clause = tree.args.get("with_")
    if with_clause:
        if with_clause.args.get("recursive"):
            raise SQLPolicyError("Recursive CTEs are not allowed")
        cte_names = {cte.alias_or_name.lower() for cte in with_clause.expressions}
        if not cte_names or not cte_names.issubset(ALLOWED_CTES):
            raise SQLPolicyError("CTE name is outside the allowlist")
        if len(cte_names) > len(ALLOWED_CTES):
            raise SQLPolicyError("Too many CTEs")

    found_physical_table = False
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.db or table.catalog:
            raise SQLPolicyError("Cross-database references are not allowed")
        if name in ALLOWED_TABLES:
            found_physical_table = True
        elif name not in ALLOWED_CTES:
            raise SQLPolicyError("Table is outside the allowlist")
    if not found_physical_table:
        raise SQLPolicyError("Query must read an approved data table")

    for column in tree.find_all(exp.Column):
        if column.db or column.catalog or column.name.lower() not in ALLOWED_COLUMNS:
            raise SQLPolicyError("Column is outside the allowlist")
    for function in tree.find_all(exp.Func):
        if isinstance(function, (exp.Binary, exp.Unary, exp.Predicate)):
            continue
        if type(function).__name__.upper() not in ALLOWED_FUNCTIONS:
            raise SQLPolicyError("Function is outside the allowlist")
    for star in tree.find_all(exp.Star):
        values_projection = (
            isinstance(star.parent, exp.Select)
            and star.parent.args.get("from_") is not None
            and isinstance(star.parent.args["from_"].this, exp.Values)
        )
        if not isinstance(star.parent, exp.Count) and not values_projection:
            raise SQLPolicyError("Wildcard projection is not allowed")

    limit = tree.args.get("limit")
    if not limit or not isinstance(limit.expression, exp.Literal) or not limit.expression.is_number:
        raise SQLPolicyError("A numeric LIMIT is required")
    try:
        row_limit = int(limit.expression.this)
    except (TypeError, ValueError):
        raise SQLPolicyError("LIMIT must be an integer") from None
    if row_limit < 1 or row_limit > MAX_ROWS:
        raise SQLPolicyError("LIMIT must be between 1 and 100")
