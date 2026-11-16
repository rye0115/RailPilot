"""Waitlist plan builder (official limits: 6 orders x 3 dates x 60 combos x 19 people)."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Candidate, Passenger, WaitlistCombo, WaitlistOrder, WaitlistPlan
from .planner import feasible


class WaitlistError(ValueError):
    pass


def _chunks(items: list, size: int) -> Iterable[list]:
    if size <= 0:
        raise WaitlistError("chunk size must be positive")
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        if candidate.key in seen:
            continue
        seen.add(candidate.key)
        result.append(candidate)
    return result


def build_plan(
    candidates: Iterable[Candidate],
    passengers: list[Passenger],
    cutoff_at: str,
    *,
    accept_new_trains: bool = True,
    max_orders: int = 6,
    max_dates_per_order: int = 3,
    max_combos_per_order: int = 60,
    max_passengers_per_order: int = 19,
    latest_arrival: str = "",
) -> WaitlistPlan:
    if not passengers:
        raise WaitlistError("at least one passenger is required")
    if not cutoff_at:
        raise WaitlistError("cutoff_at is required")
    if max_orders < 1 or max_dates_per_order < 1 or max_combos_per_order < 1:
        raise WaitlistError("limits must be positive")

    clean = [
        c for c in _dedupe(candidates)
        if feasible(c, latest_arrival=latest_arrival)
    ]
    if not clean:
        raise WaitlistError("no feasible candidates")

    clean.sort(
        key=lambda c: (
            c.priority,
            c.seat_priority,
            c.date,
            c.depart_time or "99:99",
            c.train_no,
            c.seat,
        )
    )
    dates = sorted({c.date for c in clean})
    passenger_groups = list(_chunks(passengers, max_passengers_per_order))

    orders: list[WaitlistOrder] = []
    warnings: list[str] = []
    for date_group in _chunks(dates, max_dates_per_order):
        date_set = set(date_group)
        group = [c for c in clean if c.date in date_set]
        for combo_group in _chunks(group, max_combos_per_order):
            for pax_group in passenger_groups:
                if len(orders) >= max_orders:
                    warnings.append("order limit reached; lower-priority combinations were dropped")
                    break
                orders.append(
                    WaitlistOrder(
                        index=len(orders) + 1,
                        dates=sorted({c.date for c in combo_group}),
                        combos=[
                            WaitlistCombo(
                                date=c.date,
                                train_no=c.train_no,
                                seat=c.seat,
                                priority=c.priority,
                            )
                            for c in combo_group
                        ],
                        passengers=[p.name for p in pax_group],
                    )
                )
            if len(orders) >= max_orders:
                break
        if len(orders) >= max_orders:
            break

    if not orders:
        raise WaitlistError("no orders could be built from candidates")

    plan = WaitlistPlan(
        orders=orders,
        total_combos=sum(len(order.combos) for order in orders),
        cutoff_at=cutoff_at,
        accept_new_trains=accept_new_trains,
        warnings=warnings,
    )
    validate_plan(plan, max_orders, max_dates_per_order, max_combos_per_order, max_passengers_per_order)
    return plan


def validate_plan(
    plan: WaitlistPlan,
    max_orders: int = 6,
    max_dates_per_order: int = 3,
    max_combos_per_order: int = 60,
    max_passengers_per_order: int = 19,
) -> None:
    if not plan.orders:
        raise WaitlistError("plan has no orders")
    if len(plan.orders) > max_orders:
        raise WaitlistError(f"too many orders: {len(plan.orders)} > {max_orders}")
    for order in plan.orders:
        if len(order.dates) > max_dates_per_order:
            raise WaitlistError(f"order {order.index}: too many dates")
        if len(order.combos) > max_combos_per_order:
            raise WaitlistError(f"order {order.index}: too many combos")
        if len(order.passengers) > max_passengers_per_order:
            raise WaitlistError(f"order {order.index}: too many passengers")
        if not order.combos or not order.passengers:
            raise WaitlistError(f"order {order.index}: empty combos/passengers")
        combo_dates = {combo.date for combo in order.combos}
        if not combo_dates.issubset(set(order.dates)):
            raise WaitlistError(f"order {order.index}: combo date not listed")
