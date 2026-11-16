"""Typed domain models shared by core modules."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

TicketType = Literal["adult", "child", "student"]
SubmitStatus = Literal["success", "queued", "sold_out", "unknown", "risk", "login_expired", "manual"]
OrderStatus = Literal["unpaid", "paid", "cancelled", "unknown"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Passenger:
    name: str
    type: TicketType = "adult"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("passenger name must not be empty")
        if self.type not in ("adult", "child", "student"):
            raise ValueError(f"unsupported ticket type: {self.type}")


@dataclass(frozen=True)
class Candidate:
    date: str
    train_no: str
    from_station: str
    to_station: str
    seat: str
    priority: int = 100
    seat_priority: int = 0
    depart_time: str = ""
    arrive_time: str = ""
    duration: str = ""
    available: str = ""

    @property
    def key(self) -> str:
        return f"{self.date}|{self.train_no}|{self.from_station}|{self.to_station}|{self.seat}"


@dataclass
class Task:
    id: str
    name: str
    dates: list[str]
    from_station: str
    to_station: str
    candidates: list[Candidate]
    passengers: list[Passenger]
    seat_preference: str = ""
    berth_preference: str = ""
    ticket_type: TicketType = "adult"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("task id must not be empty")
        if not self.dates:
            raise ValueError("task must have at least one date")
        if not self.passengers:
            raise ValueError("task must have at least one passenger")
        if len(self.passengers) > 19:
            raise ValueError("a single order supports at most 19 passengers")
        if not self.candidates:
            raise ValueError("task must have at least one candidate")
        date_set = set(self.dates)
        for candidate in self.candidates:
            if candidate.date not in date_set:
                raise ValueError(f"candidate date {candidate.date} is not in task dates")

    @classmethod
    def from_config(cls, raw: dict, index: int = 0) -> "Task":
        task_id = str(raw.get("id") or f"task-{index + 1}")
        dates = [str(d) for d in raw.get("dates", [])]
        passengers = [
            Passenger(str(p["name"]), str(p.get("type", "adult")))  # type: ignore[arg-type]
            if isinstance(p, dict) else Passenger(str(p))
            for p in raw.get("passengers", [])
        ]
        candidates: list[Candidate] = []
        for item in raw.get("candidates", []):
            train_no = str(item["train_no"])
            priority = int(item.get("priority", 100))
            for seat_index, seat in enumerate(item.get("seats", [])):
                for date in dates:
                    candidates.append(
                        Candidate(
                            date=date,
                            train_no=train_no,
                            from_station=str(raw["from_station"]),
                            to_station=str(raw["to_station"]),
                            seat=str(seat),
                            priority=priority,
                            seat_priority=seat_index,
                        )
                    )
        return cls(
            id=task_id,
            name=str(raw.get("name") or task_id),
            dates=dates,
            from_station=str(raw["from_station"]),
            to_station=str(raw["to_station"]),
            candidates=candidates,
            passengers=passengers,
            seat_preference=str(raw.get("seat_preference") or ""),
            berth_preference=str(raw.get("berth_preference") or ""),
            ticket_type=str(raw.get("ticket_type") or "adult"),  # type: ignore[arg-type]
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass(frozen=True)
class SubmitResult:
    status: SubmitStatus
    order_no: str | None = None
    message: str = ""
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("success", "queued", "manual")


@dataclass(frozen=True)
class Order:
    masked_no: str
    status: OrderStatus
    amount: float | None = None
    pay_deadline: str | None = None  # ISO8601
    train_no: str | None = None
    date: str | None = None


@dataclass(frozen=True)
class WaitlistCombo:
    date: str
    train_no: str
    seat: str
    priority: int = 100


@dataclass
class WaitlistOrder:
    index: int
    dates: list[str]
    combos: list[WaitlistCombo]
    passengers: list[str]


@dataclass
class WaitlistPlan:
    orders: list[WaitlistOrder]
    total_combos: int
    cutoff_at: str
    accept_new_trains: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


@dataclass(frozen=True)
class Event:
    ts: str
    task_id: str
    code: str
    message: str = ""
    latency_ms: int | None = None
    meta: dict | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("event code must not be empty")
