from railpilot.security.allowlist import NetworkBlocked, assert_allowed, is_allowed_url


def test_allows_official_https_only():
    assert is_allowed_url("https://12306.cn/")
    assert is_allowed_url("https://kyfw.12306.cn/otn/leftTicket/init")
    assert is_allowed_url("https://sub.12306.cn/path")
    assert is_allowed_url("about:blank")
    assert is_allowed_url("data:text/plain,hello")


def test_blocks_http_and_third_party():
    assert not is_allowed_url("http://kyfw.12306.cn/")
    assert not is_allowed_url("https://example.com/")
    assert not is_allowed_url("https://12306.cn.evil.com/")


def test_assert_allowed_raises_without_full_url():
    try:
        assert_allowed("https://example.com/?token=secret")
    except NetworkBlocked as exc:
        assert "example.com" in str(exc)
        assert "secret" not in str(exc)
    else:
        raise AssertionError("expected NetworkBlocked")
