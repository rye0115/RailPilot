"""Exercise the visible purchase controls in Edge with all page requests served locally."""

from unittest.mock import Mock

import pytest
from playwright.sync_api import Error, sync_playwright

from railpilot.core.journey import BookingIntent
from railpilot.models import Candidate
from railpilot.rail.adapter import RailError
from railpilot.rail.checkout import submit_once


LIST_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
FORM_URL = "https://kyfw.12306.cn/otn/confirmPassenger/initDc"
LISTING = f"""<table id="queryLeftTable"><tr><td><span>G1</span>
<div class="cdz"><strong>北京南</strong><strong>上海虹桥</strong></div>
<a href="{FORM_URL}">预订</a></td></tr></table>"""
FORM = """<div id="ticket_tit_id">2027-01-01 G1 北京南站 → 上海虹桥站</div>
<div id="normal_passenger_id"><label><input type="checkbox">乘客甲 (成人)</label></div>
<table id="ticketInfo_id"><tr><td><input id="passenger_name_1" value="乘客甲">
<select id="seatType_1"><option value="1">一等座</option><option value="2">二等座（示例）</option></select>
<select id="ticketType_1"><option>成人票</option></select></td></tr></table>
<button id="submitOrder_id" onclick="window.firstClicks++;document.querySelector('#checkOrderInfo').hidden=false">提交</button>
<div id="checkOrderInfo" hidden>2027-01-01 G1 北京南站 → 上海虹桥站
<table><tr><td>乘客甲</td><td>二等座</td></tr></table>
<button id="qr_submit_id" onclick="window.lastClicks++">确认</button></div>
<script>window.firstClicks=0;window.lastClicks=0</script>"""


@pytest.fixture(scope="module")
def edge():
    with sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(channel="msedge")
        except Error as exc:
            if "not found" in str(exc).lower() or "doesn't exist" in str(exc).lower():
                pytest.skip("Microsoft Edge unavailable")
            raise
        yield browser
        browser.close()


@pytest.mark.parametrize("case, expected", [
    ("match", [1, 1]), ("changed_seat", [1, 0]), ("extra_person", [1, 0]),
    ("wrong_passenger", [0, 0]), ("denied_claim", [0, 0]), ("stopped_at_confirmation", [1, 0]),
])
def test_checkout_action_counts(edge, case, expected):
    form = FORM
    if case == "changed_seat":
        form = form.replace("<td>二等座</td>", "<td>无座</td>")
    if case == "extra_person":
        form = form.replace("<td>二等座</td></tr>", "<td>二等座</td></tr><tr><td>乘客乙</td><td>二等座</td></tr>")
    if case == "wrong_passenger":
        form = form.replace('value="乘客甲"', 'value="乘客乙"')
    claim = Mock()
    if case == "denied_claim":
        claim.side_effect = ValueError("已经提交")
    context = edge.new_context(service_workers="block")
    try:
        pages = {LIST_URL: LISTING, FORM_URL: form}
        def local_page(route):
            if route.request.url in pages:
                route.fulfill(content_type="text/html; charset=utf-8", body=pages[route.request.url])
            else:
                route.abort()
        context.route("**/*", local_page)
        page = context.new_page()
        page.goto(LIST_URL)
        adapter = Mock()
        adapter.booking_results_page.return_value = page
        c = Candidate("2027-01-01", "G1", "北京南", "上海虹桥", "二等座")
        intent = BookingIntent.prepare(c, ["乘客甲"], [])
        def cancel():
            return case == "stopped_at_confirmation" and page.evaluate("window.firstClicks === 1")
        if case == "match":
            submit_once(adapter, intent, claim, cancel)
            assert page.locator("#seatType_1").input_value() == "2"
        else:
            with pytest.raises((RailError, ValueError)):
                submit_once(adapter, intent, claim, cancel)
        assert page.evaluate("[window.firstClicks, window.lastClicks]") == expected
        assert claim.call_count == (0 if case == "wrong_passenger" else 1)
    finally:
        context.close()
