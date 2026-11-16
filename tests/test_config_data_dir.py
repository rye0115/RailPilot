import os
from pathlib import Path

from railpilot.config import data_dir
from railpilot.security.files import app_data_dir


def test_configured_e_drive_path():
    assert data_dir({"app": {"data_dir": "E:\\RailPilot\\data"}}) == Path("E:\\RailPilot\\data")


def test_empty_uses_default():
    assert data_dir({}) == app_data_dir()


def test_legacy_localappdata_maps_to_default():
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "RailPilot"
    if not legacy.parts:
        return
    assert data_dir({"app": {"data_dir": str(legacy)}}) == app_data_dir()
