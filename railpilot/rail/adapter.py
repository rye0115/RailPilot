"""Rail adapters.

`DryRunAdapter` is deterministic and used by tests/CLI dry-runs.
`PlaywrightAdapter` opens the official pages in a real browser and keeps
cookies in memory. Live order submission is intentionally not enabled in v0.1:
the order flow needs field testing against the real page before it can be
trusted.
"""

from __future__ import annotations

import re
import time
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Iterable

from ..models import Candidate, Order, SubmitResult, WaitlistPlan
from ..security.allowlist import assert_allowed, install_route_guard
from ..security.redact import mask_order_no
from . import selectors

CN = timezone(timedelta(hours=8))


class RailError(RuntimeError):
    pass


def validate_presale_date(date_str: str, today: date_cls | None = None) -> None:
    try:
        target = date_cls.fromisoformat(date_str)
    except ValueError as exc:
        raise RailError("date must be YYYY-MM-DD") from exc
    current = today or datetime.now(CN).date()
    delta = (target - current).days
    if delta < 0:
        raise RailError("date is in the past")
    if delta > 14:
        raise RailError("date is outside the 15-day presale window")


def parse_query_rows(
    rows: Iterable[object],
    date: str,
    from_station: str,
    to_station: str,
    from_code: str,
    to_code: str,
) -> list[Candidate]:
    """Parse official queryG pipe rows into candidates.

    Index map verified against 2026-11 responses:
    30=二等座, 31=一等座, 32=商务座, 29=硬座, 28=硬卧, 26=无座.
    """
    result: list[Candidate] = []
    for row in rows:
        parts = str(row).split("|")
        if len(parts) < 34:
            continue
        if parts[6] != from_code or parts[7] != to_code:
            continue
        train_no = parts[3]
        depart = parts[8]
        arrive = parts[9]
        duration = parts[10]
        if parts[11] != "Y":
            continue
        for seat, index in selectors.SEAT_INDEX.items():
            available = parts[index] if index < len(parts) else ""
            if available in ("", "无", "--", "*"):
                continue
            result.append(
                Candidate(
                    date=date,
                    train_no=train_no,
                    from_station=from_station,
                    to_station=to_station,
                    seat=seat,
                    priority=100,
                    depart_time=depart,
                    arrive_time=arrive,
                    duration=duration,
                    available=available,
                )
            )
    return result


class DryRunAdapter:
    def __init__(
        self,
        candidates: Iterable[Candidate] = (),
        orders: Iterable[Order] = (),
        submit_status: str = "manual",
    ) -> None:
        self.candidates = list(candidates)
        self.orders = list(orders)
        self.submit_status = submit_status
        self.submitted: list[Candidate] = []

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def is_logged_in(self) -> bool:
        return True

    def query(self, date: str, from_station: str, to_station: str) -> list[Candidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.date == date
            and candidate.from_station == from_station
            and candidate.to_station == to_station
        ]

    def submit(self, candidate: Candidate) -> SubmitResult:
        self.submitted.append(candidate)
        return SubmitResult(
            status=self.submit_status,  # type: ignore[arg-type]
            message="dry-run: no real order submitted",
        )

    def read_orders(self, status: str | None = None) -> list[Order]:
        if status is None:
            return list(self.orders)
        return [order for order in self.orders if order.status == status]

    def open_payment(self, order: Order) -> None:
        return None

    def open_waitlist(self, plan: WaitlistPlan) -> None:
        return None


