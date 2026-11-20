"""Match a traveller's choices against live inventory and newly observed orders."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from ..models import Candidate, Order, OrderTicket
from .planner import parse_hhmm

CN = timezone(timedelta(hours=8))


def passenger_names(text: str) -> list[str]:
    names = [name.strip() for name in re.split(r"[,，、;；\n]", text) if name.strip()]
    if not names or len(names) > 19 or len(set(names)) != len(names):
        raise ValueError("请填写 1 至 19 个不同的乘车人姓名，以逗号分隔。")
    return names


@dataclass
class Journey:
    choices: list[Candidate] = field(default_factory=list)
    passengers: list[str] = field(default_factory=list)
    latest: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Journey:
        return cls([Candidate(**row) for row in data.get("choices", [])],
                   list(data.get("passengers", [])), data.get("latest", ""))

    def add(self, candidate: Candidate) -> None:
        if self.choices:
            first = self.choices[0]
            if (first.from_station, first.to_station) != (candidate.from_station, candidate.to_station):
                raise ValueError("同一行程的出发站和到达站须一致。")
        if candidate.key not in {c.key for c in self.choices}:
            self.choices.append(candidate)

    def availability(self, candidate: Candidate, live: Candidate | None) -> tuple[bool, str]:
        if live is None or live.key != candidate.key:
            return False, "尚未查到此席别的余票"
        if self.latest:
            limit = parse_hhmm(self.latest)
            if limit is None:
                return False, "请按 HH:MM 填写最晚到达时间"
            arrival = parse_hhmm(live.arrive_time)
            departure = parse_hhmm(live.depart_time)
            if arrival is None or departure is None or not re.fullmatch(r"\d+:\d{2}", live.duration):
                return False, "到达时间待核实"
            hours, minutes = map(int, live.duration.split(":"))
            if minutes >= 60 or departure + hours * 60 + minutes >= 1440 or arrival > limit:
                return False, "超出出发当日的到达时限"
        count = len(self.passengers)
        if not count:
            return False, "请填写乘车人"
        if live.available == "有":
            return True, "有票，具体张数以官方确认为准"
        if live.available.isdecimal():
            tickets = int(live.available)
            return tickets >= count, f"余 {tickets} 张，需 {count} 张"
        return False, f"官方显示：{live.available or '待核实'}"


@dataclass(frozen=True)
class BookingIntent:
    candidate: Candidate
    passengers: tuple[str, ...]
    before: tuple[str, ...]
    started: str

    @classmethod
    def prepare(cls, candidate: Candidate, passengers: list[str], orders: list[Order]) -> BookingIntent:
        if not passengers or len(set(passengers)) != len(passengers) or any(not p.strip() for p in passengers):
            raise ValueError("请选择可唯一识别的乘车人。")
        if any(not order.identity for order in orders):
            raise ValueError("现有订单无法可靠识别，请先到 12306 核对。")
        return cls(candidate, tuple(sorted(passengers)), tuple(sorted(o.identity for o in orders)),
                   datetime.now(CN).isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, row: dict) -> BookingIntent:
        return cls(Candidate(**row["candidate"]), tuple(row["passengers"]), tuple(row["before"]), row["started"])

    @property
    def fingerprint(self) -> str:
        raw = json.dumps([self.candidate.key, self.passengers], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def match(self, orders: list[Order]) -> Order | None:
        c = self.candidate
        wanted = Counter(OrderTicket(c.date, c.train_no, c.from_station, c.to_station, c.seat, p)
                         for p in self.passengers)
        started = datetime.fromisoformat(self.started)
        matches = [o for o in orders if o.status == "unpaid" and o.identity and o.identity not in self.before
                   and o.placed_at and datetime.fromisoformat(o.placed_at) >= started and Counter(o.tickets) == wanted]
        return matches[0] if len(matches) == 1 else None


def payment_deadline(value: object) -> str | None:
    if not isinstance(value, str) or len(value.strip()) < 16:
        return None
    try:
        stamp = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=CN)).astimezone(CN).isoformat(timespec="seconds")
