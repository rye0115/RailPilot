"""Rail adapters.

`DryRunAdapter` is deterministic and used by tests/CLI dry-runs.
`PlaywrightAdapter` opens the official pages in a real browser and keeps
cookies in memory. The desktop's single confirmed booking flow is in checkout.py.
"""

from __future__ import annotations

import re
import time
from datetime import date as date_cls, datetime, timedelta, timezone
from collections.abc import Callable, Iterable
from urllib.parse import urlencode

from ..models import Candidate, Order, SubmitResult, WaitlistPlan
from ..security.allowlist import assert_allowed, install_route_guard
from . import selectors
from .orders import parse_order

CN = timezone(timedelta(hours=8))


class RailError(RuntimeError):
    pass


def _response_rows(payload: object, key: str, label: str) -> list:
    if not isinstance(payload, dict) or payload.get("status") is not True:
        raise RailError(f"{label}失败，请稍后在 12306 核对")
    data = payload.get("data")
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RailError(f"{label}返回的数据不完整，无法确认结果")
    return rows


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

    def open_waitlist(self, plan: WaitlistPlan | None = None) -> None:
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
        try:
            self.open()
        except Exception:
            self.close()
            raise
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
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()

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

    def is_open(self) -> bool:
        return bool(self._browser and self._browser.is_connected() and self._context and self._context.pages)

    def _work_page(self):
        if self._context is None:
            raise RailError("adapter is not open")
        if self._page is None or self._page.is_closed():
            # Other tabs may contain an unfinished booking. Give queries and QR login their own page.
            self._page = self._context.new_page()
        return self._page

    def login_qr(self, timeout_s: int = 180, poll_s: float = 2.0,
                 cancelled: Callable[[], bool] | None = None) -> bool:
        page = self._work_page()
        page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
        try:
            self._page.locator("a", has_text="扫码登录").first.click(timeout=3000)
        except Exception:
            pass
        try:
            self._page.evaluate(
                """() => {
                    const old = document.getElementById('railpilot-login-banner');
                    if (old) old.remove();
                    const el = document.createElement('div');
                    el.id = 'railpilot-login-banner';
                    el.textContent = 'RailPilot 登录窗口：请只在这个窗口扫码，普通 Edge 的登录不会共享';
                    el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
                        + 'background:#0b1218;color:#4fd1c5;border-bottom:2px solid #4fd1c5;'
                        + 'font:14px/1.6 Microsoft YaHei UI,sans-serif;padding:8px 16px;text-align:center;';
                    document.body.prepend(el);
                }"""
            )
            self._page.bring_to_front()
            self._page.evaluate("window.focus()")
        except Exception:
            pass
        deadline = time.monotonic() + timeout_s
        attempt = 0
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                return False
            if attempt % 3 == 0 and self.is_logged_in():
                return True
            attempt += 1
            self._page.wait_for_timeout(int(poll_s * 1000))
        # The scan can complete exactly at the timeout boundary; do one final
        # authoritative check before reporting failure.
        return self.is_logged_in(deep=True)

    def _check_user_in_page(self) -> bool | None:
        if self._page is None:
            return None
        try:
            result = self._page.evaluate(
                """async () => {
                    try {
                        const r = await fetch('/otn/login/checkUser', {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                      'X-Requested-With': 'XMLHttpRequest'},
                            body: '_json_att='
                        });
                        if (!r.ok) return null;
                        const j = await r.json();
                        return j?.data?.flag === true;
                    } catch (e) { return null; }
                }"""
            )
            return bool(result) if result is not None else None
        except Exception:
            return None

    def login_probe(self) -> dict:
        """Return non-secret login diagnostics (cookie names, checkUser flag)."""
        if self._context is None:
            return {"cookies": [], "check_user": None, "url": ""}
        try:
            cookies = self._context.cookies(selectors.BASE)
        except Exception:
            cookies = []
        names = sorted({str(c.get("name")) for c in cookies if c.get("value")})
        check_user = self._check_user_in_page()
        if check_user is None:
            try:
                assert_allowed(selectors.CHECK_USER_URL)
                response = self._context.request.post(
                    selectors.CHECK_USER_URL,
                    form={"_json_att": ""},
                    headers={
                        "Referer": selectors.LEFT_TICKET_URL,
                        "Origin": selectors.BASE,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                if "json" in response.headers.get("content-type", ""):
                    payload = response.json()
                    check_user = (payload.get("data") or {}).get("flag") is True
            except Exception:
                pass
        return {
            "cookies": names,
            "check_user": check_user,
            "url": self._page.url if self._page is not None else "",
        }

    def is_logged_in(self, deep: bool = True) -> bool:
        if self._context is None:
            return False
        if deep:
            return self.login_probe().get("check_user") is True
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
        validate_presale_date(date)
        page = self._work_page()
        if "leftTicket" not in (page.url or ""):
            page.goto(selectors.LEFT_TICKET_URL, wait_until="domcontentloaded")
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
        rows = _response_rows(payload, "result", "余票查询")
        if any(not isinstance(row, str) or len(row.split("|")) < 34 for row in rows):
            raise RailError("余票记录格式异常，无法确认余票")
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
            raise RailError("请先在登录页完成 12306 扫码，再核对订单。")
        assert_allowed(selectors.ORDER_URL)
        response = self._context.request.post(
            selectors.ORDER_URL,
            form={"_json_att": ""},
            headers={"Referer": f"{selectors.BASE}/otn/queryOrder/initNoComplete"},
        )
        content_type = response.headers.get("content-type", "")
        if not response.ok:
            raise RailError(f"订单查询 HTTP {response.status}")
        if "json" not in content_type:
            raise RailError("order endpoint returned HTML; login/risk page")
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RailError("order endpoint returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("status") is True and "data" in payload:
            data = payload["data"]
            if data in (None, "") and not payload.get("messages") and payload.get("httpstatus", 200) == 200:
                return []
            if isinstance(data, dict) and data.get("to_page") == "cache":
                raise RailError("官方订单正在排队或处理中，请稍后核对。")
        rows = _response_rows(payload, "orderDBList", "订单查询")
        payment_list = payload["data"].get("to_page") == "db"
        result: list[Order] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RailError("订单记录格式异常，请在 12306 核对")
            order = parse_order(row, payment_list=payment_list)
            if status is not None and order.status == "unknown":
                raise RailError("订单状态无法识别，请在 12306 核对")
            if status is not None and order.status != status:
                continue
            result.append(order)
        return result

    def booking_results_page(self, candidate: Candidate):
        date, from_station, to_station = candidate.date, candidate.from_station, candidate.to_station
        validate_presale_date(date)
        params = urlencode({
            "linktypeid": "dc",
            "fs": f"{from_station},{self._station_code(from_station)}",
            "ts": f"{to_station},{self._station_code(to_station)}",
            "date": date,
            "flag": "N,N,Y",
        }, safe=",")
        return self._open_official_tab(f"{selectors.LEFT_TICKET_URL}?{params}")

    def open_booking(self, candidate: Candidate) -> bool:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = self.booking_results_page(candidate)
        train = page.locator("#queryLeftTable").get_by_text(candidate.train_no, exact=True).first
        try:
            train.scroll_into_view_if_needed(timeout=5000)
            train.focus(timeout=1000)
        except PlaywrightTimeout:
            return False
        return True

    def _open_official_tab(self, url: str):
        if self._context is None:
            raise RailError("adapter is not open")
        assert_allowed(url)
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.bring_to_front()
            return page
        except Exception:
            page.close()
            raise

    def open_orders(self) -> None:
        self._open_official_tab(f"{selectors.BASE}/otn/queryOrder/initNoComplete")

    def open_payment(self, order: Order) -> None:
        if self._page is None:
            raise RailError("adapter is not open")
        self.open_orders()

    def open_waitlist(self, plan: WaitlistPlan | None = None) -> None:
        self._open_official_tab(selectors.LEFT_TICKET_URL)
