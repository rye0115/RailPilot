from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest

from railpilot.core.booking import BookingService
from railpilot.core.journey import BookingIntent, CN, Journey, passenger_names
from railpilot.models import Candidate
from railpilot.rail.orders import parse_order
from railpilot.store.journeys import JourneyStore


@pytest.fixture
def train():
    day = (datetime.now(CN).date() + timedelta(days=1)).isoformat()
    return Candidate(day, "G1", "北京南", "上海虹桥", "二等座", depart_time="08:00",
                     arrive_time="12:30", duration="04:30", available="2")


def official_order(intent, sequence="A1234567890", state="待支付", deadline=None):
    c = intent.candidate
    return parse_order({
        "sequence_no": sequence, "order_status_name": state, "order_date": intent.started,
        "pay_limit_time": deadline,
        "tickets": [{"stationTrainDTO": {"station_train_code": c.train_no,
                    "from_station_name": c.from_station, "to_station_name": c.to_station},
                    "start_train_date_page": c.date, "seat_type_name": c.seat,
                    "passengerDTO": {"passenger_name": p}} for p in intent.passengers],
    }, payment_list=True)


def test_acceptable_seats_and_same_day_arrival(train):
    trip = Journey(passengers=passenger_names("乘客甲，乘客乙"), latest="12:30")
    trip.add(train)
    trip.add(train)
    assert trip.choices == [train]
    with pytest.raises(ValueError, match="一致"):
        trip.add(replace(train, to_station="上海"))
    with pytest.raises(ValueError, match="不同"):
        passenger_names("乘客甲，乘客甲")
    assert trip.availability(train, train)[0]
    for changes in [dict(available="候补"), dict(available="1"), dict(arrive_time="12:31"),
                    dict(depart_time="23:30", arrive_time="04:00"), dict(seat="无座")]:
        assert not trip.availability(train, replace(train, **changes))[0]


def test_order_must_be_new_unique_unpaid_and_exact(train):
    initial = BookingIntent.prepare(train, ["乘客乙", "乘客甲"], [])
    older = official_order(initial)
    intent = BookingIntent.prepare(train, list(initial.passengers), [older])
    order = official_order(intent, sequence="A1234567891")
    assert intent.match([older, order]) == order
    assert intent.match([order, official_order(intent, sequence="A1234567892")]) is None
    earlier = (datetime.fromisoformat(intent.started) - timedelta(seconds=1)).isoformat()
    for wrong in [older, replace(order, placed_at=earlier), replace(order, placed_at=None),
                  replace(order, tickets=order.tickets[:1]), replace(order, tickets=order.tickets*2),
                  official_order(intent, state="已完成"), official_order(intent, state="待人工处理")]:
        assert intent.match([wrong]) is None
    for field, value in dict(train_no="G2", date="2000-01-01", from_station="北京", to_station="上海",
                             seat="一等座", passenger="其他人").items():
        tickets = (replace(order.tickets[0], **{field: value}), order.tickets[1])
        assert intent.match([replace(order, tickets=tickets)]) is None


def test_single_claim_persists_across_restart_and_concurrent_windows(tmp_path, train):
    path = tmp_path / "journeys.db"
    store = JourneyStore(path)
    trip = Journey([train], ["测试旅客甲"])
    assert store.save(trip, 0) == 1
    assert JourneyStore(path).load() == (1, trip)
    with pytest.raises(ValueError, match="重新载入"):
        JourneyStore(path).save(trip, 0)
    intent = BookingIntent.prepare(train, trip.passengers, [])
    token = store.begin(intent)
    with pytest.raises(ValueError, match="待核实"):
        store.begin(intent)
    def attempt(_):
        try:
            JourneyStore(path).claim(token)
        except ValueError:
            return False
        return True
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(attempt, range(3))) == 1
    store.finish("unrelated")
    assert store.current() == (token, intent)
    store.finish(token)
    assert store.current() is None
    new_token = store.begin(intent)
    with pytest.raises(ValueError, match="发起过"):
        JourneyStore(path).claim(new_token)
    data = path.read_bytes()
    assert all(trip.passengers[0].encode(encoding) not in data for encoding in ("utf-8", "utf-16-le"))


def test_manual_booking_recovers_after_network_error_and_restart(tmp_path, train):
    path = tmp_path / "journeys.db"
    session = Mock()
    browser = session.visible_browser.return_value
    session.existing_browser.return_value = browser
    browser.read_orders.return_value = []
    service = BookingService(session, JourneyStore(path))
    service.manual(train, Journey([train], ["乘客甲"]))
    browser.open_booking.assert_called_once_with(train)
    token, intent = service.store.current()
    restarted = BookingService(session, JourneyStore(path))
    browser.read_orders.side_effect = ConnectionError()
    with pytest.raises(ConnectionError):
        restarted.reconcile()
    assert restarted.store.current() == (token, intent)
    browser.read_orders.side_effect = None
    assert "待核实" in restarted.reconcile()[0]
    future = (datetime.now(CN) + timedelta(minutes=10)).isoformat(timespec="seconds")
    browser.read_orders.return_value = [official_order(intent, deadline=future)]
    message, deadline = restarted.reconcile()
    assert "已占座" in message and deadline == future
    past = (datetime.now(CN) - timedelta(minutes=5)).isoformat()
    browser.read_orders.return_value = [official_order(intent, deadline=past)]
    assert "期限已过" in restarted.reconcile()[0]


def test_watch_uses_first_available_choice_once(tmp_path, train, monkeypatch):
    session = Mock()
    session.visible_browser.return_value.read_orders.return_value = []
    service = BookingService(session, JourneyStore(tmp_path / "journeys.db"))
    full = replace(train, available="候补")
    next_choice = replace(train, train_no="G2")
    later_choice = replace(train, train_no="G3")
    expired = replace(train, date="2000-01-01")
    trip = Journey([expired, full, next_choice, later_choice], ["乘客甲", "乘客乙"])
    session.query.return_value = ([full, later_choice, next_choice], [])
    submissions = []
    def submit(browser, intent, claim, cancelled):
        assert not cancelled()
        claim()
        submissions.append(intent.candidate)
    monkeypatch.setattr("railpilot.core.booking.submit_once", submit)
    service.watch(trip, 1, Event(), lambda _: None)
    assert submissions == [next_choice]
    session.query.assert_called_once()
    with pytest.raises(ValueError, match="待核实"):
        service.watch(trip, 1, Event(), lambda _: None)
    assert submissions == [next_choice]


def test_stopping_wait_creates_no_order(tmp_path, train):
    session = Mock()
    session.query.return_value = ([replace(train, available="候补")], [])
    service = BookingService(session, JourneyStore(tmp_path / "journeys.db"))
    stop = Event()
    assert "已停止" in service.watch(Journey([train], ["乘客甲"]), 1, stop, lambda _: stop.set())
    assert service.store.current() is None
    session.visible_browser.return_value.read_orders.assert_not_called()
