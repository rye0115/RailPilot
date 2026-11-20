from unittest.mock import Mock

from railpilot.gui.session import DesktopSession


def test_login_query_and_orders_share_visible_session(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("railpilot.gui.session.PlaywrightAdapter", factory)
    browser = factory.return_value
    browser.blocked_hosts = []
    browser.login_qr.return_value = True
    browser.login_probe.return_value = {"check_user": True}
    browser.is_open.return_value = True
    browser.query.return_value = []
    session = DesktopSession()
    assert session.login(lambda: False)[0]
    assert session.query("2027-02-10", "A", "B", headless=True) == ([], [])
    session.visible_browser().open_orders()
    factory.assert_called_once_with(headless=False)
    browser.close.assert_not_called()
    session.close()
    session.close()
    browser.close.assert_called_once()
