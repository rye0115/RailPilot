"""Browser operations executed on the desktop's single Playwright worker."""

from collections.abc import Callable

from ..rail.adapter import PlaywrightAdapter


class DesktopSession:
    def __init__(self) -> None:
        self._browser: PlaywrightAdapter | None = None

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    @property
    def has_session(self) -> bool:
        return self._browser is not None

    def existing_browser(self) -> PlaywrightAdapter:
        if self._browser is None or not self._browser.is_open():
            self.close()
            raise ValueError("请在登录页扫码后核对订单。")
        return self._browser

    def visible_browser(self) -> PlaywrightAdapter:
        if self._browser is None or not self._browser.is_open():
            self.close()
            browser = PlaywrightAdapter(headless=False)
            try:
                browser.open()
            except Exception:
                browser.close()
                raise
            self._browser = browser
        return self._browser

    def login(self, cancelled: Callable[[], bool]) -> tuple[bool, list[str], dict]:
        browser = self.visible_browser()
        try:
            ok = browser.login_qr(cancelled=cancelled)
            probe = {} if cancelled() else browser.login_probe()
            if probe.get("check_user") is not None:
                ok = probe["check_user"] is True
            return ok, list(browser.blocked_hosts), probe
        except Exception:
            if not browser.is_open():
                self.close()
            raise

    def query(self, date: str, origin: str, destination: str, headless: bool):
        if self._browser is not None and self._browser.is_open():
            return self._browser.query(date, origin, destination), list(self._browser.blocked_hosts)
        self.close()
        with PlaywrightAdapter(headless=headless) as browser:
            return browser.query(date, origin, destination), list(browser.blocked_hosts)
