"""Redaction helpers for logs, notifications and diagnostics.

The rule is simple: never log a cookie, token, full ID number, phone number or
full order number. Keep enough context to debug, no more.
"""

from __future__ import annotations

import logging
import re

_COOKIE = re.compile(r"(?i)(\bcookie\b\s*[:=]\s*)[^\r\n]+")
_TOKEN = re.compile(
    r"(?i)(\b(?:tk|uamtk|RAIL_DEVICEID|RAIL_EXPIRATION|token|sessionid|authorization)\b"
    r"\s*[:=]\s*)[\"']?[^\"'\s;,]+"
)
_MAINLAND_ID = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{4})(?!\d)")
_PHONE = re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)")
_ORDER_NO = re.compile(r"\b([Ee])\d{8,}\b")


def mask_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    if len(name) == 1:
        return name + "*"
    return name[0] + "*" * (len(name) - 1)


def mask_order_no(order_no: str | None) -> str:
    value = (order_no or "").strip()
    if not value:
        return ""
    if len(value) <= 5:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 6) + value[-4:]


def redact_text(value: object) -> str:
    text = str(value)
    text = _COOKIE.sub(lambda m: f"{m.group(1)}<redacted>", text)
    text = _TOKEN.sub(lambda m: f"{m.group(1)}<redacted>", text)
    text = _MAINLAND_ID.sub(lambda m: f"{m.group(1)}{'*' * 8}{m.group(2)}", text)
    text = _PHONE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", text)
    text = _ORDER_NO.sub(lambda m: f"{m.group(1)}{'*' * 8}{m.group(0)[-4:]}", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def safe_exception(exc: BaseException) -> str:
    """Return a redacted exception string safe for logs/UI."""
    return f"{type(exc).__name__}: {redact_text(exc)}"
