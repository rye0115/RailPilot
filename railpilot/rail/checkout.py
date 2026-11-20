"""Single-attempt checkout through the official visible purchase controls."""

import re
from collections.abc import Callable
from urllib.parse import urlsplit

from ..core.journey import BookingIntent
from .adapter import PlaywrightAdapter, RailError


def _check_form_url(page) -> None:
    url = urlsplit(page.url)
    if (url.scheme, url.netloc, url.path.rstrip("/")) != ("https", "kyfw.12306.cn", "/otn/confirmPassenger/initDc"):
        raise RailError("未到达官方单程确认页面，请在浏览器完成登录或核验。")


def _matches_route(text: str, intent: BookingIntent) -> bool:
    c = intent.candidate
    route = re.compile(rf"(?<![\u4e00-\u9fff]){re.escape(c.from_station)}站?(?![\u4e00-\u9fff]).*?"
                       rf"(?<![\u4e00-\u9fff]){re.escape(c.to_station)}站?(?![\u4e00-\u9fff])", re.S)
    service = re.search(rf"(?<![A-Z0-9]){re.escape(c.train_no)}(?![A-Z0-9])", text)
    return c.date in text and service is not None and route.search(text) is not None


def _seat_option(select, seat: str) -> str:
    options = select.locator("option:enabled").evaluate_all(
        "xs => xs.map(x => [x.value, x.textContent.trim()])"
    )
    matches = [value for value, label in options if re.fullmatch(re.escape(seat) + r"(?:\s*[（(].*[）)])?", label)]
    if len(matches) != 1:
        raise RailError(f"官方表单没有唯一可选的{seat}，请手动核对。")
    return matches[0]


def submit_once(browser: PlaywrightAdapter, intent: BookingIntent, claim: Callable[[], None],
                cancelled: Callable[[], bool]) -> None:
    """Claim before the first submission click, then inspect the railway's final confirmation."""
    c = intent.candidate
    page = browser.booking_results_page(c)
    train = page.locator("#queryLeftTable tr").filter(has=page.get_by_text(c.train_no, exact=True))
    train.first.wait_for(state="visible", timeout=8000)
    if train.count() != 1:
        raise RailError("当前车次未能唯一匹配，请在官方页面核对。")
    stations = train.locator(".cdz strong")
    if not stations.count():
        stations = train.locator(".start-s, .end-s")
    if [name.strip() for name in stations.all_inner_texts()] != [c.from_station, c.to_station]:
        raise RailError("当前车次或区间与行程不一致。")
    if cancelled():
        raise RailError("已停止等待。")
    train.get_by_role("link", name="预订", exact=True).click(timeout=5000)
    page.wait_for_url("**/otn/confirmPassenger/initDc*", timeout=10000)
    _check_form_url(page)
    heading = page.locator("#ticket_tit_id")
    if not _matches_route(heading.inner_text(timeout=5000), intent):
        raise RailError("官方订单表单的日期、车次或车站与选择不符。")
    people = page.locator("#normal_passenger_id")
    for name in intent.passengers:
        label = re.compile(rf"^\s*{re.escape(name)}\s*(?:[（(]成人[）)])?\s*$")
        checkbox = people.get_by_role("checkbox", name=label)
        if checkbox.count() != 1:
            raise RailError("乘车人姓名未能唯一匹配，请在官方页面核对。")
        checkbox.check(timeout=3000)
    rows = page.locator("#ticketInfo_id tr").filter(has=page.locator("select[id^='seatType_']"))
    if rows.count() != len(intent.passengers):
        raise RailError("官方表单的乘车人数与所选人数不符。")
    checked_names = []
    for row in rows.all():
        checked_names.append(row.locator("input[id^='passenger_name_']").input_value(timeout=3000).strip())
        seat = row.locator("select[id^='seatType_']")
        seat.select_option(value=_seat_option(seat, c.seat), timeout=3000)
        ticket = row.locator("select[id^='ticketType_'] option:checked").inner_text(timeout=3000).strip()
        if ticket != "成人票":
            raise RailError("本次确认仅适用于成人票，其他票种请在官方页面办理。")
    if sorted(checked_names) != sorted(intent.passengers):
        raise RailError("官方表单的乘车人与行程不符。")
    _check_form_url(page)
    if cancelled():
        raise RailError("已停止等待。")
    claim()
    page.locator("#submitOrder_id").click(timeout=3000)
    final = page.locator("#checkOrderInfo")
    final.wait_for(state="visible", timeout=7000)
    # Inspect the visible recap only. Never log its text (it can include ID numbers).
    summary = final.inner_text(timeout=3000)
    if not _matches_route(summary, intent) or any(word in summary for word in ("自动分配", "替换", "改为")):
        raise RailError("请人工核对官方最终确认页，客户端已停止提交。")
    table = final.locator("table").filter(has=page.get_by_text(intent.passengers[0], exact=True))
    details = table.locator("tr").filter(has=page.locator("td"))
    if table.count() != 1 or details.count() != len(intent.passengers) or any(
        details.filter(has=page.get_by_text(name, exact=True)).filter(has=page.get_by_text(c.seat, exact=True)).count() != 1
        for name in intent.passengers
    ):
        raise RailError("最终确认页的乘车人或席别未完全匹配，请在官方页面核对。")
    _check_form_url(page)
    if cancelled():
        raise RailError("已停止，请核对官方订单。")
    final.locator("#qr_submit_id").click(timeout=3000)
