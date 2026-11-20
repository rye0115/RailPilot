from datetime import date
from unittest.mock import Mock

import pytest

from railpilot.models import Candidate, Order
from railpilot.rail.adapter import DryRunAdapter, PlaywrightAdapter, RailError, parse_query_rows, validate_presale_date


def test_dry_run_adapter():
    candidate = Candidate(
        date="2027-02-10",
        train_no="G1",
        from_station="北京南",
        to_station="上海虹桥",
        seat="二等座",
    )
    adapter = DryRunAdapter(candidates=[candidate], orders=[Order("E1****789", "unpaid")])
    adapter.open()
    assert adapter.is_logged_in()
    assert adapter.query("2027-02-10", "北京南", "上海虹桥") == [candidate]
    result = adapter.submit(candidate)
    assert result.status == "manual"
    assert result.ok
    assert adapter.read_orders("unpaid")[0].masked_no == "E1****789"
    adapter.close()


def _row(**overrides):
    parts = [""] * 40
    parts[3] = "G1"
    parts[6] = "VNP"
    parts[7] = "AOH"
    parts[8] = "06:30"
    parts[9] = "11:24"
    parts[10] = "04:54"
    parts[11] = "Y"
    parts[29] = "有"   # 硬座
    parts[30] = "有"   # 二等座
    parts[31] = "5"    # 一等座
    parts[32] = "无"   # 商务座
    for key, value in overrides.items():
        parts[int(key)] = value
    return "|".join(parts)


def test_parse_query_rows_seat_indexes():
    rows = [_row()]
    result = parse_query_rows(rows, "2026-11-25", "北京南", "上海虹桥", "VNP", "AOH")
    seats = {c.seat: c.available for c in result}
    assert seats["二等座"] == "有"
    assert seats["一等座"] == "5"
    assert seats["硬座"] == "有"
    assert "商务座" not in seats


def test_parse_query_rows_filters_station_and_unbookable():
    rows = [_row(**{"6": "BJP"}), _row(**{"11": "N"})]
    assert parse_query_rows(rows, "2026-11-25", "北京南", "上海虹桥", "VNP", "AOH") == []


def test_validate_presale_date():
    today = date(2026, 11, 14)
    validate_presale_date("2026-11-14", today=today)
    validate_presale_date("2026-11-28", today=today)
    with pytest.raises(RailError):
        validate_presale_date("2026-11-29", today=today)
    with pytest.raises(RailError):
        validate_presale_date("2026-11-13", today=today)


@pytest.mark.parametrize("payload", [{"status": False, "data": {}}, {"status": True, "data": {}}])
def test_missing_query_results_raise(payload, monkeypatch):
    adapter = PlaywrightAdapter()
    adapter._context = Mock()
    adapter._page = Mock(url="https://kyfw.12306.cn/otn/leftTicket/init")
    adapter._page.is_closed.return_value = False
    adapter._station_map = {"A": "AAA", "B": "BBB"}
    monkeypatch.setattr("railpilot.rail.adapter.validate_presale_date", lambda _: None)
    response = adapter._context.request.get.return_value
    response.ok = True
    response.json.return_value = payload
    with pytest.raises(RailError):
        adapter.query("2027-02-10", "A", "B")
    response.json.return_value = {"status": True, "data": {"result": []}}
    assert adapter.query("2027-02-10", "A", "B") == []


def test_orders_require_explicit_list_and_known_filtered_status(monkeypatch):
    adapter = PlaywrightAdapter()
    adapter._context = Mock()
    monkeypatch.setattr(adapter, "is_logged_in", lambda: True)
    response = adapter._context.request.post.return_value
    response.ok = True
    response.headers = {"content-type": "application/json"}
    for data in ({}, {"orderDBList": None}, {"orderDBList": [{}]}):
        response.json.return_value = {"status": True, "data": data}
        with pytest.raises(RailError):
            adapter.read_orders("unpaid")
    response.json.return_value = {"status": True, "data": {"orderDBList": []}}
    assert adapter.read_orders("unpaid") == []
    response.json.return_value = {"status": True, "httpstatus": 200, "messages": [], "data": None}
    assert adapter.read_orders() == []
    response.json.return_value = {"status": True, "data": {"to_page": "cache", "orderDBList": []}}
    with pytest.raises(RailError, match="排队"):
        adapter.read_orders()


def test_closed_work_tab_preserves_other_tabs():
    adapter = PlaywrightAdapter()
    adapter._browser = Mock()
    booking = Mock()
    adapter._context = Mock(pages=[booking])
    adapter._page = Mock()
    adapter._page.is_closed.return_value = True

    assert adapter.is_open()
    work = adapter._work_page()
    assert work is not booking
    assert work is adapter._context.new_page.return_value
    work.is_closed.return_value = False
    assert adapter._work_page() is work
    adapter._context.new_page.assert_called_once()
    booking.goto.assert_not_called()
    adapter._context.pages = []
    assert not adapter.is_open()
