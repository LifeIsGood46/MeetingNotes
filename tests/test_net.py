"""SSL context tests: providers must use certifi's bundle when available.

The clean-VM failure ("CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate") came from the system trust store lacking root CAs. Bundling
certifi fixes it; these tests pin the wiring so a refactor can't drop it.
"""

from unittest import mock

import ssl
import urllib.error

from meetingnotes.net import ssl_context, ssl_error_hint


def test_ssl_context_uses_certifi_when_present():
    with mock.patch("certifi.where", return_value="C:/fake/cacert.pem"):
        created = {}

        def fake_create(*, cafile=None):
            created["cafile"] = cafile
            return mock.Mock()

        with mock.patch("ssl.create_default_context", side_effect=fake_create):
            import meetingnotes.net as net
            net._context = None  # reset the cache
            try:
                ssl_context()
            finally:
                net._context = None
        assert created["cafile"] == "C:/fake/cacert.pem"


def test_ssl_context_falls_back_without_certifi():
    with mock.patch.dict("sys.modules", {"certifi": None}):
        import meetingnotes.net as net
        net._context = None
        with mock.patch("ssl.create_default_context") as m:
            try:
                ssl_context()
            finally:
                net._context = None
            m.assert_called_once_with()


def test_ssl_context_is_cached():
    import meetingnotes.net as net
    net._context = None
    try:
        first = ssl_context()
        assert ssl_context() is first
    finally:
        net._context = None


def test_ssl_error_hint_translates_verification_failure():
    e = ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate")
    hint = ssl_error_hint(e)
    assert hint and "verified" in hint
    assert "date" in hint or "root" in hint


def test_ssl_error_hint_unwraps_urlerror():
    inner = ssl.SSLCertVerificationError(1, "certificate verify failed")
    e = urllib.error.URLError(inner)
    assert ssl_error_hint(e) is not None


def test_ssl_error_hint_ignores_other_errors():
    assert ssl_error_hint(OSError("connection refused")) is None
