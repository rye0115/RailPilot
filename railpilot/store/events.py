"""SQLite event log without PII."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..models import Event
from ..security.redact import redact_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  task_id TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  latency_ms INTEGER,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task_ts ON events(task_id, ts);
"""


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append(self, event: Event) -> None:
        meta = json.dumps(event.meta or {}, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            "INSERT INTO events(ts, task_id, code, message, latency_ms, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.ts,
                event.task_id,
                event.code,
                redact_text(event.message),
                event.latency_ms,
                redact_text(meta),
            ),
        )
        self._conn.commit()

    def timeline(self, task_id: str, limit: int = 100) -> list[Event]:
        rows = self._conn.execute(
            "SELECT ts, task_id, code, message, latency_ms, meta_json "
            "FROM events WHERE task_id = ? ORDER BY id ASC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        result: list[Event] = []
        for ts, tid, code, message, latency_ms, meta_json in rows:
            try:
                meta = json.loads(meta_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            result.append(
                Event(
                    ts=ts,
                    task_id=tid,
                    code=code,
                    message=message,
                    latency_ms=latency_ms,
                    meta=meta,
                )
            )
        return result

    def count(self, task_id: str | None = None) -> int:
        if task_id is None:
            row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE task_id = ?", (task_id,)
            ).fetchone()
        return int(row[0]) if row else 0
