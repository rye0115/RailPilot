from datetime import datetime, timedelta, timezone

from railpilot.core.guard import Guard
from railpilot.models import Order


def test_unpaid_and_duplicate_blocked():
    guard = Guard(max_manual_cancels_per_day=2, max_unpaid_orders=1)
    first = guard.before_submit("fp-1")
    assert first.allow
    second = guard.before_submit("fp-1")
    assert not second.allow
    third = guard.before_submit("fp-2", unpaid_orders=[Order("E1****789", "unpaid")])
    assert not third.allow


def test_cancel_budget_and_day_roll():
    day1 = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
    guard = Guard(max_manual_cancels_per_day=2)
    assert guard.record_cancel(day1)
    assert guard.record_cancel(day1)
    assert not guard.record_cancel(day1)
    assert not guard.can_cancel(day1)
    assert guard.can_cancel(day2)
    assert guard.record_cancel(day2)


def test_payment_reminders_once_per_threshold():
    now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    guard = Guard()
    guard.add_unpaid(
        Order(
            masked_no="E1****789",
            status="unpaid",
            pay_deadline=(now + timedelta(minutes=9)).isoformat(),
        )
    )
    first = guard.payment_reminders(now)
    assert len(first) == 1
    assert first[0].threshold == 10
    assert guard.payment_reminders(now) == []
    later = guard.payment_reminders(now + timedelta(minutes=5))
    assert len(later) == 1
    assert later[0].threshold == 5


def test_unpaid_limit():
    guard = Guard(max_unpaid_orders=1)
    guard.add_unpaid(Order("E1****789", "unpaid"))
    try:
        guard.add_unpaid(Order("E2****789", "unpaid"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    guard.mark_paid("E1****789")
    guard.add_unpaid(Order("E2****789", "unpaid"))
