"""Read-only query engine adapters for compiled MetricPlan queries."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlglot import transpile

from .runner import (
    QueryCancelledError,
    QueryExecutionError,
    QueryTimeoutError,
    execute_readonly,
    read_data_identity as read_sqlite_data_identity,
)
from .sql import CompiledQuery
from .sql_policy import MAX_ROWS, SQLPolicyError, validate_sql


@runtime_checkable
class QueryEngine(Protocol):
    """Execute only compiler-produced, validated, parameterized read queries."""

    def read_data_identity(self, *, tenant_id: str | None = None) -> tuple[str, str | None]: ...

    def execute_readonly(
        self,
        query: CompiledQuery,
        *,
        tenant_id: str | None = None,
        cancel_event: Any | None = None,
    ) -> tuple[list[dict[str, Any]], str]: ...


class SQLiteQueryEngine:
    """SQLite compatibility adapter retaining existing authorizer and budgets."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def execute_readonly(self, query: CompiledQuery, *, tenant_id: str | None = None,
                         cancel_event: Any | None = None) -> tuple[list[dict[str, Any]], str]:
        if tenant_id is not None:
            raise QueryExecutionError("SQLite adapter has no tenant isolation contract.")
        return execute_readonly(self.database_path, query, cancel_event)

    def read_data_identity(self, *, tenant_id: str | None = None) -> tuple[str, str | None]:
        if tenant_id is not None:
            raise QueryExecutionError("SQLite adapter has no tenant isolation contract.")
        return read_sqlite_data_identity(self.database_path)


class PostgresQueryEngine:
    """PostgreSQL adapter using a bounded psycopg pool and transaction-local RLS scope."""

    def __init__(self, dsn: str, *, tenant_id: str | None = None,
                 pool: Any | None = None, max_rows: int = MAX_ROWS,
                 statement_timeout_ms: int = 2000):
        if not dsn and pool is None:
            raise ValueError("A PostgreSQL DSN is required.")
        if not 1 <= max_rows <= MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {MAX_ROWS}.")
        if statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be positive.")
        self.tenant_id = tenant_id
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        self._pool = pool
        self._dsn = dsn

    def _connection_pool(self):
        if self._pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError:
                raise QueryExecutionError(
                    "PostgreSQL support requires psycopg[binary,pool]."
                ) from None
            self._pool = ConnectionPool(
                conninfo=self._dsn,
                min_size=1,
                max_size=8,
                timeout=5,
                kwargs={"autocommit": False},
                open=True,
            )
        return self._pool

    def execute_readonly(self, query: CompiledQuery, *, tenant_id: str | None = None,
                         cancel_event: Any | None = None) -> tuple[list[dict[str, Any]], str]:
        effective_tenant = tenant_id or self.tenant_id
        if not effective_tenant or not effective_tenant.strip():
            raise QueryExecutionError("A trusted tenant_id is required for PostgreSQL RLS.",
                                      failure_code="tenant_scope_missing")
        if cancel_event is not None and cancel_event.is_set():
            raise QueryCancelledError("分析已取消。")
        try:
            validate_sql(query.sql)
            from sqlglot import exp, parse_one

            placeholders = list(parse_one(query.sql, read="sqlite").find_all(exp.Placeholder))
            if len(placeholders) != len(query.params):
                raise SQLPolicyError("SQL placeholder count does not match bound parameters")
            postgres_sql = transpile(query.sql, read="sqlite", write="postgres", pretty=False)[0]
        except (SQLPolicyError, IndexError, ValueError) as exc:
            raise QueryExecutionError(f"SQL 策略拒绝：{exc}", failure_code="sql_policy") from None

        try:
            with self._connection_pool().connection() as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL ROLE insightpilot_reader")
                    connection.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self.statement_timeout_ms}ms",),
                    )
                    connection.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (effective_tenant,),
                    )
                    cursor = connection.execute(postgres_sql, query.params)
                    rows = cursor.fetchmany(self.max_rows + 1)
                    if len(rows) > self.max_rows:
                        raise QueryExecutionError(f"查询结果超过 {self.max_rows} 行限制.",
                                                  failure_code="result_limit")
                    if cancel_event is not None and cancel_event.is_set():
                        raise QueryCancelledError("分析已取消。")
                    columns = [item.name for item in cursor.description]
                    data = [{key: float(value) if isinstance(value, Decimal) else value
                             for key, value in zip(columns, row)} for row in rows]
                    return data, "postgresql"
        except QueryExecutionError:
            raise
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate == "57014":
                raise QueryTimeoutError("PostgreSQL 查询超过 statement_timeout 限制。") from None
            if sqlstate == "42501":
                code = "permission_denied"
            elif sqlstate == "42601":
                code = "sql_syntax"
            elif sqlstate in ("42P01", "42703") or (
                    isinstance(sqlstate, str) and sqlstate.startswith("3F")):
                code = "schema_error"
            else:
                code = "database_execution"
            raise QueryExecutionError(f"PostgreSQL 只读查询失败：{exc.__class__.__name__}",
                                      failure_code=code) from None

    def read_data_identity(self, *, tenant_id: str | None = None) -> tuple[str, str | None]:
        effective_tenant = tenant_id or self.tenant_id
        if not effective_tenant or not effective_tenant.strip():
            raise QueryExecutionError("A trusted tenant_id is required for PostgreSQL RLS.",
                                      failure_code="tenant_scope_missing")
        try:
            with self._connection_pool().connection() as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL ROLE insightpilot_reader")
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (effective_tenant,))
                    cursor = connection.execute(
                        "SELECT key, value FROM data_meta WHERE key IN ('data_version','data_fingerprint')")
                    metadata = dict(cursor.fetchall())
                    return metadata.get("data_version", "unknown"), metadata.get("data_fingerprint")
        except QueryExecutionError:
            raise
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            code = ("schema_error" if isinstance(sqlstate, str) and sqlstate.startswith(("42", "3F"))
                    else "database_execution")
            raise QueryExecutionError(f"PostgreSQL 数据身份读取失败：{exc.__class__.__name__}",
                                      failure_code=code) from None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
