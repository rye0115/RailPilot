"""Notification fan-out with a PII-free payload."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..security.redact import redact_text


@dataclass(frozen=True)
class NotifyEvent:
    kind: str
    masked_order_no: str = ""
    deadline: str = ""
    status: str = ""
    next_action: str = ""

    def payload(self) -> dict:
        return {key: redact_text(value) for key, value in asdict(self).items()}


class Channel(Protocol):
    def send(self, event: NotifyEvent) -> bool: ...


class ConsoleChannel:
    def send(self, event: NotifyEvent) -> bool:
        print(f"[notify] {event.kind} order={event.masked_order_no} deadline={event.deadline} {event.next_action}".strip())
        return True


class WebhookChannel:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("webhook must use https")
        self.url = url
        self.timeout = timeout

    def send(self, event: NotifyEvent) -> bool:
        body = json.dumps(event.payload(), ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "RailPilot/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout):  # nosec B310: webhook constructor enforces https
                return True
        except Exception:
            return False


class Fanout:
    def __init__(self, channels: list[Channel] | None = None) -> None:
        self.channels = channels or [ConsoleChannel()]

    def send(self, event: NotifyEvent) -> int:
        ok = 0
        for channel in self.channels:
            try:
                if channel.send(event):
                    ok += 1
            except Exception:
                continue
        return ok
