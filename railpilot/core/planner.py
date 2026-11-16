"""Candidate ranking and basic feasibility checks."""

from __future__ import annotations

import hashlib
from datetime import datetime

from ..models import Candidate, Passenger, Task


def parse_hhmm(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def feasible(candidate: Candidate, latest_arrival: str = "", min_transfer_buffer_min: int = 30) -> bool:
    if latest_arrival:
        arrive = parse_hhmm(candidate.arrive_time)
        latest = parse_hhmm(latest_arrival)
        if arrive is not None and latest is not None and arrive > latest:
            return False
    # Transfer feasibility is handled by the planner when it builds transfer
    # options; a direct candidate has no transfer buffer to check.
    _ = min_transfer_buffer_min
    return True


def rank_candidates(task: Task, latest_arrival: str = "") -> list[Candidate]:
    candidates = [c for c in task.candidates if feasible(c, latest_arrival=latest_arrival)]
    return sorted(
        candidates,
        key=lambda c: (
            c.priority,
            c.seat_priority,
            c.date,
            c.depart_time or "99:99",
            c.train_no,
            c.seat,
        ),
    )


def fingerprint(task: Task, candidate: Candidate, passengers: list[Passenger] | None = None) -> str:
    people = passengers if passengers is not None else task.passengers
    raw = "|".join(
        [
            task.id,
            candidate.date,
            candidate.train_no,
            candidate.from_station,
            candidate.to_station,
            candidate.seat,
            ",".join(sorted(p.name for p in people)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
