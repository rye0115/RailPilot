from railpilot.models import Event
from railpilot.store.events import EventStore


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
