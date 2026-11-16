from railpilot.security.redact import mask_name, mask_order_no, redact_text


def test_redacts_cookie_and_token():
    text = "Cookie: tk=abc123; uamtk=def456\nRAIL_DEVICEID=xyz"
    out = redact_text(text)
    assert "abc123" not in out
    assert "def456" not in out
    assert "xyz" not in out
    assert "<redacted>" in out


def test_redacts_id_phone_order():
    text = "乘客 110101199001011234 手机 13800138000 订单 E123456789"
    out = redact_text(text)
    assert "110101199001011234" not in out
    assert "13800138000" not in out
    assert "E123456789" not in out
    assert "110101********1234" in out
    assert "138****8000" in out


def test_masks_names_and_orders():
    assert mask_name("张三") == "张*"
    assert mask_name("欧阳锋") == "欧**"
    assert mask_name("") == ""
    assert mask_order_no("E123456789").startswith("E1")
    assert mask_order_no("E123456789").endswith("6789")
    assert "234567" not in mask_order_no("E123456789")
