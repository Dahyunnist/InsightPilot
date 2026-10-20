"""Durable, redacted run records for the local analysis API."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(question: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", question)
    value = re.sub(r"(?<!\d)1\d{10}(?!\d)", "[PHONE]", value)
    value = re.sub(r"(?<!\d)\d{15}(?:\d{2}[\dXx])?(?!\d)", "[ID]", value)
    return value[:2000]


class RunStore:
    def __init__(self, path: Path, retention_days: int = 90):
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                status TEXT NOT NULL, question_hash TEXT NOT NULL, question_redacted TEXT NOT NULL,
                result_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("UPDATE runs SET status='interrupted', updated_at=? WHERE status='running'", (_now(),))
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
            db.execute("DELETE FROM runs WHERE created_at < ?", (cutoff,))

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def start(self, run_id: str, question: str) -> None:
        with self._connection() as db:
            db.execute("INSERT INTO runs(run_id,created_at,updated_at,status,question_hash,question_redacted) VALUES(?,?,?,?,?,?)",
                       (run_id, _now(), _now(), "running", hashlib.sha256(question.encode()).hexdigest(), _redact(question)))

    def finish(self, run_id: str, result: dict) -> None:
        with self._connection() as db:
            db.execute("UPDATE runs SET status=?,updated_at=?,result_json=? WHERE run_id=?",
                       (result.get("status", "failed"), _now(), json.dumps(result, ensure_ascii=False), run_id))

    def get(self, run_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT run_id,created_at,updated_at,status,question_hash,question_redacted,result_json,cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "created_at": row[1], "updated_at": row[2], "status": row[3],
                "question_hash": row[4], "question_redacted": row[5],
                "result": json.loads(row[6]) if row[6] else None,
                "cancel_requested": bool(row[7])}

    def request_cancel(self, run_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute("UPDATE runs SET cancel_requested=1,updated_at=? WHERE run_id=? AND status='running'", (_now(), run_id))
            return cursor.rowcount == 1
