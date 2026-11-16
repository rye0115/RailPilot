import sys

import pytest

from railpilot.security import dpapi
from railpilot.store.state import StateSecurityError, StateStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def test_state_roundtrip_and_ciphertext(tmp_path):
    path = tmp_path / "state.enc"
    store = StateStore(path)
    state = {"version": 1, "tasks": [{"name": "张三"}]}
    store.save(state)
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith(dpapi.PREFIX)
    assert "张三" not in raw
    assert store.load() == state


def test_refuses_plaintext(tmp_path):
    path = tmp_path / "state.enc"
    path.write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(StateSecurityError):
        StateStore(path).load()


def test_clear(tmp_path):
    path = tmp_path / "state.enc"
    store = StateStore(path)
    store.save({"version": 1})
    assert path.exists()
    store.clear()
    assert not path.exists()
