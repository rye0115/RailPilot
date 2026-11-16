"""Payment and cancellation guard.

Hard rules:
- at most one unpaid order at a time;
- at most two manual cancellations per day (official limit is three; keep one
  spare for mistakes);
- never submit the same fingerprint twice;
- never retry blindly after an ambiguous result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable

from ..models import Decision, Order

# China Standard Time is UTC+8 year-round; avoid a tzdata dependency on Windows.
CN = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class PaymentReminder:
    masked_no: str
    deadline: str
    minutes_left: int
    threshold: int


def _as_cn(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CN)


def _parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN)
    return parsed.astimezone(timezone.utc)


class Guard:
    def __init__(
        self,
        *,
        max_manual_cancels_per_day: int = 2,
        max_unpaid_orders: int = 1,
        state: dict | None = None,
    ) -> None:
        if max_manual_cancels_per_day < 0:
            raise ValueError("max_manual_cancels_per_day must be >= 0")
        if max_unpaid_orders < 1:
            raise ValueError("max_unpaid_orders must be >= 1")
        self.max_cancels = max_manual_cancels_per_day
        self.max_unpaid = max_unpaid_orders
        self.state = state or {}
        self.state.setdefault("day", "")
        self.state.setdefault("manual_cancels", 0)
        self.state.setdefault("unpaid", [])
        self.state.setdefault("seen", [])
        self.state.setdefault("reminded", [])

    def _roll(self, now: datetime | None = None) -> str:
        day = _as_cn(now).date().isoformat()
        if self.state["day"] != day:
            self.state["day"] = day
            self.state["manual_cancels"] = 0
            self.state["reminded"] = []
        return day

    def before_submit(
        self,
        fingerprint: str,
        unpaid_orders: Iterable[Order] = (),
        now: datetime | None = None,
    ) -> Decision:
        self._roll(now)
        unpaid = list(unpaid_orders) + list(self.state["unpaid"])
        if len(unpaid) >= self.max_unpaid:
            return Decision(False, "unpaid order exists; pay or cancel it first")
        if self.state["manual_cancels"] >= self.max_cancels:
            return Decision(False, "daily manual-cancel budget exhausted")
        if fingerprint in self.state["seen"]:
            return Decision(False, "duplicate fingerprint; refusing to submit again")
        self.state["seen"].append(fingerprint)
        return Decision(True, "ok")

    def record_cancel(self, now: datetime | None = None) -> bool:
        self._roll(now)
        if self.state["manual_cancels"] >= self.max_cancels:
            return False
        self.state["manual_cancels"] += 1
        return True

    def can_cancel(self, now: datetime | None = None) -> bool:
        self._roll(now)
        return self.state["manual_cancels"] < self.max_cancels

    def add_unpaid(self, order: Order) -> None:
        if not order.masked_no:
            raise ValueError("order must have masked_no")
        if any(item["masked_no"] == order.masked_no for item in self.state["unpaid"]):
            return
        if len(self.state["unpaid"]) >= self.max_unpaid:
            raise ValueError("unpaid order limit reached")
        self.state["unpaid"].append(
            {
                "masked_no": order.masked_no,
                "deadline": order.pay_deadline or "",
                "status": order.status,
            }
        )

    def mark_paid(self, masked_no: str) -> None:
        self.state["unpaid"] = [
            item for item in self.state["unpaid"] if item["masked_no"] != masked_no
        ]

    def payment_reminders(
        self,
        now: datetime | None = None,
        thresholds: tuple[int, ...] = (10, 5, 2),
    ) -> list[PaymentReminder]:
        current = datetime.now(timezone.utc) if now is None else now
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        reminders: list[PaymentReminder] = []
        for item in list(self.state["unpaid"]):
            deadline = _parse_deadline(item.get("deadline"))
            if deadline is None:
                continue
            minutes_left = int((deadline - current).total_seconds() // 60)
            for threshold in sorted(thresholds, reverse=True):
                key = f"{item['masked_no']}:{threshold}"
                if minutes_left <= threshold and key not in self.state["reminded"]:
                    self.state["reminded"].append(key)
                    reminders.append(
                        PaymentReminder(
                            masked_no=item["masked_no"],
                            deadline=deadline.astimezone(CN).isoformat(timespec="minutes"),
                            minutes_left=minutes_left,
                            threshold=threshold,
                        )
                    )
                    break
        return reminders

    def snapshot(self) -> dict:
        return {
            "day": self.state["day"],
            "manual_cancels": self.state["manual_cancels"],
            "max_cancels": self.max_cancels,
            "unpaid": list(self.state["unpaid"]),
            "seen": len(self.state["seen"]),
        }
