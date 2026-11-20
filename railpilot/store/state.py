"""DPAPI-encrypted JSON state store.

Plaintext state files are refused on read. There is no fallback: a user who
copies a state file to another Windows account must not be able to decrypt it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..security import dpapi
from ..security.files import atomic_write_text


class StateSecurityError(RuntimeError):
    pass


def default_state() -> dict:
    return {
        "version": 1,
        "tasks": [],
        "preferences": {},
        "session": None,
    }


class StateStore:
    def __init__(
        self,
        path: str | Path,
        protect: Callable[[str], str] = dpapi.protect_text,
        unprotect: Callable[[str], str] = dpapi.unprotect_text,
    ) -> None:
        self.path = Path(path)
        self._protect = protect
        self._unprotect = unprotect

    def load(self) -> dict:
        if not self.path.exists():
            return default_state()
        text = self.path.read_text(encoding="utf-8")
        if not text.startswith(dpapi.PREFIX):
            raise StateSecurityError("refusing plaintext state file")
        try:
            payload = self._unprotect(text)
            data = json.loads(payload)
        except Exception as exc:  # noqa: BLE001 - normalize for UI
            raise StateSecurityError("state decryption/parse failed") from exc
        if not isinstance(data, dict):
            raise StateSecurityError("state root must be an object")
        return data

    def save(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        protected = self._protect(payload)
        atomic_write_text(self.path, protected)

    def clear(self) -> None:
        if self.path.is_symlink():
            raise StateSecurityError("不能清理链接指向的文件")
        if not self.path.exists():
            return
        # Authenticate the encrypted JSON before removing the directory entry.
        # An unlink failure leaves the authenticated file intact for a retry.
        self.load()
        self.path.unlink(missing_ok=True)
