import os
from pathlib import Path

from railpilot.config import ConfigError, data_dir, events_path, state_path
import pytest
from railpilot.security.files import app_data_dir


def test_configured_e_drive_path(monkeypatch):
    monkeypatch.delenv("RAILPILOT_DATA_DIR", raising=False)
    assert data_dir({"app": {"data_dir": "E:\\RailPilot\\data"}}) == Path("E:\\RailPilot\\data")


def test_empty_uses_default():
    assert data_dir({}) == app_data_dir()


def test_legacy_localappdata_maps_to_default():
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "RailPilot"
    if not legacy.parts:
        return
    assert data_dir({"app": {"data_dir": str(legacy)}}) == app_data_dir()


def test_env_overrides_config_for_all_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILPILOT_DATA_DIR", str(tmp_path))
    config = {"app": {"data_dir": "E:/RailPilot/data"}}
    assert events_path(config) == tmp_path / "events.db"
    assert state_path(config) == tmp_path / "state.enc"


def test_state_file_must_be_in_the_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILPILOT_DATA_DIR", str(tmp_path / "app"))
    with pytest.raises(ConfigError):
        state_path({"security": {"state_file": str(tmp_path / "other.enc")}})
