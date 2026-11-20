from railpilot.models import Event
from railpilot.store.events import EventStore
import sqlite3


def test_append_timeline_redacts(tmp_path):
    store = EventStore(tmp_path / "events.db")
    try:
        store.append(
            Event(
                ts="2026-01-01T00:00:00+00:00",
                task_id="t1",
                code="TEST",
                message="Cookie: tk=secret 身份证 110101199001011234",
                meta={"phone": "13800138000"},
            )
        )
        rows = store.timeline("t1")
        assert len(rows) == 1
        assert "tk=secret" not in rows[0].message
        assert "110101199001011234" not in rows[0].message
        assert "13800138000" not in str(rows[0].meta)
        assert store.count("t1") == 1
    finally:
        store.close()


def test_structured_redaction_and_recent_events(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    try:
        for i in range(3):
            store.append(Event("now", "ui", str(i), "11010119900101123X",
                               meta={"headers": [{"TOKEN": "sample-credential"}], "attempt": i}))
        rows = store.timeline("ui", limit=2)
        assert [e.code for e in rows] == ["1", "2"]
        assert rows[-1].meta == {"headers": [{"TOKEN": "<redacted>"}], "attempt": 2}
        with sqlite3.connect(path) as conn:
            message, meta = conn.execute("SELECT message, meta_json FROM events LIMIT 1").fetchone()
        assert message == "110101********123X"
        assert "sample-credential" not in meta
    finally:
        store.close()
