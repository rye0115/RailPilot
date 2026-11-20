import pytest

from railpilot.core.waitlist import WaitlistError, build_plan, validate_plan
from railpilot.models import Candidate, Passenger


def _candidate(date, train, seat, priority=100, arrive="10:00"):
    return Candidate(
        date=date,
        train_no=train,
        from_station="北京南",
        to_station="上海虹桥",
        seat=seat,
        priority=priority,
        arrive_time=arrive,
    )


def test_three_dates_and_dedup():
    candidates = [
        _candidate("2027-02-10", "G1", "二等座"),
        _candidate("2027-02-10", "G1", "二等座"),
        _candidate("2027-02-10", "G1", "一等座"),
        _candidate("2027-02-11", "G3", "二等座"),
        _candidate("2027-02-12", "G5", "二等座"),
    ]
    plan = build_plan(candidates, [Passenger("张三")], "2027-02-12T18:00:00+08:00")
    assert plan.total_combos == 4
    assert len(plan.orders) == 1
    assert len(plan.orders[0].dates) == 3
    validate_plan(plan)


def test_more_than_three_dates_splits_orders():
    candidates = [
        _candidate(f"2027-02-{day:02d}", "G1", "二等座") for day in range(10, 15)
    ]
    plan = build_plan(candidates, [Passenger("张三")], "2027-02-14T18:00:00+08:00")
    assert len(plan.orders) >= 2
    for order in plan.orders:
        assert len(order.dates) <= 3
    validate_plan(plan)


def test_combos_chunk_at_sixty():
    seats = [f"S{i}" for i in range(70)]
    candidates = [_candidate("2027-02-10", "G1", seat) for seat in seats]
    plan = build_plan(candidates, [Passenger("张三")], "2027-02-10T18:00:00+08:00")
    assert [len(order.combos) for order in plan.orders] == [60, 10]
    validate_plan(plan)


def test_passenger_split():
    passengers = [Passenger(f"P{i}") for i in range(25)]
    candidates = [_candidate("2027-02-10", "G1", "二等座")]
    plan = build_plan(candidates, passengers, "2027-02-10T18:00:00+08:00")
    assert [len(order.passengers) for order in plan.orders] == [19, 6]
    validate_plan(plan)


def test_latest_arrival_filter():
    candidates = [
        _candidate("2027-02-10", "G1", "二等座", arrive="23:50"),
        _candidate("2027-02-10", "G2", "二等座", arrive="18:00"),
    ]
    plan = build_plan(
        candidates,
        [Passenger("张三")],
        "2027-02-10T18:00:00+08:00",
        latest_arrival="22:00",
    )
    assert plan.total_combos == 1
    assert plan.orders[0].combos[0].train_no == "G2"


def test_no_feasible_candidates():
    with pytest.raises(WaitlistError):
        build_plan([], [Passenger("张三")], "2027-02-10T18:00:00+08:00")


@pytest.mark.parametrize("cutoff", ["abc", "2027-02-10", "2027-02-10T18:00:00"])
def test_rejects_invalid_cutoff(cutoff):
    with pytest.raises(WaitlistError, match="截止时间"):
        build_plan([_candidate("2027-02-10", "G1", "二等座")], [Passenger("P")], cutoff)


def test_reports_only_actual_overflow():
    candidates = [_candidate("2027-02-10", f"G{i}", "二等座") for i in range(361)]
    args = ([Passenger("P")], "2027-02-10T12:00:00+08:00")
    assert build_plan(candidates[:360], *args).warnings == []
    plan = build_plan(candidates, *args)
    assert plan.total_combos == 360
    assert "1 个" in plan.warnings[0]
