"""Coordinate availability checks, human handoff and one approved submission."""

from datetime import datetime
from threading import Event
from time import monotonic

from .journey import BookingIntent, CN, Journey
from ..models import Candidate
from ..rail.adapter import validate_presale_date
from ..rail.checkout import submit_once
from ..store.journeys import JourneyStore


class BookingService:
    def __init__(self, session, store: JourneyStore):
        self.session = session
        self.store = store

    def inventory(self, trip: Journey, stop: Event) -> dict[str, Candidate]:
        today = datetime.now(CN).date().isoformat()
        routes = dict.fromkeys((c.date, c.from_station, c.to_station) for c in trip.choices if c.date >= today)
        if not routes:
            raise ValueError("当前行程没有可查询的出发日期，请从查票页添加新的车次。")
        live = {}
        for index, route in enumerate(routes):
            validate_presale_date(route[0])
            if stop.is_set() or (index and stop.wait(5)):
                raise ValueError("已停止查询。")
            records, _ = self.session.query(*route, headless=True)
            live.update((c.key, c) for c in records)
        return live

    def start(self, candidate: Candidate, trip: Journey, automatic: bool):
        if self.store.current():
            raise ValueError("还有一笔待核实的行程，请先核对订单。")
        browser = self.session.visible_browser()
        orders = browser.read_orders()
        if automatic and any(o.status in ("unpaid", "unknown") for o in orders):
            raise ValueError("账号有待支付或状态未明的订单，请先到 12306 核对。")
        intent = BookingIntent.prepare(candidate, trip.passengers, orders)
        token = self.store.begin(intent)
        return browser, token, intent

    def manual(self, candidate: Candidate, trip: Journey) -> str:
        validate_presale_date(candidate.date)
        browser, _, _ = self.start(candidate, trip, False)
        found = browser.open_booking(candidate)
        if not found:
            return "已记录原有订单。官方页面尚未找到所选车次，请手动核对。"
        return f"请在 12306 确认 {candidate.train_no} {candidate.seat} 和乘车人。提交后可在本页核对订单。"

    def watch(self, trip: Journey, minutes: int, stop: Event, progress) -> str:
        if not 1 <= minutes <= 180:
            raise ValueError("等待时长须为 1 至 180 分钟。")
        if self.store.current():
            raise ValueError("还有一笔待核实的行程，请先核对。")
        if not self.session.visible_browser().is_logged_in():
            raise ValueError("请先在登录页扫码，再启动单次占座。")
        deadline = monotonic() + minutes * 60
        while monotonic() < deadline and not stop.is_set():
            live = self.inventory(trip, stop)
            if stop.is_set() or monotonic() >= deadline:
                break
            choice = next((c for c in trip.choices if trip.availability(c, live.get(c.key))[0]), None)
            if choice is not None:
                browser, token, intent = self.start(choice, trip, True)
                submit_once(browser, intent, lambda: self.store.claim(token),
                            lambda: stop.is_set() or monotonic() >= deadline)
                return "单次提交已发出，正在核对 12306 的订单结果。"
            progress("本轮没有符合人数和到达时限的余票，30 秒后再查。")
            stop.wait(min(30, max(0, deadline - monotonic())))
        return "已停止等待。" if stop.is_set() else "已到等待截止时间。"

    def reconcile(self) -> tuple[str, str | None]:
        active = self.store.current()
        if not active:
            return "当前没有需要核实的行程。", None
        _, intent = active
        orders = self.session.existing_browser().read_orders()
        if self.store.current() != active:
            return "待核实行程已变更，请重新核对。", None
        match = intent.match(orders)
        if not match:
            return "待核实：尚未找到唯一匹配的新待支付订单。若已付款，请在 12306 查询已支付订单。", None
        c = intent.candidate
        if match.pay_deadline and datetime.fromisoformat(match.pay_deadline) <= datetime.now(CN):
            return "该订单的支付期限已过，请在 12306 确认最新状态。", None
        text = f"已占座，待支付 · {c.date} {c.train_no} {c.seat} · {len(intent.passengers)} 人 · {match.masked_no}"
        return text, match.pay_deadline
