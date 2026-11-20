"""User-encrypted journey drafts and durable one-shot submission claims."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ..core.journey import BookingIntent, Journey
from ..security import dpapi


class JourneyStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS draft (id INTEGER PRIMARY KEY, rev INTEGER, body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS active (id INTEGER PRIMARY KEY, token TEXT, body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS sent (fingerprint TEXT PRIMARY KEY, token TEXT UNIQUE)")
        return db

    @staticmethod
    def _encode(obj) -> str:
        return dpapi.protect_text(json.dumps(asdict(obj), ensure_ascii=False))

    @staticmethod
    def _decode(cipher: str) -> dict:
        return json.loads(dpapi.unprotect_text(cipher))

    def load(self) -> tuple[int, Journey]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT rev, body FROM draft WHERE id=1").fetchone()
        return (row[0], Journey.from_dict(self._decode(row[1]))) if row else (0, Journey())

    def save(self, journey: Journey, revision: int) -> int:
        cipher = self._encode(journey)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT rev FROM draft WHERE id=1").fetchone()
            if (row[0] if row else 0) != revision:
                raise ValueError("另一窗口已保存此行程，请重新载入。")
            db.execute("INSERT OR REPLACE INTO draft VALUES (1, ?, ?)", (revision + 1, cipher))
        return revision + 1

    def begin(self, intent: BookingIntent) -> str:
        token = uuid4().hex
        with closing(self._connect()) as db, db:
            try:
                db.execute("INSERT INTO active VALUES (1, ?, ?)", (token, self._encode(intent)))
            except sqlite3.IntegrityError as exc:
                raise ValueError("还有一笔行程待核实，请先核对订单。") from exc
        return token

    def current(self) -> tuple[str, BookingIntent] | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT token, body FROM active WHERE id=1").fetchone()
        return (row[0], BookingIntent.from_dict(self._decode(row[1]))) if row else None

    def claim(self, token: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM active WHERE id=1 AND token=?", (token,)).fetchone()
            if not row:
                raise ValueError("行程已变更，请重新核对。")
            intent = BookingIntent.from_dict(self._decode(row[0]))
            try:
                db.execute("INSERT INTO sent VALUES (?, ?)", (intent.fingerprint, token))
            except sqlite3.IntegrityError as exc:
                raise ValueError("此行程已发起过一次提交，请核对官方订单。") from exc

    def finish(self, token: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM active WHERE id=1 AND token=?", (token,))
