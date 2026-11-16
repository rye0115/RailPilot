from datetime import date

import pytest

from railpilot.models import Candidate, Order
from railpilot.rail.adapter import DryRunAdapter, RailError, parse_query_rows, validate_presale_date


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
