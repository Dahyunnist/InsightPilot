"""Execute only compiled queries against a read-only SQLite connection."""

from __future__ import annotations

import sqlite3
import time
from threading import Event
from pathlib import Path
from urllib.parse import quote

from .sql import CompiledQuery
from .sql_policy import ALLOWED_COLUMNS, ALLOWED_FUNCTIONS, ALLOWED_TABLES, MAX_ROWS, SQLPolicyError, validate_sql


class QueryExecutionError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "query_execution"):
        super().__init__(message)
        self.failure_code = failure_code


class QueryTimeoutError(QueryExecutionError):
    def __init__(self, message: str, *, failure_code: str = "statement_timeout"):
        super().__init__(message, failure_code=failure_code)


class QueryBudgetError(QueryExecutionError):
    pass


class QueryCancelledError(QueryExecutionError):
    pass


QUERY_TIMEOUT_SECONDS = 2.0
PROGRESS_OP_INTERVAL = 1000
MAX_VM_STEPS = 250_000


def _authorize(action: int, arg1: str | None, arg2: str | None, _database: str | None, _source: str | None) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if arg1 in ALLOWED_TABLES and (not arg2 or arg2 in ALLOWED_COLUMNS):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        function = (arg2 or arg1 or "").upper()
        return sqlite3.SQLITE_OK if function in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def execute_readonly(database_path: Path, query: CompiledQuery, cancel_event: Event | None = None) -> tuple[list[dict], str]:
    if cancel_event is not None and cancel_event.is_set():
        raise QueryCancelledError("分析已取消。")
    if not database_path.is_file():
        raise QueryExecutionError("固定数据文件不存在；先运行 python data/seed.py。")
    try:
        validate_sql(query.sql)
    except SQLPolicyError as exc:
        raise QueryExecutionError(f"SQL 策略拒绝：{exc}") from None
    uri = "file:" + quote(database_path.resolve().as_posix(), safe="/:\\") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_SECONDS)
        try:
            connection.execute("PRAGMA query_only = ON")
            version_row = connection.execute("SELECT value FROM data_meta WHERE key = 'data_version'").fetchone()
            version = version_row[0] if version_row else "unknown"
            vm_budget = MAX_VM_STEPS * 20 if version in (
                "insightpilot-enterprise-v1", "insightpilot-multidomain-v1"
            ) else MAX_VM_STEPS
            deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
            execution_state = {"steps": 0, "timed_out": False, "over_budget": False}

            def check_progress() -> int:
                execution_state["steps"] += PROGRESS_OP_INTERVAL
                if cancel_event is not None and cancel_event.is_set():
                    execution_state["cancelled"] = True
                    return 1
                if execution_state["steps"] > vm_budget:
                    execution_state["over_budget"] = True
                    return 1
                if time.monotonic() >= deadline:
                    execution_state["timed_out"] = True
                    return 1
                return 0

            connection.set_authorizer(_authorize)
            connection.set_progress_handler(check_progress, PROGRESS_OP_INTERVAL)
            cursor = connection.execute(query.sql, query.params)
            rows = cursor.fetchmany(MAX_ROWS + 1)
            if len(rows) > MAX_ROWS:
                raise QueryExecutionError("查询结果超过 100 行限制。")
            columns = [item[0] for item in cursor.description]
            data = [dict(zip(columns, row)) for row in rows]
            return data, version
        finally:
            connection.close()
    except QueryExecutionError:
        raise
    except sqlite3.Error as exc:
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc).lower():
            if 'execution_state' in locals() and execution_state.get("cancelled"):
                raise QueryCancelledError("分析已取消。") from None
            if 'execution_state' in locals() and execution_state["over_budget"]:
                raise QueryBudgetError("查询超过 250,000 条 SQLite 虚拟机指令预算。") from None
            raise QueryTimeoutError("查询超过 2 秒时间限制。") from None
        raise QueryExecutionError(f"只读查询失败：{exc.__class__.__name__}") from None


def read_data_identity(database_path: Path) -> tuple[str, str | None]:
    if not database_path.is_file():
        raise QueryExecutionError("固定数据文件不存在。")
    uri = "file:" + quote(database_path.resolve().as_posix(), safe="/:\\") + "?mode=ro"
    connection = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_SECONDS)
        connection.execute("PRAGMA query_only=ON")
        rows = dict(connection.execute("SELECT key,value FROM data_meta WHERE key IN ('data_version','data_fingerprint')"))
        return rows.get("data_version", "unknown"), rows.get("data_fingerprint")
    except sqlite3.Error as exc:
        raise QueryExecutionError(f"无法读取数据版本：{exc.__class__.__name__}") from None
    finally:
        if connection is not None:
            connection.close()


def read_data_version(database_path: Path) -> str:
    return read_data_identity(database_path)[0]
