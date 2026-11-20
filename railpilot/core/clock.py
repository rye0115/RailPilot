"""Server clock calibration using HTTP Date + monotonic clock.

HTTP Date has one-second resolution; this module aims for a stable offset, not
millisecond truth. It never contacts anything except the configured 12306 URL.
"""

from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.request import Request, urlopen

from ..security.allowlist import assert_allowed


@dataclass(frozen=True)
class ClockState:
    offset_ms: int
    measured_at: str
    samples: int
    min_rtt_ms: float


def _fetch_http_date(url: str, timeout: float = 5.0) -> tuple[float, float]:
    """Return (server_epoch_seconds, rtt_seconds) from the Date header."""
    assert_allowed(url)
    context = ssl.create_default_context()
    started = time.monotonic()
    request = Request(url, method="HEAD", headers={"User-Agent": "RailPilot/0.1"})
    try:
        with urlopen(request, timeout=timeout, context=context) as response:  # nosec B310: URL is assert_allowed()
            date_header = response.headers.get("Date")
    except Exception:
        started = time.monotonic()
        request = Request(url, method="GET", headers={"User-Agent": "RailPilot/0.1"})
        with urlopen(request, timeout=timeout, context=context) as response:  # nosec B310: URL is assert_allowed()
            date_header = response.headers.get("Date")
    elapsed = time.monotonic() - started
    if not date_header:
        raise ValueError("server response has no Date header")
    parsed = parsedate_to_datetime(date_header)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp(), elapsed


def calibrate(
    url: str = "https://www.12306.cn",
    samples: int = 5,
    timeout: float = 5.0,
    fetcher: Callable[[str, float], tuple[float, float]] | None = None,
) -> ClockState:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    fetch = fetcher or _fetch_http_date
    best: tuple[float, float] | None = None
    for _ in range(samples):
        server_epoch, rtt = fetch(url, timeout)
        offset = server_epoch + rtt / 2 - time.time()
        if best is None or rtt < best[1]:
            best = (offset, rtt)
    assert best is not None
    offset_ms = int(round(best[0] * 1000))
    return ClockState(
        offset_ms=offset_ms,
        measured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        samples=samples,
        min_rtt_ms=round(best[1] * 1000, 1),
    )


class Clock:
    """Monotonic countdown clock anchored at calibration time."""

    def __init__(
        self,
        state: ClockState,
        base_mono: float | None = None,
        base_wall: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.state = state
        self._monotonic = monotonic
        self._wall = wall
        self._base_mono = monotonic() if base_mono is None else base_mono
        self._base_wall = wall() if base_wall is None else base_wall

    def now_ms(self) -> int:
        elapsed = self._monotonic() - self._base_mono
        return int(round((self._base_wall + self.state.offset_ms / 1000 + elapsed) * 1000))

    def remaining_ms(self, target_ms: int) -> int:
        return target_ms - self.now_ms()

    def sleep_until(self, target_ms: int, spin_ms: int = 15) -> None:
        while True:
            remaining = self.remaining_ms(target_ms)
            if remaining <= 0:
                return
            if remaining > spin_ms:
                time.sleep(min((remaining - spin_ms) / 1000, 0.5))
            else:
                time.sleep(0)