class PlaywrightAdapter:
    def __init__(self, *, headless: bool = False, timeout_ms: int = 30_000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._station_map: dict[str, str] | None = None
        self.blocked_hosts: list[str] = []

    def __enter__(self) -> "PlaywrightAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on install
            raise RailError("playwright is not installed") from exc
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel="msedge", headless=self.headless)
        except Exception:
            self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 900},
        )
        install_route_guard(self._context, self._on_block)
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def _on_block(self, host: str) -> None:
        if host not in self.blocked_hosts:
            self.blocked_hosts.append(host)

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None

    def login_qr(self, timeout_s: int = 180, poll_s: float = 2.0) -> bool:
        if self._page is None:
            raise RailError("adapter is not open")
        self._page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
        try:
            self._page.locator("a", has_text="扫码登录").first.click(timeout=3000)
        except Exception:
            pass
        try:
            self._page.bring_to_front()
            self._page.evaluate("window.focus()")
        except Exception:
            pass
        print("请在这个新打开的浏览器窗口里用 12306 App 扫码；普通 Edge 的登录态不会共享。")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_logged_in():
                return True
            self._page.wait_for_timeout(int(poll_s * 1000))
        return False

    def is_logged_in(self) -> bool:
        if self._context is None:
            return False
        try:
            cookies = self._context.cookies(selectors.BASE)
        except Exception:
            return False
        names = {cookie.get("name") for cookie in cookies if cookie.get("value")}
        return bool({"tk", "uamtk"} & names)

    def _station_code(self, name: str) -> str:
        if self._station_map is None:
            assert_allowed(selectors.STATION_JS_URL)
            response = self._context.request.get(selectors.STATION_JS_URL)  # type: ignore[union-attr]
            if not response.ok:
                raise RailError(f"station list HTTP {response.status}")
            text = response.text()
            mapping: dict[str, str] = {}
            for _code, station, telecode in re.findall(
                r"@([a-z]+)\|([^|]+)\|([A-Z]+)\|", text
            ):
                mapping[station] = telecode
            self._station_map = mapping
        code = self._station_map.get(name.strip())
        if not code:
            raise RailError(f"unknown station: {name}")
        return code

    def query(self, date: str, from_station: str, to_station: str) -> list[Candidate]:
        if self._context is None or self._page is None:
            raise RailError("adapter is not open")
        validate_presale_date(date)
        if "leftTicket" not in (self._page.url or ""):
            self._page.goto(selectors.LEFT_TICKET_URL, wait_until="domcontentloaded")
        from_code = self._station_code(from_station)
        to_code = self._station_code(to_station)
        url = (
            f"{selectors.QUERY_URL}?leftTicketDTO.train_date={date}"
            f"&leftTicketDTO.from_station={from_code}"
            f"&leftTicketDTO.to_station={to_code}&purpose_codes=ADULT"
        )
        assert_allowed(url)
        response = self._context.request.get(
            url,
            headers={"Referer": selectors.LEFT_TICKET_URL},
        )
        if not response.ok:
            raise RailError(f"query HTTP {response.status}")
        try:
            payload = response.json()
        except Exception as exc:
            content_type = response.headers.get("content-type", "")
            hint = "date may be outside the 15-day presale window" if "text/html" in content_type else "login/risk page?"
            raise RailError(f"query returned non-JSON ({hint})") from exc
        rows = payload.get("data", {}).get("result", []) or []
        return parse_query_rows(rows, date, from_station, to_station, from_code, to_code)

    def submit(self, candidate: Candidate) -> SubmitResult:
        _ = candidate
        return SubmitResult(
            status="manual",
            message="live submit is not enabled in v0.1; use the official page after field testing",
        )

    def read_orders(self, status: str | None = None) -> list[Order]:
        if self._context is None:
            raise RailError("adapter is not open")
        if not self.is_logged_in():
            raise RailError("not logged in; refusing to guess order state")
        assert_allowed(selectors.ORDER_URL)
        response = self._context.request.post(
            selectors.ORDER_URL,
            form={"_json_att": ""},
            headers={"Referer": f"{selectors.BASE}/otn/queryOrder/initNoComplete"},
        )
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            raise RailError("order endpoint returned HTML; login/risk page")
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RailError("order endpoint returned invalid JSON") from exc
        if not payload.get("status", False):
            raise RailError("order query failed")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise RailError("order endpoint returned unexpected data shape")
        rows = data.get("orderDBList") or []
        result: list[Order] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_no = str(row.get("order_no") or row.get("orderId") or "")
            raw_status = str(row.get("status") or row.get("order_status") or "")
            mapped = "unknown"
            if raw_status in ("未支付", "待支付", "unpaid"):
                mapped = "unpaid"
            elif raw_status in ("已支付", "paid"):
                mapped = "paid"
            elif raw_status in ("已取消", "cancelled"):
                mapped = "cancelled"
            if status is not None and mapped != status:
                continue
            result.append(
                Order(
                    masked_no=mask_order_no(raw_no),
                    status=mapped,  # type: ignore[arg-type]
                    amount=row.get("ticket_price") or row.get("amount"),
                    pay_deadline=row.get("pay_limit_time") or row.get("pay_deadline"),
                    train_no=row.get("train_code") or row.get("train_no"),
                    date=row.get("train_date") or row.get("date"),
                )
            )
        return result

    def open_payment(self, order: Order) -> None:
        if self._page is None:
            raise RailError("adapter is not open")
        self._page.goto(f"{selectors.BASE}/otn/queryOrder/initNoComplete", wait_until="domcontentloaded")

    def open_waitlist(self, plan: WaitlistPlan) -> None:
        if self._page is None:
            raise RailError("adapter is not open")
        self._page.goto(selectors.LEFT_TICKET_URL, wait_until="domcontentloaded")
