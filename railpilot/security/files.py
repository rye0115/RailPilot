"""Local file helpers: private data dir, atomic writes, best-effort delete."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import uuid
from pathlib import Path

APP_DIR_NAME = "RailPilot"
DEFAULT_WINDOWS_DRIVE = Path("E:/")


def _data_root() -> Path:
    override = os.environ.get("RAILPILOT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        if DEFAULT_WINDOWS_DRIVE.exists():
            return DEFAULT_WINDOWS_DRIVE / APP_DIR_NAME / "data"
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DIR_NAME


def app_data_dir() -> Path:
    path = _data_root()
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        if not path.is_dir():
            raise
        return path
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    restrict_acl(path)
    return path


def restrict_acl(path: Path) -> None:
    """Best-effort Windows ACL: current user only.

    This is defense in depth; DPAPI is the primary control. Failures are
    non-fatal because some filesystems/containers do not expose icacls.
    """
    if sys.platform != "win32":
        return
    user = os.environ.get("USERNAME") or getpass.getuser()
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        restrict_acl(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def secure_delete(path: Path) -> None:
    """Best-effort overwrite then unlink.

    On SSDs/CoW filesystems this is not a guarantee; use full-disk encryption
    for the real protection.
    """
    path = Path(path)
    if not path.exists():
        return
    try:
        size = path.stat().st_size
        if size > 0:
            with path.open("r+b") as handle:
                handle.write(os.urandom(size))
                handle.flush()
                os.fsync(handle.fileno())
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass
