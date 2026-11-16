"""Windows DPAPI (current user) helpers.

Only user-scoped protection is allowed. There is no plaintext fallback:
callers must treat a decryption failure as a hard error.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX = "dpapi1:"


class DpapiError(RuntimeError):
    pass


class DpapiUnavailable(DpapiError):
    pass


if sys.platform == "win32":
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
        if not data:
            raise DpapiError("cannot protect empty data")
        buffer = ctypes.create_string_buffer(data, len(data))
        blob = DATA_BLOB(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
        )
        return blob, buffer

    def _take(blob: DATA_BLOB) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            if blob.pbData:
                _kernel32.LocalFree(ctypes.cast(blob.pbData, wintypes.HLOCAL))

    def protect(data: bytes) -> bytes:
        blob_in, _buffer = _blob(data)
        blob_out = DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise DpapiError(f"CryptProtectData failed: {ctypes.get_last_error()}")
        return _take(blob_out)

    def unprotect(blob: bytes) -> bytes:
        if not blob:
            raise DpapiError("cannot unprotect empty blob")
        blob_in, _buffer = _blob(blob)
        blob_out = DATA_BLOB()
        description = wintypes.LPWSTR()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            ctypes.byref(description),
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise DpapiError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
        if description:
            _kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
        return _take(blob_out)
else:
    def protect(data: bytes) -> bytes:
        raise DpapiUnavailable("DPAPI is only available on Windows")

    def unprotect(blob: bytes) -> bytes:
        raise DpapiUnavailable("DPAPI is only available on Windows")


def available() -> bool:
    return sys.platform == "win32"


def protect_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    return PREFIX + base64.b64encode(protect(text.encode("utf-8"))).decode("ascii")


def unprotect_text(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise DpapiError("refusing non-DPAPI or plaintext value")
    try:
        blob = base64.b64decode(value[len(PREFIX):], validate=True)
    except Exception as exc:  # noqa: BLE001 - normalize as DpapiError
        raise DpapiError("invalid DPAPI payload") from exc
    return unprotect(blob).decode("utf-8")
