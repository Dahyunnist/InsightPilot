"""Execute only compiled queries against a read-only SQLite connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from .sql import CompiledQuery


class QueryExecutionError(RuntimeError):
    pass


def execute_readonly(database_path: Path, query: CompiledQuery) -> tuple[list[dict], str]:
    if not database_path.is_file():
        raise QueryExecutionError("固定数据文件不存在；先运行 python data/seed.py。")
    uri = "file:" + quote(database_path.resolve().as_posix(), safe="/:\\") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            connection.execute("PRAGMA query_only = ON")
            cursor = connection.execute(query.sql, query.params)
            rows = cursor.fetchmany(101)
            if len(rows) > 100:
                raise QueryExecutionError("查询结果超过 100 行限制。")
            columns = [item[0] for item in cursor.description]
            data = [dict(zip(columns, row)) for row in rows]
            version_row = connection.execute("SELECT value FROM data_meta WHERE key = 'data_version'").fetchone()
            version = version_row[0] if version_row else "unknown"
            return data, version
        finally:
            connection.close()
    except QueryExecutionError:
        raise
    except sqlite3.Error as exc:
        raise QueryExecutionError(f"只读查询失败：{exc.__class__.__name__}") from None
