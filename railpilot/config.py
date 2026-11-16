"""Config loading with security-invariant checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .security.files import app_data_dir

RED_LINE_KEYS = (
    "allow_multi_account",
    "allow_proxy",
    "allow_captcha_bypass",  # red-line
    "allow_anti_detect",  # red-line
    "allow_auto_pay",
    "allow_buy_short_ride_long",
)


class ConfigError(ValueError):
    pass


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def load_config(path: str | Path) -> dict:
    raw_path = Path(path)
    if not raw_path.exists():
        raise ConfigError(f"config not found: {raw_path}")
    with raw_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    data = _expand(data)

    security = data.setdefault("security", {})
    for key in RED_LINE_KEYS:
        if security.get(key) is True:
            raise ConfigError(f"red-line setting must stay false: {key}")
    if security.get("dpapi_scope", "user") != "user":
        raise ConfigError("dpapi_scope must be 'user'")
    if security.get("block_third_party", True) is not True:
        raise ConfigError("block_third_party must stay true")
    if security.get("log_redaction", True) is not True:
        raise ConfigError("log_redaction must stay true")

    account = data.setdefault("account", {})
    if account.get("login_mode", "qr") != "qr":
        raise ConfigError("only QR login is supported; password login is not allowed")
    if account.get("password") or account.get("passwd"):
        raise ConfigError("passwords must not be stored in config")

    return data


def data_dir(config: dict) -> Path:
    configured = str(config.get("app", {}).get("data_dir") or "").strip()
    if not configured:
        return app_data_dir()
    path = Path(configured).expanduser()
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "RailPilot"
    # Migrate old configs that explicitly point at the pre-E-drive location.
    if legacy.parts and path == legacy:
        return app_data_dir()
    return path


def state_path(config: dict) -> Path:
    return data_dir(config) / str(config.get("security", {}).get("state_file", "state.enc"))


def events_path(config: dict) -> Path:
    return data_dir(config) / "events.db"


def task_configs(config: dict) -> list[dict]:
    tasks = config.get("tasks", [])
    if not isinstance(tasks, list):
        raise ConfigError("tasks must be a list")
    return tasks
