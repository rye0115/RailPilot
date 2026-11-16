"""Network allowlist: official 12306 only."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

ALLOWED_SUFFIXES = (".12306.cn",)
ALLOWED_EXACT = ("12306.cn",)
LOCAL_SCHEMES = ("about:", "data:", "blob:")


class NetworkBlocked(RuntimeError):
    pass


def is_allowed_host(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    return host in ALLOWED_EXACT or any(host.endswith(suffix) for suffix in ALLOWED_SUFFIXES)


def is_allowed_url(url: str) -> bool:
    if not url:
        return False
    if url.startswith(LOCAL_SCHEMES):
        return True
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    return is_allowed_host(parsed.hostname or "")


def assert_allowed(url: str) -> None:
    if not is_allowed_url(url):
        host = urlparse(url).hostname or "<unknown>"
        raise NetworkBlocked(f"blocked host: {host}")


def install_route_guard(context, on_block: Callable[[str], None] | None = None) -> None:
    """Install a Playwright route guard on a BrowserContext.

    The callback receives only the hostname, never the full URL (which may
    contain tokens). Non-12306 requests are aborted.
    """

    def handler(route) -> None:
        url = route.request.url
        if is_allowed_url(url):
            route.continue_()
            return
        host = urlparse(url).hostname or "<unknown>"
        if on_block is not None:
            try:
                on_block(host)
            except Exception:
                pass
        route.abort()

    context.route("**/*", handler)
