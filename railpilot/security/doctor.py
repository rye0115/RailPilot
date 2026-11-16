"""Local security self-checks shared by CLI and GUI."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..config import ConfigError, load_config
from ..security import dpapi
from ..security.allowlist import is_allowed_url
from ..security.files import secure_delete
from ..store.state import StateStore


def run_checks(config_path: str | Path | None = None) -> dict:
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    if config_path is not None:
        try:
            load_config(config_path)
            checks["config"] = True
        except (ConfigError, OSError) as exc:
            checks["config"] = False
            details["config"] = str(exc)

    checks["dpapi_available"] = dpapi.available()
    if checks["dpapi_available"]:
        try:
            plain = "railpilot-doctor"
            cipher = dpapi.protect_text(plain)
            recovered = dpapi.unprotect_text(cipher)
            checks["dpapi_roundtrip"] = recovered == plain
            checks["dpapi_no_plaintext"] = plain not in cipher
        except Exception as exc:  # noqa: BLE001
            checks["dpapi_roundtrip"] = False
            checks["dpapi_no_plaintext"] = False
            details["dpapi"] = type(exc).__name__

    with tempfile.TemporaryDirectory(prefix="railpilot-doctor-") as temp:
        path = Path(temp) / "state.enc"
        store = StateStore(path)
        try:
            store.save({"version": 1, "probe": "x"})
            checks["state_roundtrip"] = store.load().get("probe") == "x"
            checks["state_ciphertext"] = path.read_text(encoding="utf-8").startswith(dpapi.PREFIX)
            secure_delete(path)
            checks["state_delete"] = not path.exists()
        except Exception as exc:  # noqa: BLE001
            checks["state_roundtrip"] = False
            checks["state_ciphertext"] = False
            checks["state_delete"] = False
            details["state"] = type(exc).__name__

    checks["allowlist_12306"] = is_allowed_url("https://kyfw.12306.cn/otn/leftTicket/init")
    checks["allowlist_block_http"] = not is_allowed_url("http://kyfw.12306.cn/")
    checks["allowlist_block_third_party"] = not is_allowed_url("https://example.com/")

    return {"ok": all(checks.values()), "checks": checks, "details": details}
