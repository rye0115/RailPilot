import sys

import pytest

from railpilot.security import dpapi

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def test_roundtrip_and_no_plaintext():
    plain = "railpilot-secret-123"
    cipher = dpapi.protect_text(plain)
    assert cipher.startswith(dpapi.PREFIX)
    assert plain not in cipher
    assert dpapi.unprotect_text(cipher) == plain


def test_refuses_plaintext():
    with pytest.raises(dpapi.DpapiError):
        dpapi.unprotect_text("plaintext")


def test_tampered_blob_fails():
    cipher = dpapi.protect_text("railpilot")
    tampered = cipher[:-2] + ("AA" if not cipher.endswith("AA") else "BB")
    with pytest.raises(dpapi.DpapiError):
        dpapi.unprotect_text(tampered)
